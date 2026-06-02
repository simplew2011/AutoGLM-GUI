"""AsyncMobiZenAgent — MobiZen-GUI model agent with tool_call parsing.

Inherits from AsyncAgentBase for streaming, cancellation, and watchdog support.
Uses the MobiZen system prompt and stateless per-step message format:
every API request consists of exactly 2 messages (system + user), with
step history compressed into a text summary inside the user message.
This matches the original MobiZen-GUI QwenMessageBuilder.build_messages().
"""

from __future__ import annotations

import json
import time
import asyncio
import traceback
from typing import Any
from collections.abc import AsyncGenerator, Callable

from AutoGLM_GUI.logger import logger
from AutoGLM_GUI.trace import trace_span
from AutoGLM_GUI.prompt_config import get_messages
from AutoGLM_GUI.agents.base import AsyncAgentBase
from AutoGLM_GUI.agents.protocols import AsyncAgent
from AutoGLM_GUI.config import AgentConfig, ModelConfig
from AutoGLM_GUI.device_protocol import DeviceProtocol


from .parser import MobiZenParser
from .prompts import MOBIZEN_SYSTEM_PROMPT


def _count_image_parts(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            count += sum(
                1
                for part in content
                if isinstance(part, dict) and part.get("type") == "image_url"
            )
    return count


class AsyncMobiZenAgent(AsyncAgentBase, AsyncAgent):
    """Async MobiZen-GUI agent using stateless tool_call XML format.

    Each step sends a fresh [system, user] message pair. Step history is
    compressed into a text string inside the user message, matching the
    original MobiZen-GUI implementation. The model never sees its own
    previous outputs (no assistant messages in the conversation context).
    """

    def __init__(
        self,
        model_config: ModelConfig,
        agent_config: AgentConfig,
        device: DeviceProtocol,
        confirmation_callback: Callable[[str], bool] | None = None,
        takeover_callback: Callable[[str], None] | None = None,
    ):
        self.parser = MobiZenParser()
        super().__init__(
            model_config=model_config,
            agent_config=agent_config,
            device=device,
            confirmation_callback=confirmation_callback,
            takeover_callback=takeover_callback,
        )
        self._task: str | None = None
        self._ref_images: list[dict[str, str]] = []

    def _get_default_system_prompt(self, lang: str) -> str:
        return MOBIZEN_SYSTEM_PROMPT

    def _prepare_initial_context(
        self,
        task: str,
        screenshot_base64: str,
        current_app: str,
        reference_images: list[dict[str, str]] | None = None,
    ) -> None:
        """Stash task and reference images. History is cleared per task."""
        self._task = task
        self._ref_images = (reference_images or []).copy()
        self._mobizen_history: list[dict[str, Any]] = []

    async def _execute_step(self) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a single step: screenshot -> build messages -> LLM ->
        parse -> execute.

        Messages are built fresh each step: [system, user] only.
        Previous step subtasks are compressed into a text string inside
        the user message, matching the original MobiZen-GUI format.
        """
        self._step_count += 1

        # 1. Capture device state
        try:
            with trace_span(
                "step.capture_screenshot",
                attrs={
                    "step": self._step_count,
                    "agent_type": self.__class__.__name__,
                },
            ):
                screenshot = await asyncio.to_thread(self.device.get_screenshot)
        except Exception as e:
            logger.error(f"Failed to get screenshot: {e}")
            yield {"type": "error", "data": {"message": f"Device error: {e}"}}
            yield {
                "type": "step",
                "data": {
                    "step": self._step_count,
                    "thinking": "",
                    "action": None,
                    "success": False,
                    "finished": True,
                    "message": f"Device error: {e}",
                },
            }
            return

        # 2. Build messages — stateless: [system, user] only
        with trace_span(
            "step.build_message",
            attrs={
                "step": self._step_count,
                "agent_type": self.__class__.__name__,
            },
        ):
            # Build history text from previous steps (compressed)
            history_text = ""
            for idx, hist in enumerate(self._mobizen_history):
                subtask = hist.get("subtask", "")
                subtask = subtask.replace("\n", "").replace('"', "")
                history_text += f"Step {idx + 1}: {subtask}\n"

            if not self._mobizen_history:
                user_query = f"The user query: {self._task}.\n"
            else:
                user_query = (
                    f"The user query: {self._task}.\n"
                    f"Task progress (You have done the following operation "
                    f"on the current device): {history_text}"
                )

            # Build user message: text first, then screenshot image
            # (matches original MobiZen-GUI message order)
            image_data_url = f"data:image/png;base64,{screenshot.base64_data}"
            content_parts: list[dict[str, Any]] = [
                {"type": "text", "text": user_query},
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url},
                },
            ]

            # Attach reference images on first step
            if not self._mobizen_history and self._ref_images:
                for ref in self._ref_images:
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{ref['mime_type']};base64,{ref['data']}"
                                ),
                            },
                        }
                    )
                self._ref_images = []

            # Reset context to fresh [system, user] pair
            self._context = [
                self._initial_system_message,
                {"role": "user", "content": content_parts},
            ]

        # 3. Stream LLM call
        image_count = _count_image_parts(self._context)
        if image_count < 1:
            logger.warning(
                "MobiZen request should carry at least one screenshot, "
                "got %d (step %d)",
                image_count,
                self._step_count,
            )

        try:
            if self.agent_config.verbose:
                msgs = get_messages(self.agent_config.lang)
                logger.debug(f"💭 {msgs['thinking']}:")

            thinking_parts: list[str] = []
            raw_content = ""

            with trace_span(
                "step.llm",
                attrs={
                    "step": self._step_count,
                    "agent_type": self.__class__.__name__,
                    "model_name": self.model_config.model_name,
                },
            ):
                async for chunk_data in self._stream_openai(self._context):
                    if self._cancel_event.is_set():
                        raise asyncio.CancelledError()

                    if chunk_data["type"] in ["thinking", "reasoning"]:
                        thinking_parts.append(chunk_data["content"])
                        yield {
                            "type": "thinking",
                            "data": {"chunk": chunk_data["content"]},
                        }
                        if self.agent_config.verbose:
                            logger.debug(chunk_data["content"])

                    elif chunk_data["type"] == "raw":
                        raw_content += chunk_data["content"]
                    else:
                        logger.warning(
                            f"Unknown chunk type: {chunk_data['type']}, "
                            f"chunk_data: {chunk_data}"
                        )

            thinking = "".join(thinking_parts)

        except asyncio.CancelledError:
            logger.info(f"Step {self._step_count} cancelled during LLM call")
            raise

        except Exception as e:
            logger.error(f"LLM error: {e}")
            if self.agent_config.verbose:
                logger.debug(traceback.format_exc())
            yield {"type": "error", "data": {"message": f"Model error: {e}"}}
            yield {
                "type": "step",
                "data": {
                    "step": self._step_count,
                    "thinking": "",
                    "action": None,
                    "success": False,
                    "finished": True,
                    "message": f"Model error: {e}",
                },
            }
            return

        # 4. Parse response and convert action
        with trace_span(
            "step.parse_action",
            attrs={
                "step": self._step_count,
                "agent_type": self.__class__.__name__,
            },
        ):
            parsed_thinking, subtask, mobizen_action = self.parser.parse_response(
                raw_content
            )
            if not thinking and parsed_thinking:
                thinking = parsed_thinking

            try:
                if mobizen_action:
                    action = self.parser.convert_action(mobizen_action)
                else:
                    action = {
                        "_metadata": "finish",
                        "message": raw_content or "No action parsed",
                    }
            except Exception as e:
                logger.warning(f"Failed to convert action: {mobizen_action}, err: {e}")
                action = {"_metadata": "finish", "message": str(e)}

            if self.agent_config.verbose:
                logger.debug(f"raw_content: \n\n {raw_content}\n\n")
                logger.debug(f"thinking_parts: \n\n {thinking_parts}\n\n")
                logger.debug(f"parsed_thinking: \n\n{parsed_thinking}\n\n")
                logger.debug(f"subtask: \n\n{subtask}\n\n")
                logger.debug(f"mobizen_action: \n\n{mobizen_action}\n\n")
                logger.debug(f"action: \n\n{action}\n\n")

        if self.agent_config.verbose:
            msgs = get_messages(self.agent_config.lang)
            logger.debug(f"🎯 {msgs['action']}:")
            logger.debug(json.dumps(action, ensure_ascii=False, indent=2))

        # 5. Execute action
        try:
            with trace_span(
                "step.execute_action",
                attrs={
                    "step": self._step_count,
                    "agent_type": self.__class__.__name__,
                    "action_name": action.get("action"),
                    "action_type": action.get("_metadata"),
                },
            ):
                result = await asyncio.to_thread(
                    self.action_handler.execute,
                    action,
                    screenshot.width,
                    screenshot.height,
                )
        except Exception as e:
            logger.error(f"Action execution error: {e}")
            if self.agent_config.verbose:
                logger.debug(traceback.format_exc())
            from AutoGLM_GUI.actions import ActionResult

            result = ActionResult(success=False, should_finish=True, message=str(e))

        if self._step_count <= 1:
            # same as mobizen official process
            time.sleep(3)

        # 6. Record step in history (for next step's text compression)
        self._mobizen_history.append(
            {
                "subtask": subtask,
                "thought": thinking,
                "action": mobizen_action,
            }
        )

        # 7. Check completion
        finished = action.get("_metadata") == "finish" or result.should_finish
        if finished and self.agent_config.verbose:
            msgs = get_messages(self.agent_config.lang)
            logger.debug(
                f"✅ {msgs['task_completed']}: "
                f"{result.message or action.get('message', msgs['done'])}"
            )

        # 8. Yield step result
        yield {
            "type": "step",
            "data": {
                "step": self._step_count,
                "thinking": thinking,
                "action": action,
                "success": result.success,
                "finished": finished,
                "message": result.message or action.get("message"),
                "screenshot": screenshot.base64_data if screenshot else None,
            },
        }

    async def _stream_openai(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[dict[str, str], None]:
        """Stream OpenAI chat completion, yielding thinking and raw chunks.

        Uses marker-based splitting: everything before <tool_call> is
        "thinking", everything after is "raw" (the tool call JSON).
        """
        stream = await self.openai_client.chat.completions.create(
            messages=messages,  # type: ignore[arg-type]
            model=self.model_config.model_name,
            max_tokens=self.model_config.max_tokens,
            temperature=self.model_config.temperature,
            top_p=self.model_config.top_p,
            frequency_penalty=self.model_config.frequency_penalty,
            extra_body=self.model_config.extra_body,
            stream=True,
        )

        buffer = ""
        marker = "<tool_call>"
        in_tool_call = False
        reasoning_buffer = ""

        try:
            async for chunk in stream:
                if self._cancel_event.is_set():
                    await stream.close()
                    raise asyncio.CancelledError()

                if len(chunk.choices) == 0:
                    continue

                delta = chunk.choices[0].delta
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    reasoning_buffer += delta.reasoning_content
                    yield {"type": "reasoning", "content": delta.reasoning_content}

                if delta.content is not None:
                    content = delta.content
                    yield {"type": "raw", "content": content}

                    if in_tool_call:
                        continue

                    buffer += content

                    if marker in buffer:
                        thinking_part = buffer.split(marker, 1)[0]
                        yield {"type": "thinking", "content": thinking_part}
                        in_tool_call = True
                        continue

                    is_potential = False
                    for i in range(1, len(marker)):
                        if buffer.endswith(marker[:i]):
                            is_potential = True
                            break

                    if not is_potential and len(buffer) > 0:
                        yield {"type": "thinking", "content": buffer}
                        buffer = ""
        finally:
            await stream.close()
            if reasoning_buffer:
                logger.debug(f"Total reasoning content: {len(reasoning_buffer)} chars")
