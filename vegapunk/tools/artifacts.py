"""Session-bound artifact tools; models cannot choose another session's identity."""

import json
from dataclasses import replace
from typing import Callable

from .. import artifacts
from .registry import tool


@tool
def register_artifact(title: str, text: str) -> str:
    """Save an immutable text snapshot associated with this session.

    Args:
        title: Short description of the evidence or deliverable.
        text: Text to save; captures at most 16 MiB and reports completeness.
    """
    return "Artifacts require a persistent session."


@tool
def list_artifacts(offset: int = 0) -> str:
    """List saved artifact metadata for this session.

    Args:
        offset: Pagination offset returned as next_offset by a previous call.
    """
    return "Artifacts require a persistent session."


@tool
def read_artifact(artifact_id: str, cursor: int = 0) -> str:
    """Retrieve up to 12000 characters of a saved snapshot without rerunning actions.

    Args:
        artifact_id: Artifact ID from this session.
        cursor: Unicode character offset, using next_cursor for subsequent pages.
    """
    return "Artifacts require a persistent session."


@tool
def search_artifact(artifact_id: str, query: str) -> str:
    """Find up to twenty literal matches in a saved text snapshot.

    Args:
        artifact_id: Artifact ID from this session.
        query: Literal case-sensitive search text, at most 1000 characters.
    """
    return "Artifacts require a persistent session."


def bind(tools: list, identity: Callable[[], str | None]) -> list:
    """Bind artifact handlers while retaining their registered names and schemas."""
    functions = {"register_artifact": artifacts.store_text, "list_artifacts": artifacts.list_artifacts,
                 "read_artifact": artifacts.read, "search_artifact": artifacts.search}

    def handler(name: str):
        def invoke(**arguments):
            sid = identity()
            if sid is None:
                return "Artifacts require a persistent session."
            return json.dumps(functions[name](sid, **arguments), ensure_ascii=False)
        return invoke

    return [replace(t, handler=handler(t.name)) if t.name in functions else t for t in tools]
