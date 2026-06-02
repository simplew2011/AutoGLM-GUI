"""AsyncMAIAgent - 异步 MAI Agent，基于 AsyncAgentBase。

核心特性：
- 多图像历史上下文（保留最近 N 张截图，对齐 MAI-UI 官方实现）
- XML 格式的思考过程和动作输出
- 999 坐标系统归一化
- 自动重试机制
- 原生 async 流式输出和取消
"""

import asyncio
import base64
import traceback
from collections.abc import AsyncGenerator, Callable
from io import BytesIO
from typing import Any

from PIL import Image

from AutoGLM_GUI.actions import ActionResult
from AutoGLM_GUI.agents.base import AsyncAgentBase
from AutoGLM_GUI.config import AgentConfig, ModelConfig
from AutoGLM_GUI.device_protocol import DeviceProtocol
from AutoGLM_GUI.logger import logger
from AutoGLM_GUI.trace import trace_span

from .parser import MAIParseError, MAIParser
from .prompts import MAI_MOBILE_SYSTEM_PROMPT
from .traj_memory import TrajMemory, TrajStep


class AsyncMAIAgent(AsyncAgentBase):
    """异步 MAI Agent，通过多图历史上下文 + XML 格式解析执行操作。

    与其他 AsyncAgent 的关键差异：
    - 不使用基类的 self._context 累积消息，而是每步通过 TrajMemory 重建完整消息列表
    - 支持多图历史（最近 N 张截图同时送入 LLM）
    - LLM 解析失败时自动重试（最多 3 次）
    """

    def __init__(
        self,
        model_config: ModelConfig,
        agent_config: AgentConfig,
        device: DeviceProtocol,
        history_n: int = 3,
        confirmation_callback: Callable[[str], bool] | None = None,
        takeover_callback: Callable[[str], None] | None = None,
    ):
        self._history_n = history_n
        self.parser = MAIParser()
        self.traj_memory = TrajMemory(task_goal="", task_id="", steps=[])

        super().__init__(
            model_config=model_config,
            agent_config=agent_config,
            device=device,
            confirmation_callback=confirmation_callback,
            takeover_callback=takeover_callback,
        )

    def _get_default_system_prompt(self, lang: str) -> str:
        return MAI_MOBILE_SYSTEM_PROMPT

    def _prepare_initial_context(
        self,
        task: str,
        screenshot_base64: str,
        current_app: str,
        reference_images: list[dict[str, str]] | None = None,
    ) -> None:
        """MAI 不使用基类的 _context 累积，只记录 task_goal。

        实际消息在每步的 _build_messages 中通过 TrajMemory 动态构建。
        """
        self.traj_memory = TrajMemory(task_goal=task, task_id="", steps=[])

    async def _execute_step(self) -> AsyncGenerator[dict[str, Any], None]:
        """执行单步：获取截图 -> 构建多图消息 -> 流式调用 LLM -> 解析 -> 执行动作。"""
        self._step_count += 1

        # 1. 获取当前屏幕状态
        try:
            with trace_span(
                "step.capture_screenshot",
                attrs={"step": self._step_count, "agent_type": self.__class__.__name__},
            ):
                screenshot = await asyncio.to_thread(self.device.get_screenshot)
        except Exception as e:
            logger.error(f"Failed to get device info: {e}")
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

        with trace_span(
            "step.build_message",
            attrs={"step": self._step_count, "agent_type": self.__class__.__name__},
        ):
            screenshot_bytes = base64.b64decode(screenshot.base64_data)
            pil_image = Image.open(BytesIO(screenshot_bytes))

            with trace_span(
                "memory.read",
                attrs={
                    "step": self._step_count,
                    "memory_type": "mai_traj",
                    "history_n": self._history_n,
                    "stored_steps": len(self.traj_memory.steps),
                },
            ):
                messages = self._build_messages(
                    instruction=self.traj_memory.task_goal,
                    current_screenshot_base64=screenshot.base64_data,
                )

        # 3. 带重试的 LLM 调用 + 解析
        max_retries = 3
        raw_content = ""
        thinking = ""
        raw_action = None
        converted_action = None

        for attempt in range(max_retries):
            try:
                if self._cancel_event.is_set():
                    raise asyncio.CancelledError()

                thinking_parts: list[str] = []
                raw_content = ""

                with trace_span(
                    "step.llm",
                    attrs={
                        "step": self._step_count,
                        "attempt": attempt + 1,
                        "agent_type": self.__class__.__name__,
                        "model_name": self.model_config.model_name,
                        "message_count": len(messages),
                    },
                ):
                    async for chunk_data in self._stream_openai(messages):
                        if self._cancel_event.is_set():
                            raise asyncio.CancelledError()

                        if chunk_data["type"] == "thinking":
                            thinking_parts.append(chunk_data["content"])
                            yield {
                                "type": "thinking",
                                "data": {"chunk": chunk_data["content"]},
                            }
                        elif chunk_data["type"] == "raw":
                            raw_content += chunk_data["content"]

                thinking = "".join(thinking_parts)

                with trace_span(
                    "step.parse_action",
                    attrs={
                        "step": self._step_count,
                        "attempt": attempt + 1,
                        "agent_type": self.__class__.__name__,
                    },
                ):
                    if self.agent_config.verbose:
                        logger.debug(f"raw_content: \n\n {raw_content}\n\n")
                        logger.debug(f"thinking_parts: \n\n {thinking_parts}\n\n")

                    parsed = self.parser.parse_with_thinking(raw_content)
                    thinking = parsed["thinking"]
                    raw_action = parsed["raw_action"]
                    converted_action = parsed["converted_action"]

                if self.agent_config.verbose:
                    logger.debug(f"parsed_thinking: \n\n{thinking}\n\n")
                    logger.debug(f"action_str: \n\n{raw_action}\n\n")
                    logger.debug(f"action: \n\n{converted_action}\n\n")

                break

            except asyncio.CancelledError:
                logger.info(f"Step {self._step_count} cancelled during LLM call")
                raise

            except MAIParseError as e:
                logger.warning(
                    f"Parse failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    yield {"type": "error", "data": {"message": f"Parse error: {e}"}}
                    yield {
                        "type": "step",
                        "data": {
                            "step": self._step_count,
                            "thinking": thinking,
                            "action": None,
                            "success": False,
                            "finished": True,
                            "message": f"Parse error after {max_retries} retries: {e}",
                        },
                    }
                    return

            except Exception as e:
                logger.warning(
                    f"Model call failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
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
                            "message": f"Model error after {max_retries} retries: {e}",
                        },
                    }
                    return

        if not raw_content or raw_action is None or converted_action is None:
            yield {
                "type": "step",
                "data": {
                    "step": self._step_count,
                    "thinking": thinking,
                    "action": None,
                    "success": False,
                    "finished": True,
                    "message": "Failed to get valid response after retries",
                },
            }
            return

        if self.agent_config.verbose:
            logger.debug(f"Step {self._step_count} action: {converted_action}")

        # 4. 记录轨迹
        with trace_span(
            "step.update_context",
            attrs={"step": self._step_count, "agent_type": self.__class__.__name__},
        ):
            traj_step = TrajStep(
                screenshot=pil_image,
                accessibility_tree=None,
                prediction=raw_content,
                action=raw_action,
                conclusion="",
                thought=thinking,
                step_index=self._step_count - 1,
                agent_type="AsyncMAIAgent",
                model_name=self.model_config.model_name,
                screenshot_bytes=screenshot_bytes,
                structured_action={"action_json": raw_action},
            )
            with trace_span(
                "memory.write",
                attrs={
                    "step": self._step_count,
                    "memory_type": "mai_traj",
                    "item_type": "TrajStep",
                    "history_n": self._history_n,
                    "model_name": self.model_config.model_name,
                },
            ):
                self.traj_memory.add_step(traj_step)

        # 5. 执行动作
        try:
            with trace_span(
                "step.execute_action",
                attrs={
                    "step": self._step_count,
                    "agent_type": self.__class__.__name__,
                    "action_name": converted_action.get("action"),
                    "action_type": converted_action.get("_metadata"),
                },
            ):
                result = await asyncio.to_thread(
                    self.action_handler.execute,
                    converted_action,
                    screenshot.width,
                    screenshot.height,
                )
        except Exception as e:
            logger.error(f"Action execution error: {e}")
            if self.agent_config.verbose:
                logger.debug(traceback.format_exc())
            result = ActionResult(success=False, should_finish=True, message=str(e))

        # 6. 检查完成
        finished = converted_action.get("_metadata") == "finish" or result.should_finish

        # 7. 返回步骤结果
        yield {
            "type": "step",
            "data": {
                "step": self._step_count,
                "thinking": thinking,
                "action": converted_action,
                "success": result.success,
                "finished": finished,
                "message": result.message or converted_action.get("message"),
                "screenshot": screenshot.base64_data,
            },
        }

    async def _stream_openai(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[dict[str, str], None]:
        """流式调用 OpenAI，yield thinking chunks 和 raw content。"""
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
        action_markers = ["</thinking>", "<tool_call>"]
        in_action_phase = False

        logger.info("================================================")
        logger.info(f"messages: {self._sanitize_messages_for_log(messages)}")
        logger.info("================================================")

        try:
            async for chunk in stream:
                if self._cancel_event.is_set():
                    await stream.close()
                    raise asyncio.CancelledError()

                if len(chunk.choices) == 0:
                    continue

                if chunk.choices[0].delta.content is not None:
                    content = chunk.choices[0].delta.content
                    yield {"type": "raw", "content": content}

                    if in_action_phase:
                        continue

                    buffer += content

                    marker_found = False
                    for marker in action_markers:
                        if marker in buffer:
                            thinking_part = buffer.split(marker, 1)[0]
                            if thinking_part:
                                yield {"type": "thinking", "content": thinking_part}
                            in_action_phase = True
                            marker_found = True
                            break

                    if marker_found:
                        continue

                    is_potential_marker = False
                    for marker in action_markers:
                        for i in range(1, len(marker)):
                            if buffer.endswith(marker[:i]):
                                is_potential_marker = True
                                break
                        if is_potential_marker:
                            break

                    if not is_potential_marker and len(buffer) > 0:
                        yield {"type": "thinking", "content": buffer}
                        buffer = ""

        finally:
            await stream.close()

    def _build_messages(
        self, instruction: str, current_screenshot_base64: str
    ) -> list[dict[str, Any]]:
        """Build multi-image context messages aligned with MAI-UI official format.

        Message structure (with 2-step history, history_n=3):
        [
          system: pure string,
          user: [{"type": "text", "text": instruction}],
          user: [{"type": "image_url", ...}],   # step 0 screenshot (input)
          assistant: pure string,               # step 0 raw model output
          user: [{"type": "image_url", ...}],   # step 1 screenshot (input)
          assistant: pure string,               # step 1 raw model output
          user: [{"type": "image_url", ...}],   # current screenshot
        ]

        Key format requirements (strictly aligned with MAI-UI official):
        - system.content is a pure string, NOT a list
        - assistant.content is a pure string (raw model output), NOT a list
        - user.content is a list of content blocks (text and/or image_url)
        - user(text:task) and user(image:screenshot) are separate messages
        """
        system_prompt = self.agent_config.system_prompt or MAI_MOBILE_SYSTEM_PROMPT

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [{"type": "text", "text": instruction}],
            },
        ]

        steps = self.traj_memory.steps
        if steps:
            for step in steps:
                # Include the screenshot that was shown to the model for this step
                if step.screenshot_bytes:
                    img_base64 = base64.b64encode(step.screenshot_bytes).decode("utf-8")
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_base64}"
                                    },
                                }
                            ],
                        }
                    )

                # Use raw model output directly as assistant response.
                # This avoids precision loss from round-tripping parsed
                # [0,1] coordinates back to 999-scale, and preserves the
                # exact format the model itself produced.
                if step.prediction:
                    messages.append({"role": "assistant", "content": step.prediction})

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{current_screenshot_base64}"
                        },
                    }
                ],
            }
        )

        # Retain at most history_n images (including current screenshot)
        messages = self._limit_images(messages)

        return messages

    def _limit_images(
        self, messages: list[dict[str, Any]], max_images: int | None = None
    ) -> list[dict[str, Any]]:
        """Limit the number of image-bearing user messages in context.

        MAI-UI recommends retaining at most 3 recent screenshots (history_n).
        Only user(image) messages are removed; corresponding assistant
        messages are kept as textual history for the model.
        """
        if max_images is None:
            max_images = self._history_n

        image_indices: list[int] = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        image_indices.append(i)
                        break

        if len(image_indices) > max_images:
            indices_to_remove = set(image_indices[:-max_images])
            messages = [
                msg for i, msg in enumerate(messages) if i not in indices_to_remove
            ]

        return messages

    def reset(self) -> None:
        """重置状态，包括 TrajMemory。"""
        super().reset()
        self.traj_memory.clear()
