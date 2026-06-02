"""AsyncVaphoneAgent — VAPhone model agent with tab-separated action parsing.

Inherits from AsyncAgentBase for streaming, cancellation, and watchdog support.
Uses the Vaphone Chinese system prompt embedded as user text content
(no system role). Summary-based history: only the last summary field is kept.

Message format: single user message per step with the task define prompt
embedded as text, followed by current screenshot and step instruction.
"""

from __future__ import annotations

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

from .parser import VaphoneParser
from .prompts import SYSTEM_PROMPT, STEP_INSTRUCTION_PROMPT


class AsyncVaphoneAgent(AsyncAgentBase, AsyncAgent):
    """Async Vaphone agent using tab-separated action format.

    Each step sends a single user message with the system prompt embedded
    as text content (no system role). Summary-based history: only the
    model-generated running summary from the last step is kept.

    Unique aspects:
    - Chinese system prompt embedded in user message (no system role)
    - <THINK> tag for thinking, tab-separated key:value for actions
    - Summary-based history (only latest summary, not full action list)
    - 0-1000 coordinate space
    """

    def __init__(
        self,
        model_config: ModelConfig,
        agent_config: AgentConfig,
        device: DeviceProtocol,
        confirmation_callback: Callable[[str], bool] | None = None,
        takeover_callback: Callable[[str], None] | None = None,
    ):
        self.parser = VaphoneParser()
        super().__init__(
            model_config=model_config,
            agent_config=agent_config,
            device=device,
            confirmation_callback=confirmation_callback,
            takeover_callback=takeover_callback,
        )
        self._task: str | None = None
        self._ref_images: list[dict[str, str]] = []
        self._running_summary: str = ""
        # INFO action: track pending question and user reply separately
        self._info_question: str | None = None
        self._info_reply: str | None = None

    def set_info_reply(self, reply: str) -> None:
        """Set the user's reply to a pending INFO question.

        Called externally when the user responds to an INFO action.
        The reply will be injected into the next step's prompt.
        """
        self._info_reply = reply

    def _get_default_system_prompt(self, lang: str) -> str:
        return SYSTEM_PROMPT

    def _prepare_initial_context(
        self,
        task: str,
        screenshot_base64: str,
        current_app: str,
        reference_images: list[dict[str, str]] | None = None,
    ) -> None:
        """Stash task and reference images. Clear summary and info state."""
        self._task = task
        self._ref_images = (reference_images or []).copy()
        self._running_summary = ""
        self._info_question = None
        self._info_reply = None

    async def _execute_step(self) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a single step.

        Messages are built fresh each step. The system prompt is embedded
        as text content in the user message (no system role).
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

        # 2. Build messages — single user message with embedded system prompt
        with trace_span(
            "step.build_message",
            attrs={
                "step": self._step_count,
                "agent_type": self.__class__.__name__,
            },
        ):
            # Build user prompt text
            history_display = (
                self._running_summary
                if self._running_summary.strip()
                else "暂无历史操作"
            )

            # Inject pending INFO Q&A if user has replied
            info_text = ""
            if self._info_reply:
                question = self._info_question or ""
                info_text = (
                    f"\n这是你和用户的对话历史：\n"
                    f"你曾经提出的问题：{question}\n"
                    f"用户对你的指示：{self._info_reply}\n"
                    f"你需要更加注意用户最后的指示。\n"
                )
                self._info_question = None
                self._info_reply = None
            elif self._info_question:
                # Question asked but no reply yet — mention it
                info_text = (
                    f"\n你上一步向用户提出了问题：{self._info_question}\n"
                    f"请等待用户回复后再继续操作。\n"
                )

            user_text = (
                f"\n已知用户指令为：{self._task}\n"
                f"已知已经执行过的历史动作如下：{history_display}\n"
                f"{info_text}"
                f"当前手机屏幕截图如下：\n"
            )

            image_data_url = f"data:image/png;base64,{screenshot.base64_data}"

            # Content parts: system prompt as text + user text + image + instruction
            content_parts: list[dict[str, Any]] = [
                {"type": "text", "text": SYSTEM_PROMPT},
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url},
                },
                {"type": "text", "text": STEP_INSTRUCTION_PROMPT},
            ]

            # Attach reference images on first step
            if self._step_count == 1 and self._ref_images:
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

            # Single user message with embedded system prompt
            self._context = [
                {"role": "user", "content": content_parts},
            ]

            logger.info("============================================")
            logger.info(self._sanitize_messages_for_log(self._context))
            logger.info("============================================")

        # 3. Stream LLM call
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

                    if chunk_data["type"] == "thinking":
                        thinking_parts.append(chunk_data["content"])
                        yield {
                            "type": "thinking",
                            "data": {"chunk": chunk_data["content"]},
                        }
                        if self.agent_config.verbose:
                            logger.debug(chunk_data["content"])

                    elif chunk_data["type"] == "raw":
                        raw_content += chunk_data["content"]

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
            try:
                parsed_action = self.parser.parse_response(raw_content)
                action = self.parser.convert_action(parsed_action)

                # Extract thinking from <THINK> content
                cot = parsed_action.get("cot", "")
                if cot and not thinking:
                    thinking = cot

                # Update running summary for next step
                new_summary = parsed_action.get("summary", "")
                if new_summary:
                    self._running_summary = self.parser.clean_summary(new_summary)

                if self.agent_config.verbose:
                    logger.debug(f"raw_content: \n\n {raw_content}\n\n")
                    logger.debug(f"thinking: \n\n {thinking}\n\n")
                    logger.debug(f"parsed_action: \n\n {thinking}\n\n")
                    logger.debug(f"action: \n\n {action}\n\n")

            except Exception as e:
                logger.warning(
                    f"Failed to parse/convert action: {e}, raw_content: {raw_content}"
                )
                action = {"_metadata": "finish", "message": str(e)}

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

        # 6. Handle Take_over (INFO action): store the question so it
        # appears in the next step's prompt and the user can reply.
        if action.get("action") == "Take_over":
            self._info_question = action.get("message", "")
            self._info_reply = None
            logger.info(
                f"Vaphone INFO question (step {self._step_count}): "
                f"{self._info_question}"
            )

        # 7. Check completion
        finished = action.get("_metadata") == "finish" or result.should_finish

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

        Uses marker-based splitting: everything between <THINK> and </THINK>
        is yielded as "thinking" (with tags stripped), everything after
        </THINK> is "raw" (the tab-separated action).
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
        end_marker = "</THINK>"  # noqa: S105
        start_markers = ["<THINK>", "<think>", "<THINK ", "<think ", "<TINK>"]
        after_think = False

        try:
            async for chunk in stream:
                if self._cancel_event.is_set():
                    await stream.close()
                    raise asyncio.CancelledError()

                if len(chunk.choices) == 0:
                    continue

                delta = chunk.choices[0].delta
                if delta.content is not None:
                    content = delta.content
                    yield {"type": "raw", "content": content}

                    if after_think:
                        continue

                    buffer += content

                    # Normalize case for closing tag matching
                    normalized = buffer.replace("</THINK>", end_marker).replace(
                        "</think>", end_marker
                    )

                    if end_marker in normalized:
                        think_part = normalized.split(end_marker, 1)[0]
                        # Strip opening <THINK> / <think> tag from thinking
                        for sm in start_markers:
                            if sm in think_part:
                                think_part = think_part.split(sm, 1)[1]
                                break
                        clean_thinking = think_part.strip()
                        if clean_thinking:
                            yield {
                                "type": "thinking",
                                "content": clean_thinking,
                            }
                        after_think = True
        finally:
            await stream.close()
