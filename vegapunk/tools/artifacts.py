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
def register_file_artifact(title: str, path: str) -> str:
    """Remember a mutable workspace text file reference, not a content snapshot.

    Args:
        title: Short description of the file's purpose.
        path: Workspace-relative text file; reads report changes since registration.
    """
    return "Artifacts require a persistent session."


@tool
def link_artifact_checkpoint(artifact_id: str, checkpoint_id: str) -> str:
    """Associate session evidence with an existing progress checkpoint.

    Args:
        artifact_id: Artifact ID owned by this session.
        checkpoint_id: Progress checkpoint ID from this same session.
    """
    return "Artifacts require a persistent session."


@tool
def get_tool_observations() -> str:
    """Inspect recent session tool intents/outcomes and result artifact IDs.

    An outcome_unknown intent does not prove whether the action ran. Inspect state
    before retrying it. Completed means the handler returned, not that its action
    succeeded; inspect the stored result and exit status.
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
    from ..execution import tool_observations

    functions = {"register_artifact": artifacts.store_text, "list_artifacts": artifacts.list_artifacts,
                 "read_artifact": artifacts.read, "search_artifact": artifacts.search,
                 "register_file_artifact": artifacts.register_file,
                 "link_artifact_checkpoint": artifacts.link_checkpoint,
                 "get_tool_observations": tool_observations}

    def handler(name: str):
        def invoke(**arguments):
            sid = identity()
            if sid is None:
                return "Artifacts require a persistent session."
            return json.dumps(functions[name](sid, **arguments), ensure_ascii=False)
        return invoke

    return [replace(t, handler=handler(t.name)) if t.name in functions else t for t in tools]
