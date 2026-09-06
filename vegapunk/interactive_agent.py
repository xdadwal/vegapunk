"""Interactive-only execution rules layered over Logpose's agent loop."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from copy import copy
from dataclasses import replace
from typing import Any

from logpose import (
    Agent,
    Conversation,
    Message,
    RunEnd,
    TextBlock,
    TextDelta,
    ToolResult,
    ToolResultBlock,
    ToolUseBlock,
    TurnEnd,
)

from .gate import DELEGATION_TOOL, QUESTION_TOOL

QUESTION_BATCH_BLOCKED = (
    "Blocked: ask_user must be the only tool call in a step. Read the user's answer, "
    "then decide whether this action is still appropriate."
)
TEXT_QUESTION_CORRECTION = (
    "Continue the active interview through ask_user. Do not ask questions or present choices in "
    "assistant text. If another preference is needed, call ask_user. If the interview is complete, "
    "start the completed recommendation with exactly `FINAL:`; Vegapunk removes that marker before "
    "showing it to the user."
)
TEXT_QUESTION_RETRY_NOTICE = (
    "I couldn't continue the interview through the interactive picker. Please try the request "
    "again."
)
_MAX_TEXT_QUESTION_RETRIES = 2
_FINAL_MARKER = "FINAL:"


def _final_answer(text: str) -> str | None:
    """Return a completed interview response with its private marker removed."""
    if not text.startswith(_FINAL_MARKER):
        return None
    answer = text.removeprefix(_FINAL_MARKER).lstrip()
    return answer or None


class InteractiveAgent(Agent):
    """An Agent that turns a user question into a barrier between tool steps."""

    async def _invoke(
        self, call: ToolUseBlock, queued_at: float, turn_context: Any
    ) -> ToolResultBlock:
        """Keep delegated work joined instead of detaching it at the generic timeout."""
        if call.name != DELEGATION_TOOL:
            return await super()._invoke(call, queued_at, turn_context)

        # Agent._invoke reads tool_timeout from self. A shallow execution view
        # keeps every shared runtime object (tool map, semaphore, observers)
        # while overriding only this call's deadline, without racing sibling
        # tools by mutating the live agent. Stream cancellation still reaches
        # this wrapper, but the shield and cancellation handler drain the role
        # before propagating that cancellation. A delegate therefore cannot
        # outlive even an interrupted primary turn.
        untimed = copy(self)
        untimed.tool_timeout = None
        invocation = asyncio.create_task(Agent._invoke(untimed, call, queued_at, turn_context))
        try:
            return await asyncio.shield(invocation)
        except asyncio.CancelledError:
            try:
                await invocation
            finally:
                raise

    async def _execute(
        self, calls: Sequence[ToolUseBlock], turn_context: Any
    ) -> list[ToolResultBlock]:
        """Prevent a batched action from running before an answer is available."""
        question_call = next((call for call in calls if call.name == QUESTION_TOOL), None)
        if question_call is None or len(calls) == 1 or self.on_tool_call is None:
            return await super()._execute(calls, turn_context)

        # ``make_gate`` substitutes ask_user with the terminal answer. Run only
        # that gate call, then explicitly return a non-error blocked result for
        # every sibling. The next model iteration sees both the answer and the
        # instruction to reconsider its dependent action.
        gated_question = await self._gate([question_call])
        question_result = gated_question.get(0)
        if question_result is None:
            return await super()._execute(calls, turn_context)
        return [
            question_result
            if call is question_call
            else ToolResultBlock(
                tool_use_id=call.id,
                content=QUESTION_BATCH_BLOCKED,
                is_error=False,
            )
            for call in calls
        ]

    def stream(
        self,
        prompt: str | Message | None = None,
        *,
        conversation: Conversation | None = None,
    ):
        """Stream with a narrow retry guard for an active picker interview."""
        return self._stream_interview(prompt, conversation)

    async def _stream_interview(
        self, prompt: str | Message | None, conversation: Conversation | None
    ):
        """Suppress text-only follow-up questions after a picker response.

        Providers make tool choice per completion, so a system instruction alone
        cannot prevent a later text completion from imitating a menu. Once a
        real ``ask_user`` result occurs, buffer each later text turn until its
        end. A completed interview response must begin with ``FINAL:``; any
        other text is treated as an invalid faux-picker, followed by a
        corrective user message and another model iteration. The invalid
        response never reaches the terminal. A bounded retry avoids an infinite
        loop with a noncompliant model while still refusing to show the broken
        faux-picker.
        """
        interview_active = False
        retries = 0
        next_prompt = prompt
        next_conversation = conversation

        while True:
            buffered_text: list[TextDelta] = []
            deferred_end: TurnEnd | None = None
            retry = False
            turn_stream = super().stream(next_prompt, conversation=next_conversation)
            try:
                while True:
                    try:
                        event = await anext(turn_stream)
                    except StopAsyncIteration:
                        break
                    if isinstance(event, ToolResult) and event.name == QUESTION_TOOL:
                        interview_active = True
                        yield event
                        continue

                    if interview_active and isinstance(event, TextDelta):
                        buffered_text.append(event)
                        continue

                    if interview_active and isinstance(event, TurnEnd):
                        if event.stop_reason == "tool_use":
                            # Text before a tool call is a preamble, not a final
                            # answer. The picker is the visible question instead.
                            buffered_text.clear()
                            yield event
                        else:
                            deferred_end = event
                        continue

                    if interview_active and isinstance(event, RunEnd):
                        answer = _final_answer(event.result.text)
                        if answer is None:
                            retries += 1
                            if retries > _MAX_TEXT_QUESTION_RETRIES:
                                yield TextDelta(TEXT_QUESTION_RETRY_NOTICE)
                                if deferred_end is not None:
                                    yield deferred_end
                                yield RunEnd(replace(event.result, text=TEXT_QUESTION_RETRY_NOTICE))
                                return
                            if next_conversation is None:
                                next_conversation = Conversation(event.result.messages)
                            next_conversation.messages.append(
                                Message(
                                    role="user",
                                    content=[TextBlock(text=TEXT_QUESTION_CORRECTION)],
                                )
                            )
                            next_prompt = None
                            retry = True
                            break

                        # The marker is a model-control protocol, not user
                        # content. Re-emit one clean delta instead of the
                        # buffered provider chunks that contained it.
                        yield TextDelta(answer)
                        if deferred_end is not None:
                            yield deferred_end
                        yield RunEnd(replace(event.result, text=answer))
                        return

                    yield event
            finally:
                await turn_stream.aclose()

            if not retry:
                return
