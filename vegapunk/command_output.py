"""Human-readable views for slash commands; domain/tool payloads stay structured."""

from __future__ import annotations

import json


_CONTROL_ESCAPES = {code: f"\\x{code:02x}" for code in (*range(32), *range(127, 160)) if code not in (9, 10)}


def _render(lines: list[str]) -> str:
    # Human views preserve layout, but must not execute stored ANSI/OSC controls.
    return "\n".join(lines).translate(_CONTROL_ESCAPES)


def _value(value: object) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _details(values: dict, indent: str = "  ") -> list[str]:
    lines = []
    for key, value in values.items():
        label = key.replace("_", " ").capitalize()
        if isinstance(value, dict):
            lines.append(f"{indent}{label}:")
            lines.extend(_details(value, indent + "  "))
        elif isinstance(value, list):
            lines.append(f"{indent}{label}:")
            for item in value:
                if isinstance(item, dict):
                    lines.extend(_details(item, indent + "  "))
                else:
                    lines.append(f"{indent}  • {_value(item)}")
        else:
            lines.append(f"{indent}{label}: {_value(value)}")
    return lines


def _one_line(text: str) -> str:
    return " ".join(text.split())


def plan_view(plan: dict) -> str:
    """Show stable IDs, progress and step evidence without retry bookkeeping."""
    steps = plan["steps"]
    completed = sum(step["status"] == "completed" for step in steps)
    lines = [f"Plan: {plan['title']}", f"ID: {plan['id']}",
             f"Revision: {plan['revision']} · {completed}/{len(steps)} completed"]
    for index, step in enumerate(steps, 1):
        lines.extend(["", f"  {index}. [{step['status']}] {step['title']}", f"     ID: {step['id']}"])
        if step.get("dependencies"):
            lines.append("     Depends on: " + ", ".join(str(i + 1) for i in step["dependencies"]))
        if step.get("outcome"):
            lines.append(f"     Outcome: {step['outcome']}")
        if step.get("evidence"):
            lines.append("     Evidence (model-supplied references):")
            lines.extend(f"       • {item}" for item in step["evidence"])
    return _render(lines)


def plan_history_view(page: dict) -> str:
    """Render a scoped history page with copyable navigation."""
    if not page["items"]:
        return "(no plans on this page)"
    lines = ["Plan history:"]
    for item in page["items"]:
        state = "active" if item["active"] else "finished"
        lines.extend([f"  [{state}] {_one_line(item['title'])} · {item['completed']}/{item['steps']} completed · revision {item['revision']}",
                      f"    {item['id']}"])
    lines.append("Inspect: /plan show <id>")
    if page["next_offset"] is not None:
        lines.append(f"Next page: /plan history {page['next_offset']}")
    return _render(lines)


def _artifact_line(info: dict) -> str:
    kind = "file reference" if info["kind"] == "file_reference" else "text snapshot"
    if info["kind"] == "file_reference":
        size = f"baseline {info['size_bytes']:,} bytes"
    else:
        completeness = "complete" if info["complete"] else "incomplete"
        size = f"{info['captured_bytes']:,}/{info['size_bytes']:,} bytes · {completeness}"
    return (f"{_one_line(info['title'])} · {kind} · {size}"
            f"\n    {info['id']}")


def artifact_list_view(page: dict) -> str:
    """Show captured sizes and completeness without expanding text bodies."""
    if not page["items"]:
        return "(no session artifacts)"
    lines = ["Session artifacts:", *("  " + _artifact_line(item) for item in page["items"]),
             "Read: /artifacts read <id> · Search: /artifacts search <id> <query>"]
    if page["next_offset"] is not None:
        lines.append(f"Next page: /artifacts list {page['next_offset']}")
    return _render(lines)


def artifact_read_view(page: dict) -> str:
    """Keep the returned text literal, including Unicode and line breaks."""
    info = page["artifact"]
    lines = [f"Artifact: {info['title']}", f"ID: {info['id']}"]
    if info["kind"] == "file_reference":
        state = "changed since registration" if page["changed"] else "unchanged since registration"
        lines.extend([f"Source: file reference · {info['path']}",
                      f"Baseline: {info['size_bytes']:,} bytes at registration", f"File: {state}"])
    else:
        lines.append(f"Capture: {'complete' if info['complete'] else 'incomplete'} · {info['captured_bytes']:,}/{info['size_bytes']:,} bytes")
    lines.extend(["", page["text"]])
    if page["next_cursor"] is not None:
        lines.extend(["", f"Continue: /artifacts read {info['id']} {page['next_cursor']}"])
    return _render(lines)


def artifact_search_view(matches: list[dict], artifact_id: str) -> str:
    """Show each match's exact location and retrieval command."""
    if not matches:
        return "(no matches)"
    lines = [f"Artifact matches: {artifact_id} (up to 20)"]
    for match in matches:
        lines.extend(["", f"  Line {match['line']} · cursor {match['cursor']}", match["excerpt"],
                      f"  Read: /artifacts read {artifact_id} {match['cursor']}"])
    return _render(lines)


def checkpoints_view(rows: list[dict], *, detail: bool, offset: int = 0) -> str:
    """List compact snapshots, expanding only an explicitly selected checkpoint."""
    if not rows:
        return "(no checkpoints)"
    lines = []
    for row in rows:
        lines.extend([f"Checkpoint: {row['id']}", f"  Reason: {row['reason']}"])
        if row.get("execution"):
            execution = row["execution"]
            if detail:
                lines.extend(_details(execution))
            else:
                if execution.get("run_status"):
                    lines.append(f"  Run: {execution['run_status']}")
                if execution.get("tool_name"):
                    lines.append(f"  Tool: {execution['tool_name']} [{execution.get('tool_status', 'unknown')}]")
            if execution.get("tool_status") == "outcome_unknown":
                lines.append("  Outcome unknown; reconcile before repeating the action.")
        if row.get("plan"):
            plan = row["plan"]
            if detail:
                lines.extend(["", plan_view(plan)])
            else:
                lines.append(f"  Plan: {_one_line(plan['title'])} · revision {plan['revision']}")
        for item in row.get("artifacts", []):
            lines.append("  Evidence: " + _artifact_line(item))
            if detail:
                lines.append(f"  Read: /artifacts read {item['id']}")
        lines.append("")
    if not detail:
        lines.append("Inspect: /checkpoints show <id>")
        if len(rows) == 50:
            lines.append(f"Check next page: /checkpoints list {offset + 50}")
    return _render(lines).rstrip()


def context_view(report: dict) -> str:
    """Label preparation and usage separately, preserving unknown coverage."""
    mode = report.get("mode")
    lines = [f"Context policy: {mode or 'no request prepared yet'}"]
    if mode == "observe":
        lines.append("Observe: model receives full history; proposed reductions are diagnostic only.")
    elif mode == "off":
        lines.append("Off: context projection disabled.")
    elif mode == "bounded":
        lines.append("Bounded: eligible context is reduced; original history remains saved.")
    preparation = {key: value for key, value in report.items() if key not in {"mode", "usage"}}
    if preparation:
        lines.extend(["", "Request preparation:", *_details(preparation)])
    usage = report.get("usage")
    if usage:
        lines.extend(["", "Usage (completed model calls; unknown usage is not zero):"])
        for key in ("scope", "coverage", "cache_semantics"):
            if key in usage:
                lines.append(f"  {usage[key]}")
        for key, label in (("session", "Conversation totals"), ("turn", "Latest turn"),
                           ("latest_preparation_by_actor", "Latest preparation by agent")):
            if key in usage:
                lines.extend(["", label + ":"])
                if key == "latest_preparation_by_actor":
                    for actor, row in usage[key].items():
                        fields = (("failed", "failed"), ("retained_estimated_tokens", "retained estimate"),
                                  ("proposed_estimated_tokens", "proposed estimate"),
                                  ("estimated_input_budget", "budget"), ("estimated_budget_exceeded", "over budget"),
                                  ("artifact_replacements", "artifact replacements"))
                        parts = [f"{label} {_value(row[field])}" for field, label in fields if field in row]
                        lines.append(f"  {actor.capitalize()}: " + " · ".join(parts))
                else:
                    lines.extend(_usage_details(usage[key]))
    return _render(lines)


def _usage_details(usage: dict) -> list[str]:
    groups = (
        (("completed_model_calls", "Model calls"), ("calls_without_usage", "Calls without usage")),
        (("total_input_tokens", "Total input tokens"), ("reported_input_tokens", "Uncached input"),
         ("reported_output_tokens", "Reported output tokens")),
        (("cache_read_input_tokens", "Cache read input"), ("cache_creation_input_tokens", "Cache creation input")),
        (("estimated_input_tokens", "Estimated input"), ("estimated_requests", "Estimated calls"),
         ("estimator_error_fraction", "Estimator error fraction")),
        (("retrieval_requests", "Retrievals"), ("retrieval_result_bytes", "Retrieved bytes")),
        (("preparations", "Preparations"), ("failed_preparations", "Failed preparations"),
         ("summary_cache_hits", "Summary cache hits")),
    )
    lines = []
    for group in groups:
        parts = [f"{label}: {_value(usage[key])}" for key, label in group if key in usage]
        if parts:
            lines.append("  " + " · ".join(parts))
    for actor, row in usage.get("by_actor", {}).items():
        # Actor rows stay compact; totals above retain coverage/cache diagnostics.
        fields = (("completed_model_calls", "calls"), ("total_input_tokens", "input"),
                  ("reported_output_tokens", "output"), ("calls_without_usage", "missing usage"),
                  ("retrieval_requests", "retrievals"), ("summary_cache_hits", "summary cache hits"))
        parts = [f"{label} {_value(row[key])}" for key, label in fields if key in row]
        lines.append(f"  {actor.capitalize()}: " + " · ".join(parts))
    return lines


def observations_view(rows: list[dict]) -> str:
    """Render tool outcomes without presenting handler completion as action success."""
    lines = ["Tool observations (completed means handler returned; inspect results):"]
    for row in rows:
        lines.extend(["", f"  {row['tool']} [{row['status']}]", f"  Observation: {row['id']}",
                      f"  Runtime run: {row['runtime_run_id']} · Call: {row['tool_call_id']}"])
        if row.get("arguments"):
            try:
                arguments = json.loads(row["arguments"])
            except ValueError:
                lines.append("  Arguments: unreadable stored data")
            else:
                lines.append("  Arguments:")
                lines.extend(_details(arguments, "    ") if isinstance(arguments, dict) else [f"    {_value(arguments)}"])
        if row.get("artifact_id"):
            lines.append(f"  Result: /artifacts read {row['artifact_id']}")
        if row["status"] == "outcome_unknown":
            lines.append("  Outcome unknown; reconcile before repeating the action.")
    return _render(lines)


def memory_view(content: str, candidate: dict | None = None) -> str:
    """Separate the proposed fact from its source quote and model confidence."""
    if candidate is None:
        return _render(["Explicitly saved memory (no extraction evidence):", content])
    lines = [f"Memory candidate: {candidate['id']}", f"Status: {candidate['status']}",
             f"Topic: {candidate['topic']}", f"Model confidence: {candidate['confidence']:.2f} (not independent verification)",
             f"Source session: {candidate['session_slug'] or 'unavailable'}", f"Source ID: {candidate['source_id']}",
             "", "Fact:", content, "", "Source quote:", candidate["quote"]]
    if candidate["status"] == "pending":
        lines.extend(["", f"Decide: /memory approve {candidate['id']} · /memory reject {candidate['id']}"])
    return _render(lines)
