"""The approval gate — what stands between the model and a side effect.

logpose calls this once per tool the model asks for, in the order it asked,
before any of them start. That ordering is the whole point: an interactive
approver prompts on stdin, and two prompts at once are unusable.

The gate is *fail-closed by construction*. A guarded tool runs only if an
approver says yes; with no approver wired at all — the scheduler worker, a
script — it is blocked rather than run silently. So an unattended run does its
read-only work and reports back that it couldn't do the rest, which is the
behavior you want from something running while you're asleep.
"""

from __future__ import annotations

import asyncio

from logpose import ToolGateResult, ToolUseBlock

from .approval import Approver
from .tools import GUARDED

# Results fed back when a guarded tool is not allowed to run. Both are worded to
# steer a small model away from immediately re-requesting the same tool.
DENIED = "Denied by the user. Do not retry this tool; consider another approach or ask the user."
NO_GATE = (
    "Blocked: this tool needs approval, but no approval gate is available here. "
    "Do not retry it; tell the user it can't run in this context."
)


class ApprovalCancelled(Exception):
    """Ctrl-C at the approval prompt: the user is cancelling the turn.

    Raised instead of letting ``KeyboardInterrupt`` out of the gate. The gate
    runs on logpose's event-loop thread, and a ``KeyboardInterrupt`` there tears
    the loop down and prints a thread traceback over the CLI's own output — so
    the interrupt is carried back as an ordinary exception and turned into the
    same "(interrupted)" the REPL prints for a Ctrl-C anywhere else.
    """


def denied_with_feedback(feedback: str) -> str:
    """Frame the user's steer as an imperative tool result — the channel this
    model acts on — so declining a call redirects it instead of dead-ending."""
    return (
        f"The user declined this tool and said: {feedback}\n"
        "Do that instead — don't retry the same call."
    )


def make_gate(approver: Approver | None):
    """Build the gate logpose consults before running each requested tool.

    Returns ``None`` to let a call through, or the result to hand the model in
    place of running it.
    """

    async def gate(call: ToolUseBlock) -> str | ToolGateResult | None:
        if call.name not in GUARDED:
            return None  # read-only, or a name that doesn't exist — runs freely
        if approver is None:
            return NO_GATE  # guarded, but nothing here can approve it
        # Approving may block on stdin, so it belongs off the event loop. Still
        # sequential: the await lands in logpose's own one-at-a-time pre-pass.
        try:
            decision = await asyncio.to_thread(approver.approve, call.name, call.input)
        except KeyboardInterrupt as exc:
            raise ApprovalCancelled("cancelled at the approval prompt") from exc
        if decision.allow:
            return None
        if decision.feedback:
            # A decline *with* a steer is not a failure — it's a redirection,
            # and marking it as an error would tell the model something broke.
            return ToolGateResult(denied_with_feedback(decision.feedback), is_error=False)
        return DENIED

    return gate
