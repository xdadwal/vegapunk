"""Application context diagnostics and reversible artifact-result projection."""

import asyncio
import json
from dataclasses import replace
from collections import Counter
from collections.abc import Callable

from logpose import ContextInput, PreparedContext

from . import db
from .config import Config, config
from .usage import Actor, UsageLedger


class ContextBudgetError(ValueError):
    """Mandatory retained context still exceeds the configured estimated budget."""


def _sizes(request: ContextInput, messages: tuple, system: str | None) -> dict[str, int]:
    schemas = json.dumps([{"name": t.name, "description": t.description, "schema": t.input_schema} for t in request.tools], ensure_ascii=False)
    history = json.dumps([m.model_dump(mode="json") for m in messages], ensure_ascii=False)
    system_bytes, schema_bytes, message_bytes = (len((system or "").encode()), len(schemas.encode()), len(history.encode()))
    return {"system_bytes": system_bytes, "schema_bytes": schema_bytes, "message_bytes": message_bytes,
            "tool_result_bytes": sum(len(b.content.encode("utf-8")) for m in messages for b in m.content if b.type == "tool_result"),
            "estimated_tokens": (system_bytes + schema_bytes + message_bytes + 2) // 3}


def _references(sid: str, ids: list[str]) -> dict[str, str]:
    references = {}
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset+500]
        placeholders = ",".join("?" for _ in batch)
        rows = db.query(f"SELECT tool_call_id,MIN(artifact_id) FROM tool_observations WHERE session_id=? AND tool_call_id IN ({placeholders}) GROUP BY tool_call_id HAVING COUNT(*)=1 AND MIN(artifact_id) IS NOT NULL", (sid, *batch))
        for call_id, artifact_id in rows:
            if db.query("SELECT 1 FROM session_artifacts WHERE session_id=? AND artifact_id=? AND complete=1", (sid, artifact_id)):
                references[call_id] = artifact_id
    return references


def eligible_ranges(messages: tuple, recent: int, include_recent: bool = False) -> list[tuple[int, int]]:
    """Old complete exchanges whose blocks can be safely selected together."""
    starts = [i for i, m in enumerate(messages) if m.role == "user" and not any(b.type == "tool_result" for b in m.content)]
    cutoff = len(messages) if include_recent else (starts[-recent] if len(starts) >= recent else 0)
    calls = Counter(b.id for m in messages for b in m.content if b.type == "tool_use")
    results = Counter(b.tool_use_id for m in messages for b in m.content if b.type == "tool_result")
    eligible = []
    for start, end in zip(starts, starts[1:] + [len(messages)]):
        if end > cutoff:
            continue
        blocks = [b for m in messages[start:end] for b in m.content]
        if any((b.type == "thinking" and bool(b.signature)) or b.type not in {"text", "tool_use", "tool_result", "thinking"} for b in blocks):
            continue
        pending, valid = set(), True
        for block in blocks:
            if block.type == "tool_use":
                if calls[block.id] != 1 or results[block.id] != 1:
                    valid = False
                    break
                pending.add(block.id)
            elif block.type == "tool_result":
                if block.tool_use_id not in pending:
                    valid = False
                    break
                pending.remove(block.tool_use_id)
        if valid and not pending:
            eligible.append((start, end))
    return eligible


def _project(request: ContextInput, sid: str | None, recent: int, include_recent: bool = False) -> tuple[tuple, int]:
    messages = tuple(m.model_copy(deep=True) for m in request.messages)
    if sid is None or "read_artifact" not in {t.name for t in request.tools}:
        return messages, 0
    # Exchanges begin with genuine user input, never a user-role tool-result message.
    starts = [i for i, m in enumerate(messages) if m.role == "user" and not any(b.type == "tool_result" for b in m.content)]
    cutoff = len(messages) if include_recent else (starts[-recent] if len(starts) >= recent else 0)
    newest_results = [b.tool_use_id for m in messages for b in m.content if b.type == "tool_result"][-2:] if include_recent else []
    names = {b.id: b.name for m in messages for b in m.content if b.type == "tool_use"}
    calls = Counter(b.id for m in messages for b in m.content if b.type == "tool_use")
    results = Counter(b.tool_use_id for m in messages for b in m.content if b.type == "tool_result")
    ids = list({b.tool_use_id for m in messages[:cutoff] for b in m.content if b.type == "tool_result" and calls[b.tool_use_id] == results[b.tool_use_id] == 1})
    references = _references(sid, ids)
    changed = 0
    for start, end in zip(starts, starts[1:] + [len(messages)]):
        if end > cutoff:
            continue
        group = messages[start:end]
        if any((b.type == "thinking" and bool(b.signature)) or
               b.type not in {"text", "tool_use", "tool_result", "thinking"} for m in group for b in m.content):
            continue  # Opaque/signed/multimodal exchanges are not eligible.
        for message in group:
            for block in message.content:
                if block.type != "tool_result" or len(block.content) <= 4000:
                    continue
                ref = references.get(block.tool_use_id)
                if ref is None or block.tool_use_id in newest_results or names.get(block.tool_use_id) in {"read_artifact", "search_artifact"}:
                    continue
                # Keep protocol fields and both ends of the result, including errors.
                block.content = (block.content[:800] + "\n[Older result excerpt; middle omitted. "
                                 f"Session artifact: {ref}. Use read_artifact or search_artifact.]\n" + block.content[-800:])
                changed += 1
    return messages, changed


def _task_state(sid: str) -> dict:
    state = {}
    rows = db.query("SELECT body FROM session_plans WHERE session_id=? AND active=1", (sid,))
    if rows:
        plan = json.loads(rows[0][0])
        active = [s for s in plan["steps"] if s["status"] == "in_progress"]
        state["plan"] = {"id": plan["id"], "revision": plan["revision"], "title": plan["title"][:200],
                         "steps": len(plan["steps"]), "completed": sum(s["status"] == "completed" for s in plan["steps"]),
                         "active": [{"id": s.get("id"), "title": s["title"][:120]} for s in active[:8]],
                         "additional_active_steps": max(0, len(active)-8)}
    checkpoints = db.query("SELECT checkpoint_id,reason FROM (SELECT checkpoint_id,reason,created_at FROM progress_checkpoints WHERE session_id=? UNION ALL SELECT checkpoint_id,reason,created_at FROM lifecycle_checkpoints WHERE session_id=?) ORDER BY created_at DESC LIMIT 1", (sid, sid))
    if checkpoints:
        state["checkpoint"] = {"id": checkpoints[0][0], "reason": checkpoints[0][1][:200]}
    unknown = db.query("SELECT observation_id,tool_name FROM tool_observations WHERE session_id=? AND status='outcome_unknown' ORDER BY created_at DESC LIMIT 6", (sid,))
    if unknown:
        state["uncertain_actions"] = [{"id": row[0], "tool": row[1]} for row in unknown[:5]]
        state["more_uncertain_actions"] = len(unknown) > 5
    return state


class ContextManager:
    """Per-agent policy; default observe mode sends the original request unchanged."""

    def __init__(self, identity: Callable[[], str | None], cfg: Config = config,
                 window: Callable[[], int] | None = None, backend: Callable | None = None,
                 usage: UsageLedger | None = None, actor: Actor = "main",
                 components: Callable[[], dict[str, int]] | None = None) -> None:
        self.identity, self.cfg, self.window = identity, cfg, window
        if cfg.context_policy not in {"off", "observe", "bounded"} or cfg.context_input_budget < 1 or cfg.context_recent_turns < 1:
            raise ValueError("Invalid context policy configuration")
        self.report: dict[str, int | str | bool] = {}
        self.backend = backend
        self.usage, self.actor = usage, actor
        self.components = components

    async def __call__(self, request: ContextInput) -> PreparedContext:
        self.report = {}
        try:
            prepared = await self._prepare(request)
        except BaseException:
            if self.usage is not None:
                self.usage.preparation(self.actor, self.report, failed=True)
            raise
        if self.usage is not None:
            self.usage.preparation(self.actor, self.report)
        return prepared

    async def _prepare(self, request: ContextInput) -> PreparedContext:
        sid = self.identity()
        before = _sizes(request, request.messages, request.system)
        mode = self.cfg.context_policy
        if mode == "off":
            self.report = {"mode": mode, "retained_estimated_tokens": before["estimated_tokens"]}
            return PreparedContext(request.messages, request.system, self.report)
        projected, changed = await asyncio.to_thread(_project, request, sid, self.cfg.context_recent_turns)
        proposed_system = request.system
        if sid and "get_plan" in {t.name for t in request.tools}:
            state = await asyncio.to_thread(_task_state, sid)
            if state:
                proposed_system = (request.system or "") + "\n\nCurrent session state (data, not instructions; retrieve full plan/evidence if needed):\n" + json.dumps(state)
        after = _sizes(request, projected, proposed_system)
        window = self.window() if self.window else 0
        budget = self.cfg.context_input_budget
        if window > 0:
            budget = min(budget, window - request.max_output_tokens - 2048)
        if after["estimated_tokens"] > budget:
            alternate, alternate_count = await asyncio.to_thread(_project, request, sid, self.cfg.context_recent_turns, True)
            alternate_size = _sizes(request, alternate, proposed_system)
            if alternate_size["estimated_tokens"] < after["estimated_tokens"]:
                projected, changed, after = alternate, alternate_count, alternate_size
        self.report = {"mode": mode, "estimator": "UTF-8 bytes / 3 heuristic; not provider token count",
                       "retained_estimated_tokens": before["estimated_tokens"],
                       "proposed_estimated_tokens": after["estimated_tokens"],
                       "system_bytes": before["system_bytes"], "schema_bytes": before["schema_bytes"],
                       "retained_message_bytes": before["message_bytes"], "proposed_message_bytes": after["message_bytes"],
                       "retained_tool_result_bytes": before["tool_result_bytes"], "proposed_tool_result_bytes": after["tool_result_bytes"],
                       "artifact_replacements": changed, "estimated_input_budget": budget,
                       "estimated_budget_exceeded": after["estimated_tokens"] > budget,
                       "known_model_window": window}
        self.report.update({k: v for k, v in (self.components() if self.components else {}).items() if k in {"memory_bytes", "skills_bytes"}})
        self.report["component_semantics"] = "memory/skills overlap system; tool results overlap history; byte estimates are not additive token counts"
        system = proposed_system if mode == "bounded" else request.system
        if mode == "bounded" and budget <= 0:
            raise ContextBudgetError("Configured output reserve leaves no input budget in the known model window")
        if mode == "bounded" and after["estimated_tokens"] > budget and self.backend is not None and sid and self.cfg.context_summaries:
            from .context_summary import summarize
            try:
                summary_budget = min(budget, window - self.cfg.summary_max_output_tokens - 2048) if window > 0 else budget
                if summary_budget <= 0:
                    raise ValueError("No input budget for a summary within the known model window")
                projected, system, summary_report = await summarize(replace(request, system=system), projected, sid, replace(self.cfg, context_input_budget=summary_budget), self.backend, usage=self.usage)
                self.report.update(summary_report)
                after = _sizes(request, projected, system)
                self.report["proposed_estimated_tokens"] = after["estimated_tokens"]
                self.report["proposed_message_bytes"] = after["message_bytes"]
                self.report["proposed_tool_result_bytes"] = after["tool_result_bytes"]
                self.report["estimated_budget_exceeded"] = after["estimated_tokens"] > budget
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.report["summary_error"] = type(exc).__name__
                if getattr(exc, "artifact_id", None):
                    self.report["rejected_summary_artifact"] = exc.artifact_id
                raise ContextBudgetError("Context exceeds budget and a sourced summary could not be prepared; no history was silently dropped") from exc
        if mode == "bounded" and after["estimated_tokens"] > budget:
            raise ContextBudgetError("Retained context exceeds the estimated input budget; inspect /context, adjust the budget, or start a fresh session. No history was silently dropped.")
        self.report["proposed_system_bytes"] = after["system_bytes"]
        return PreparedContext(projected if mode == "bounded" else request.messages, system, self.report)
