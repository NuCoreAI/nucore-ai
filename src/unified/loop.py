"""The genuine multi-turn agentic tool-calling loop.

Replaces the router's pre-planned ``route_plan``/``_execute_route_plan``
(runtime.py) with the pattern every major provider's native tool-calling API
is actually designed for: call a tool, get a result, decide whether to call
another tool or answer -- repeated in one continuous conversation, tools
staying enabled the whole time (unlike ``_synthesize_tool_result_response``,
which makes exactly one tool-call round then a separate call with
``tools=[]``).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .adapters import LLMAdapter, ToolCall, ToolSpec
from .fabrication_guard import detect_completion_claim
from utils import get_logger, get_prompt_log_manager

logger = get_logger(__name__)

ToolDispatch = Callable[[str, dict[str, Any]], Awaitable[Any]]

# See fabrication_guard.py's docstring for what this catches and what it
# doesn't. Returned instead of a fabricated claim once corrective retries
# (see max_fabrication_retries) are exhausted in "block" mode.
_FABRICATION_FALLBACK_TEXT = (
    "I'm not fully sure that completed correctly -- please check, or ask me to try again."
)


class AgenticLoop:
    """Drives one query through repeated generate/tool-execute rounds."""

    def __init__(
        self,
        *,
        llm_client: LLMAdapter,
        tool_specs: list[ToolSpec],
        dispatch: ToolDispatch,
        max_iterations: int = 8,
        fabrication_guard_mode: str = "log",
        max_fabrication_retries: int = 1,
    ) -> None:
        self.llm_client = llm_client
        self.tool_specs = tool_specs
        self._exported_tools = llm_client.export_tools(tool_specs)
        self.dispatch = dispatch
        self.max_iterations = max_iterations
        # "off": the guard never runs. "log": detect and write a
        # fabrication_flag log line, but never change what the customer
        # sees (default -- zero behavior risk). "block": also inject a
        # corrective nudge and force one more round instead of returning
        # the flagged reply, up to max_fabrication_retries times. See
        # fabrication_guard.py and this method's own comments.
        self.fabrication_guard_mode = fabrication_guard_mode
        self.max_fabrication_retries = max_fabrication_retries

    @staticmethod
    def _canonical_to_tool_calls(canonical: list[dict[str, Any]]) -> list[ToolCall]:
        return [
            ToolCall(
                call_id=str(tc.get("id", "")),
                name=str(tc.get("name", "")),
                args=tc.get("input") or {},
                provider="",
                raw=tc,
            )
            for tc in canonical
        ]

    async def run(
        self,
        *,
        system_prompt: str | list[str],
        history_messages: list[dict[str, Any]],
        user_message: str,
        llm_config: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run the loop for one user turn.

        ``system_prompt`` may be a list of sections, ordered least-to-most
        volatile (see prompt_builder.build_system_prompt_sections); each
        becomes its own system message so an adapter can give each its own
        cache breakpoint, independent of how often the others change.

        Returns:
            ``(final_text, new_messages)`` -- the model's final answer, and
            the messages generated this turn (the user turn plus every
            tool-call/tool-result round trip and the final assistant reply),
            for a caller that wants to persist the full turn.
        """
        sections = [system_prompt] if isinstance(system_prompt, str) else list(system_prompt)
        messages: list[dict[str, Any]] = (
            [{"role": "system", "content": section} for section in sections if section]
            + list(history_messages)
            + [{"role": "user", "content": user_message}]
        )
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        # UnifiedRuntime.handle_query sets this to the caller's per-conversation
        # session id (the same one GrokAdapter reads back out for its
        # x-grok-conv-id header) -- passed through to the prompt log purely to
        # scope its cross-turn content dedup (see PromptLogManager.write), not
        # used for anything else here.
        conversation_id = (llm_config or {}).get("session_id")
        # Whether any tool has actually been dispatched anywhere in this
        # turn yet -- the fabrication guard's precondition (see
        # fabrication_guard.py's docstring for why "no tool call at all",
        # not "no *matching* tool call", is what keeps this tool-agnostic).
        any_tool_dispatched = False
        fabrication_retries = 0

        for iteration in range(self.max_iterations):
            intent_name = f"unified (round {iteration + 1})"
            await get_prompt_log_manager().write(intent_name, messages, conversation_id=conversation_id)
            raw_response = await self.llm_client.generate(
                messages=messages,
                config=llm_config,
                tools=self._exported_tools,
            )
            usage = raw_response.get("usage") or {}
            if usage:
                await get_prompt_log_manager().write_usage(intent_name, usage)
            canonical_calls = raw_response.get("tool_calls") or []
            if not canonical_calls:
                text = raw_response.get("text") or raw_response.get("content") or ""
                if not isinstance(text, str):
                    text = str(text)

                if not any_tool_dispatched and self.fabrication_guard_mode != "off":
                    claim = detect_completion_claim(text)
                    if claim:
                        await get_prompt_log_manager().write_flag(
                            intent_name,
                            "fabrication_flag",
                            {"pattern": claim, "text": text, "iteration": iteration + 1},
                            conversation_id=conversation_id,
                        )
                        if (
                            self.fabrication_guard_mode == "block"
                            and fabrication_retries < self.max_fabrication_retries
                        ):
                            fabrication_retries += 1
                            nudge = {
                                "role": "user",
                                "content": (
                                    "(system note, not from the customer: your last reply claimed "
                                    "an action was completed, but no tool was called this turn. "
                                    "Call the tool the customer's request actually needs, or -- if "
                                    "nothing needs calling -- answer again without that claim.)"
                                ),
                            }
                            messages.append(nudge)
                            new_messages.append(nudge)
                            continue
                        if self.fabrication_guard_mode == "block":
                            # Retries exhausted -- don't hand back the
                            # flagged claim either; a known-safe non-claim
                            # beats a possibly-false "Done".
                            await get_prompt_log_manager().write_flag(
                                intent_name,
                                "fabrication_retry_exhausted",
                                {"text": text, "iteration": iteration + 1},
                                conversation_id=conversation_id,
                            )
                            text = _FABRICATION_FALLBACK_TEXT

                assistant_message = {"role": "assistant", "content": text}
                messages.append(assistant_message)
                new_messages.append(assistant_message)
                # Log the actual final answer within its own turn -- write()
                # is otherwise only ever called before a generate() call, so
                # without this, a turn's final reply was never captured live;
                # it only ever showed up (if at all) retroactively, mislabeled
                # under the *next* turn's timestamp, once that next turn's
                # reconstructed history happened to include it.
                await get_prompt_log_manager().write(intent_name, messages, conversation_id=conversation_id)
                return text, new_messages

            any_tool_dispatched = True
            tool_calls = self._canonical_to_tool_calls(canonical_calls)
            logger.info(
                "unified: round %d tool calls: %s",
                iteration + 1,
                [(tc.name, tc.args) for tc in tool_calls],
            )
            # Sequential, not asyncio.gather -- tool calls in the same turn can
            # have real ordering dependencies (pair a device, then reference it
            # in a scene/routine; stage one item, then another that assumes the
            # first already landed). That used to be masked by every IoXWrapper
            # HTTP call being fake-async (a blocking `requests` call dressed up
            # in an async def), which meant a "concurrently" gathered call's
            # blocking I/O monopolized the single-threaded loop and accidentally
            # serialized everything anyway. Now that HTTP calls are genuinely
            # async (httpx.AsyncClient), gather would let a later call's REST
            # request actually interleave with and outrace an earlier call's
            # still-in-flight one for the same device/state -- e.g. a
            # multi_device_scene role-check racing ahead of the pair_device
            # call adding that very device, reporting it "not available" even
            # though it was, moments later, added successfully.
            tool_results = []
            for tc in tool_calls:
                tool_results.append(await self.dispatch(tc.name, tc.args))
            logger.info("unified: round %d tool results: %s", iteration + 1, list(tool_results))

            round_trip = self.llm_client.build_tool_round_trip_messages(
                raw_response=raw_response,
                tool_calls=tool_calls,
                tool_results=list(tool_results),
                config=llm_config,
            )
            messages.extend(round_trip)
            new_messages.extend(round_trip)

        logger.warning("AgenticLoop hit max_iterations=%d without a final answer", self.max_iterations)
        # No fabrication-guard check here: this text is a hardcoded constant,
        # never model-generated, so it structurally can't claim an action
        # that didn't happen.
        fallback = (
            "I wasn't able to finish that within the allowed number of steps -- "
            "please try rephrasing or breaking it into smaller requests."
        )
        fallback_message = {"role": "assistant", "content": fallback}
        messages.append(fallback_message)
        new_messages.append(fallback_message)
        await get_prompt_log_manager().write(intent_name, messages, conversation_id=conversation_id)
        return fallback, new_messages
