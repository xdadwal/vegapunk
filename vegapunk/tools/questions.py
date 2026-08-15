"""The interactive ``ask_user`` tool.

Its terminal implementation lives in :mod:`vegapunk.questions`. The CLI
installs a handler while it owns stdin; scheduled and other unattended runs get
an explicit unavailable result rather than a blocking prompt.
"""

from __future__ import annotations

from .. import questions
from .registry import tool


@tool
def ask_user(question: str, options: list[str], preferred_option: str) -> str:
    """Ask the user to choose the next step when their decision is necessary.

    Use this whenever the user asks you to ask questions, gather preferences,
    or make a choice before proceeding — including every follow-up question.
    Do not ask a question or print a list of choices in assistant text: the
    user can reliably answer only through this picker. Ask one concise question
    with 1 to 5 distinct options. ``preferred_option`` must be one of those
    exact options and identifies the option you recommend. The picker always
    includes an "Other" choice for a custom typed response. Make this the only
    tool call in the current step so the user can answer before you take a
    dependent action. The user can press Ctrl+N to open an inline note field on
    the highlighted option, then press Enter to submit that option and its note.
    Use the note alongside the option. Esc or Ctrl+C from the custom-answer
    field returns the user to the picker without cancelling the question.
    Once you have enough information, begin the completed response with
    ``FINAL:``; Vegapunk removes that private marker before the user sees the
    recommendation.

    Args:
        question: The concise decision the user needs to make.
        options: Between 1 and 5 distinct, actionable choices.
        preferred_option: The exact option you recommend and preselect.
    """
    return questions.ask_from_tool(question, options, preferred_option)
