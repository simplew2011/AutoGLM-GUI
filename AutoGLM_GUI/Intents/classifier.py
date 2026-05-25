"""Intent classifier powered by LLM via OpenAI-compatible API."""

import json
import logging
import argparse
from typing import Any, Literal, cast

from openai import OpenAI
from typing_extensions import get_args

from AutoGLM_GUI.Intents.prompt import PROMPT_CONTENT
from AutoGLM_GUI.Intents.utils import (
    extract_json,
    extract_code_block,
    parse_few_shot_examples
)

logger = logging.getLogger(__name__)

PROMPT_CONTENT = PROMPT_CONTENT
SYSTEM_PROMPT = extract_code_block(PROMPT_CONTENT, "## 系统提示词")
FEW_SHOT_EXAMPLES = parse_few_shot_examples(PROMPT_CONTENT)
INTENT_CATEGORY = Literal["gui_agent", "simple_chat", "layered_gui_agent"]

logger.info(
    "prompt loaded: system=%d chars, few_shot=%d examples",
    len(SYSTEM_PROMPT),
    len(FEW_SHOT_EXAMPLES),
)


class IntentResult:
    def __init__(self, category: INTENT_CATEGORY, reason: str, raw: str = ""):
        self.category = category
        self.reason = reason
        self.raw = raw

    def to_dict(self) -> dict[str, str]:
        return {"category": self.category, "reason": self.reason}

    def __repr__(self) -> str:
        return f"IntentResult(category={self.category!r}, reason={self.reason!r})"

    def __str__(self) -> str:
        return f"[{self.category}] {self.reason}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IntentResult):
            return NotImplemented
        return self.category == other.category and self.reason == other.reason


class IntentClassifier:
    """Intent classifier powered by LLM via OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "not-needed",
        model: str = "Qwen3.6-27B",
        temperature: float = 0.0,
        top_p: float = 0.01,
        max_tokens: int = 2048,
        max_retries: int = 3,
        enable_thinking: bool = False,
        few_shot: bool = False,
    ):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.enable_thinking = enable_thinking

        if few_shot:
            self._few_shot_messages = self._build_few_shot_messages()
        else:
            self._few_shot_messages = []

        logger.info(
            "IntentClassifier init: model=%s, base_url=%s, temperature=%.2f, "
            "top_p=%.2f, max_tokens=%d, max_retries=%d, few_shot=%s (%d messages)",
            model,
            base_url,
            temperature,
            top_p,
            max_tokens,
            max_retries,
            few_shot,
            len(self._few_shot_messages),
        )

    def _build_few_shot_messages(self) -> list[dict[str, str]]:
        """Build few-shot conversation messages from parsed examples."""
        messages: list[dict[str, str]] = []
        for user_input, cat, reason in FEW_SHOT_EXAMPLES:
            messages.append(
                {
                    "role": "user",
                    "content": f'用户输入: "{user_input}"',
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"category": cat, "reason": reason}, ensure_ascii=False
                    ),
                }
            )
        return messages

    def classify(self, user_input: str) -> IntentResult:
        """Classify user input into an intent category."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._few_shot_messages)
        messages.append(
            {
                "role": "user",
                "content": f'用户输入："{user_input}"',
            }
        )

        logger.info("classify processing...")

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(
                    "classify attempt=%d/%d", attempt + 1, self.max_retries + 1
                )
                extra_body = {}
                if not self.enable_thinking:
                    extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=self.temperature,
                    top_p=self.top_p,
                    max_tokens=self.max_tokens,
                    extra_body=extra_body,
                )
                finish_reason = response.choices[0].finish_reason
                if finish_reason == "length":
                    last_error = (
                        f"truncated by max_tokens={self.max_tokens}, not retrying"
                    )
                    logger.warning(last_error)
                    break
                if response.choices[0].message.content is not None:
                    text = response.choices[0].message.content.strip()
                    logger.debug("classify raw_response=%r", text[:200])
                    result = self._parse_response(text)
                    if result:
                        logger.info("classify successed, result=%s", result)
                        return result
                else:
                    last_error = f"finish_reason: {finish_reason}"
                    logger.warning(
                        "classify attempt=%d empty response: %s",
                        attempt + 1,
                        last_error,
                    )
            except Exception as e:
                last_error = e
                logger.warning("classify attempt=%d error: %s", attempt + 1, e)

        logger.error(
            "classify all %d attempts failed for input=%r, last_error=%s",
            self.max_retries + 1,
            user_input,
            last_error,
        )
        return self._rule_based_fallback(user_input, str(last_error))

    def _validate_result(self, data: dict[str, Any]) -> dict[str, str]:
        """Validate and normalize the classification result."""
        category = str(data.get("category", "")).strip().strip('"').strip("'")
        if category not in list(get_args(INTENT_CATEGORY)):
            category = "simple_chat"  # fallback

        reason = str(data.get("reason", ""))

        return {"category": category, "reason": reason}

    def _parse_response(self, text: str) -> IntentResult | None:
        """Parse LLM response text into a classification result."""
        data = extract_json(text)
        if data is None:
            logger.warning(
                "_parse_response failed to extract json from: %r", text[:200]
            )
            return None

        validated = self._validate_result(data)
        return IntentResult(
            category=cast(INTENT_CATEGORY, validated["category"]),
            reason=validated["reason"],
            raw=text,
        )

    @staticmethod
    def _rule_based_fallback(user_input: str, error_msg: str) -> IntentResult:
        """Fallback classifier using keyword rules when model calls fail."""
        logger.info("_rule_based_fallback triggered for input=%r", user_input)
        gui_keywords = [
            "淘宝",
            "京东",
            "拼多多",
            "美团",
            "饿了么",
            "高德",
            "百度地图",
            "微信",
            "QQ",
            "微博",
            "抖音",
            "B站",
            "哔哩哔哩",
            "网易云",
            "QQ音乐",
            "打开",
            "导航",
            "截图",
            "录屏",
            "音量",
            "设置",
            "安装",
            "卸载",
            "app",
            "软件",
            "小红书",
            "大众点评",
        ]
        app_names = gui_keywords[:17]
        complex_gui_keywords = [
            "分别",
            "跨应用",
        ]
        multi_app_indicators = ["和", "与", "还有", "、"]

        input_lower = user_input.lower()

        gui_score = sum(1 for kw in gui_keywords if kw.lower() in input_lower)
        complex_gui_score = sum(
            1 for kw in complex_gui_keywords if kw.lower() in input_lower
        )

        app_mentions = sum(1 for kw in app_names if kw in user_input)
        has_multi_app_connector = any(ind in user_input for ind in multi_app_indicators)

        if (app_mentions >= 3 and complex_gui_score >= 1) or (
            app_mentions >= 2 and has_multi_app_connector and complex_gui_score >= 1
        ):
            return IntentResult(
                category="layered_gui_agent",
                reason=f"规则回退（模型调用失败: {error_msg}）",
            )

        if gui_score >= 1:
            return IntentResult(
                category="gui_agent",
                reason=f"规则回退（模型调用失败: {error_msg}）",
            )

        return IntentResult(
            category="simple_chat",
            reason=f"规则回退（模型调用失败: {error_msg}）",
        )


def classify(
    user_input: str,
    base_url: str = "http://localhost:8000/v1",
    model: str = "Qwen3.6-27B",
    **kwargs: Any,
) -> IntentResult:
    """Classify user input into an intent category."""
    logger.info(
        "classify entry: input=%r, model=%s, base_url=%s", user_input, model, base_url
    )
    clf = IntentClassifier(base_url=base_url, model=model, **kwargs)
    return clf.classify(user_input)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for CLI usage. Call this before using the classifier as a script."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="用户意图分类器")
    parser.add_argument(
        "--base-url", type=str, default="http://10.24.9.2:9996/v1", help="API base URL"
    )
    parser.add_argument("--api-key", type=str, default="not-needed", help="API Key")
    parser.add_argument("--model", type=str, default="ci_model", help="Model name")
    parser.add_argument(
        "--temperature", type=float, default=0.7, help="Model temperature"
    )
    parser.add_argument("--top_p", type=float, default=0.8, help="Model top_p")
    parser.add_argument("--max_tokens", type=int, default=2048, help="Model max_tokens")
    parser.add_argument(
        "--enable_thinking",
        action="store_true",
        default=False,
        help="Enable thinking mode",
    )
    parser.add_argument(
        "--few-shot",
        action="store_true",
        default=False,
        help="Enable few-shot examples",
    )
    parser.add_argument(
        "--input", default="介绍深圳景点", help="User input to classify"
    )

    args = parser.parse_args()

    result = classify(
        user_input=args.input,
        base_url=args.base_url,
        model=args.model,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        enable_thinking=args.enable_thinking,
        few_shot=args.few_shot,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
