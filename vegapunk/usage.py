"""Content-free usage accounting for one live conversation and its joined workers."""

from copy import deepcopy
from threading import Lock
from typing import Literal

from logpose import Event, ToolCall, ToolResult, TurnEnd

Actor = Literal["main", "delegate", "summary"]
_ACTORS = ("main", "delegate", "summary")
_FIELDS = ("completed_model_calls", "calls_without_usage", "reported_input_tokens",
           "reported_output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
           "retrieval_requests", "retrieval_result_bytes", "preparations", "failed_preparations",
           "summary_cache_hits", "estimated_input_tokens", "estimated_requests")
_PREPARATION_FIELDS = {"retained_estimated_tokens", "proposed_estimated_tokens", "system_bytes",
                       "schema_bytes", "retained_message_bytes", "proposed_message_bytes",
                       "retained_tool_result_bytes", "proposed_tool_result_bytes", "memory_bytes",
                       "skills_bytes", "proposed_system_bytes", "estimated_input_budget",
                       "artifact_replacements", "estimated_budget_exceeded"}


def _empty() -> dict[str, dict[str, int]]:
    return {actor: dict.fromkeys(_FIELDS, 0) for actor in _ACTORS}


def _view(scope: dict[str, dict[str, int]]) -> dict:
    def visible(row: dict[str, int]) -> dict:
        result = dict(row)
        total_input = row["reported_input_tokens"] + row["cache_read_input_tokens"] + row["cache_creation_input_tokens"]
        result["total_input_tokens"] = total_input
        result["estimator_error_fraction"] = ((row["estimated_input_tokens"] - total_input) / total_input
             if total_input > 0 and row["estimated_requests"] == row["completed_model_calls"] and not row["calls_without_usage"] else None)
        if not row["estimated_requests"]:
            result["estimated_input_tokens"] = None
        if row["completed_model_calls"] == row["calls_without_usage"]:
            result["total_input_tokens"] = None
            for key in _FIELDS[2:6]:
                result[key] = None
        return result
    totals = {key: sum(row[key] for row in scope.values()) for key in _FIELDS}
    total_view = visible(totals)
    if any(row["estimated_requests"] != row["completed_model_calls"] or row["calls_without_usage"] for row in scope.values()):
        total_view["estimator_error_fraction"] = None
    return {**total_view, "by_actor": {actor: visible(row) for actor, row in scope.items()}}


class UsageLedger:
    """Count each raw TurnEnd once; RunEnd aggregates are deliberately ignored.

    Totals cover the current in-process conversation, with a separate latest-turn
    view. They do not estimate billing for failed attempts lacking usage events.
    Logpose normalizes input_tokens as uncached; cache categories stay visible and
    total_input_tokens sums these normalized categories once, independently of price.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._session, self._turn = _empty(), _empty()
        self._latest: dict[str, dict] = {}

    def clear(self) -> None:
        """Clear on conversation reset/restore, after active work has drained."""
        with self._lock:
            self._session, self._turn = _empty(), _empty()
            self._latest = {}

    def begin_turn(self) -> None:
        """Start the next sequential user turn without losing conversation totals."""
        with self._lock:
            self._turn = _empty()

    def _add(self, actor: Actor, values: dict[str, int]) -> None:
        if actor not in _ACTORS:
            raise ValueError("Unknown usage actor")
        with self._lock:
            for scope in (self._session, self._turn):
                for key, value in values.items():
                    scope[actor][key] += value

    def observe(self, actor: Actor, event: Event) -> None:
        """Record numeric metadata only, before rendering or summary validation."""
        if isinstance(event, TurnEnd):
            usage = event.usage
            fields = dict(zip(_FIELDS[2:6], (usage.input_tokens, usage.output_tokens,
                          usage.cache_read_input_tokens, usage.cache_creation_input_tokens)))
            self._add(actor, {**fields, "completed_model_calls": 1, "calls_without_usage": int(not any(fields.values()))})
        elif isinstance(event, ToolCall) and event.name in {"read_artifact", "search_artifact"}:
            self._add(actor, {"retrieval_requests": 1})
        elif isinstance(event, ToolResult) and event.name in {"read_artifact", "search_artifact"}:
            self._add(actor, {"retrieval_result_bytes": len(event.content.encode("utf-8"))})

    def preparation(self, actor: Actor, report: dict, *, failed: bool = False) -> None:
        """Count cache reuse separately from actual summary-provider calls."""
        self._add(actor, {"preparations": 1, "failed_preparations": int(failed),
                          "summary_cache_hits": int(bool(report.get("summary_cache_hit")))})
        estimate = report.get("proposed_estimated_tokens" if report.get("mode") == "bounded" else "retained_estimated_tokens")
        if not failed and isinstance(estimate, int) and not isinstance(estimate, bool) and estimate >= 0:
            self._add(actor, {"estimated_input_tokens": estimate, "estimated_requests": 1})
        with self._lock:
            self._latest[actor] = {"failed": failed, **{key: value for key, value in report.items()
                                   if key in _PREPARATION_FIELDS and isinstance(value, (int, bool))}}

    def snapshot(self) -> dict:
        """Return detached totals with unavailable provider usage represented as null."""
        with self._lock:
            session, turn = deepcopy(self._session), deepcopy(self._turn)
            latest = deepcopy(self._latest)
        return {"scope": "current in-process conversation; reset on new/restore",
                "coverage": "completed model turns only; failed/partial attempts may have unreported cost",
                "cache_semantics": "Logpose input_tokens is uncached; total_input_tokens sums normalized uncached/read/write categories once",
                "session": _view(session), "turn": _view(turn), "latest_preparation_by_actor": latest}
