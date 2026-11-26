"""One investigation: a logpose run with the read-only tools that answers in a fixed shape.

The model may only cite log lines. Every cited line is checked against the redacted
`container_logs` output the tools returned during this run, for the finding's own container,
and is kept only if it is one whole line of it. Nothing here is stored: the runner stores the
drafts, adding the rule's own fact line to a finding about the signal's subject.
"""

import contextlib
import logging
import re
import tempfile
from collections.abc import Callable, Collection, Sequence
from contextlib import aclosing
from datetime import datetime
from pathlib import Path

from logpose import (
    Agent,
    AuthError,
    MaxIterationsError,
    OutputRefusalError,
    OutputValidationError,
    ProviderError,
    RunEnd,
    RunTimeoutError,
    StructuredOutputUnsupportedError,
    ToolCall,
    ToolResult,
)
from pydantic import BaseModel

from vegapunk.ai.gate import make_gate
from vegapunk.ai.operator import RECALL_TOOL, build_agent, default_make_agent
from vegapunk.ai.prompt import build_investigation_prompt
from vegapunk.ai.redact import redact, scrub_secret
from vegapunk.ai.store import AiConfig
from vegapunk.ai.tools import TOOL_NAMES, build_tools
from vegapunk.config import Settings
from vegapunk.host.docker import DockerHost
from vegapunk.timefmt import z
from vegapunk.watch.findings import (
    MAX_DIAGNOSIS,
    MAX_EVIDENCE_LINES,
    MAX_EVIDENCE_TEXT,
    MAX_FIX,
    MAX_TITLE,
    EvidenceLine,
    FindingDraft,
)
from vegapunk.watch.rules import Signal

log = logging.getLogger(__name__)

MAX_ITERATIONS = 10
MAX_TOOL_CALLS = 20
MAX_TOKENS = 4_096
RUN_TIMEOUT = 300
MAX_FINDINGS = 5
MIN_LINE = 12  # characters, after the timestamp: shorter lines say too little to be evidence
HOST = "host"
LOGS_TOOL = "container_logs"

# Docker's own timestamp at the start of every line (host/docker.py reads with timestamps).
_STAMPED = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})) (.*)$"
)
_OMITTED = "[earlier output omitted:"  # limit_text's note; the line after it is cut

AgentFactory = Callable[..., Agent]


class InvestigationError(Exception):
    """The run did not produce an answer. `code` is one of key_rejected, provider_error,
    timeout, too_many_steps, unsupported, error; `message` is safe to store and show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ReportedFinding(BaseModel):
    """One finding as the model writes it. Lengths are enforced after parsing: strict JSON
    schemas may reject `maxLength`."""

    subject: str
    title: str
    diagnosis: str
    log_lines: list[str]
    suggested_fix: str


class CheckReport(BaseModel):
    findings: list[ReportedFinding]


def investigation_agent_factory(
    *,
    config: AiConfig,
    settings: Settings,
    docker: DockerHost,
    started_at: datetime,
    host: dict | None,
    make_agent: Callable[..., Agent] = default_make_agent,
) -> AgentFactory:
    """A factory `investigate` calls once, with a throwaway database path and what the
    prompt needs to know about this check."""

    def make(db_path: Path, *, focus_summary: str | None, open_subjects: Sequence[str]) -> Agent:
        return build_agent(
            make_agent,
            config=config,
            system=build_investigation_prompt(
                host,
                config.instructions,
                focus_summary=focus_summary,
                open_subjects=open_subjects,
            ),
            tools=build_tools(
                settings.db_path,
                docker,
                interval=settings.capture_interval,
                started_at=started_at,
            ),
            on_tool_call=make_gate((*TOOL_NAMES, RECALL_TOOL), MAX_TOOL_CALLS),
            max_iterations=MAX_ITERATIONS,
            max_tokens=MAX_TOKENS,
            run_timeout=RUN_TIMEOUT,
            db_path=db_path,
            session_ttl=None,
        )

    return make


def _request(focus: Signal | None) -> str:
    if focus is None:
        return "Run the daily check of this host and its containers now."
    return f"Look into what the rule saw (subject `{focus.subject}`) now."


async def investigate(
    agent_factory: AgentFactory,
    *,
    focus: Signal | None,
    open_findings: list[dict],
    subjects: Collection[str],
    secret: str | None = None,
) -> list[FindingDraft]:
    """Run one check and return its verified drafts (an empty list when nothing is wrong).

    `subjects` are the names a finding may be about: the current containers and "host".
    `secret` is the API key, removed from any error text. Raises InvestigationError.
    """
    open_subjects = sorted({str(f["subject"]) for f in open_findings if f.get("subject")})
    reads: dict[str, list[str]] = {}
    output: CheckReport | None = None
    # logpose keeps no session here, but writes run records to its database: keep that
    # file in a directory that is deleted when the run ends.
    with tempfile.TemporaryDirectory(prefix="vegapunk-check-") as scratch:
        agent: Agent | None = None
        try:
            agent = agent_factory(
                Path(scratch) / "logpose.db",
                focus_summary=focus.summary if focus else None,
                open_subjects=open_subjects,
            )
            pending: list[tuple[str, str, dict]] = []  # tool calls awaiting their results
            stream = agent.stream(_request(focus), output_type=CheckReport)
            async with aclosing(stream) as events:
                async for event in events:
                    if isinstance(event, ToolCall):
                        args = event.input if isinstance(event.input, dict) else {}
                        pending.append((event.id, event.name, args))
                    elif isinstance(event, ToolResult):
                        call = _take(pending, event.id)
                        if call is None or event.is_error or not isinstance(event.content, str):
                            continue
                        _, name, args = call
                        target = args.get("name")
                        if name == LOGS_TOOL and isinstance(target, str):
                            reads.setdefault(target, []).append(event.content)
                    elif isinstance(event, RunEnd):
                        output = event.result.output
        except AuthError:
            raise InvestigationError(
                "key_rejected", "Your AI provider rejected the API key."
            ) from None
        except (StructuredOutputUnsupportedError, OutputValidationError, OutputRefusalError):
            raise InvestigationError(
                "unsupported",
                "Your AI provider couldn't give an answer in the shape checks need.",
            ) from None
        except ProviderError as exc:
            detail = scrub_secret(exc.message, secret)
            raise InvestigationError(
                "provider_error", f"The AI provider couldn't answer: {detail}"
            ) from None
        except RunTimeoutError:
            raise InvestigationError("timeout", "The check took too long.") from None
        except MaxIterationsError:
            raise InvestigationError("too_many_steps", "The check needed too many steps.") from None
        except Exception:
            log.exception("A check hit a problem")
            raise InvestigationError("error", "The check hit a problem.") from None
        finally:
            if agent is not None:
                with contextlib.suppress(Exception):
                    await agent.aclose()
    if not isinstance(output, CheckReport):
        raise InvestigationError("error", "The check ended without an answer.")
    return _drafts(output, reads, focus=focus, subjects=subjects)


def _take(pending: list[tuple[str, str, dict]], id: str) -> tuple[str, str, dict] | None:
    """The call a result answers. logpose yields a turn's calls, then its results in the
    same order; ids may repeat across turns (or within one), so the oldest match wins."""
    for i, call in enumerate(pending):
        if call[0] == id:
            return pending.pop(i)
    return pending.pop(0) if pending else None


def _at(stamp: str) -> str | None:
    try:
        return z(datetime.fromisoformat(stamp))
    except ValueError:
        return None


def _index(texts: list[str]) -> dict[str, tuple[str, str | None]]:
    """Every whole, timestamped log line in `texts`, findable with or without its timestamp,
    mapped to (the line without its timestamp, that time)."""
    lines: dict[str, tuple[str, str | None]] = {}
    for text in texts:
        rows = text.split("\n")
        if rows and rows[0].startswith(_OMITTED):
            rows = rows[2:]  # the note, then a line cut at its start
        for row in rows:
            match = _STAMPED.match(row.strip())
            if not match:
                continue  # not a Docker log line: one of the tool's own sentences
            message = match.group(2).strip()
            if len(message) < MIN_LINE:
                continue
            entry = (message, _at(match.group(1)))
            lines.setdefault(row.strip(), entry)
            lines.setdefault(message, entry)
    return lines


def _evidence(cited: list[str], lines: dict[str, tuple[str, str | None]]) -> list[EvidenceLine]:
    kept: list[EvidenceLine] = []
    seen: set[tuple[str, str | None]] = set()
    for raw in cited:
        if not isinstance(raw, str):
            continue
        line = raw.strip()
        if "\n" in line or "\r" in line or line not in lines:
            continue
        message, at = lines[line]
        text = redact(message)[:MAX_EVIDENCE_TEXT]
        if (text, at) in seen:
            continue
        seen.add((text, at))
        kept.append(EvidenceLine("log", text, at))
        if len(kept) == MAX_EVIDENCE_LINES:
            break
    return kept


def _text(value: str, limit: int) -> str:
    return redact(value.strip())[:limit].strip()


def _drafts(
    report: CheckReport,
    reads: dict[str, list[str]],
    *,
    focus: Signal | None,
    subjects: Collection[str],
) -> list[FindingDraft]:
    known = set(subjects)
    drafts: list[FindingDraft] = []
    done: set[str] = set()
    for item in report.findings:
        subject = item.subject.strip()
        if subject not in known or subject in done:
            continue
        title = _text(item.title, MAX_TITLE)
        if not title:
            continue
        # A host finding has no container whose logs its lines must come from.
        lines = _index(reads.get(subject, [])) if subject != HOST else {}
        evidence = _evidence(item.log_lines, lines)
        has_rule_fact = focus is not None and focus.subject == subject
        if not evidence and not has_rule_fact:
            continue  # nothing shows it really happened
        done.add(subject)
        drafts.append(
            FindingDraft(
                subject=subject,
                title=title,
                diagnosis=_text(item.diagnosis, MAX_DIAGNOSIS),
                suggested_fix=_text(item.suggested_fix, MAX_FIX),
                evidence=tuple(evidence),
            )
        )
        if len(drafts) == MAX_FINDINGS:
            break
    return drafts
