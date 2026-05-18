from __future__ import annotations

import json
import re
from typing import Any, Literal, cast

from AutoGLM_GUI.Intents.types import IntentResult
from AutoGLM_GUI.logger import logger


INTENT_SYSTEM_PROMPT = """You are an intent classifier for a phone automation system. Given a user message, classify it into one of three modes:

- "classic": The user wants to perform GUI automation on a phone (e.g., open apps, tap buttons, navigate, type text, perform actions on the phone screen).
- "layered": The user wants to perform a complex multi-step phone task that may require planning and decomposition (e.g., research tasks, multi-app workflows, data gathering across apps).
- "chat": The user wants a pure conversation, asking questions, or chatting (no phone automation needed).

Analyze the user's message and respond with ONLY a JSON object in this exact format:
{"mode": "classic"}

Do not include any other text, explanation, or formatting."""

_VALID_MODES = frozenset({"classic", "layered", "chat"})


def _extract_mode(text: str) -> str | None:
    """Extract mode from text, handling JSON and plain-text responses."""
    # Try direct JSON parse
    try:
        parsed = json.loads(text)
        mode = str(parsed.get("mode", "")).strip().lower()
        if mode in _VALID_MODES:
            return mode
    except (json.JSONDecodeError, TypeError):
        pass

    # Try regex extraction: "classic", "layered", "chat"
    for mode in ("classic", "layered", "chat"):
        if re.search(rf'\b{mode}\b', text, re.IGNORECASE):
            return mode

    return None


class IntentDetector:
    def __init__(self, config_manager: Any) -> None:
        self._config_manager = config_manager

    @property
    def _intent_config(self) -> dict[str, str]:
        config = self._config_manager.get_effective_config()
        return {
            "base_url": (config.intent_base_url or "").strip(),
            "api_key": (config.intent_api_key or "EMPTY").strip(),
            "model_name": (config.intent_model_name or "").strip(),
        }

    async def detect(self, message: str) -> IntentResult:
        config = self._intent_config
        if not config["base_url"] or not config["model_name"]:
            raise RuntimeError("Intent detection model is not configured.")

        import httpx

        url = f"{config['base_url'].rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        }
        payload = {
            "model": config["model_name"],
            "messages": [
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            "temperature": 0.0,
            "max_tokens": 200,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error(
                    f"Intent detection API error (status={resp.status_code}): {resp.text[:500]}"
                )
                raise RuntimeError(
                    f"Intent detection API returned status {resp.status_code}"
                )
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()

        # For thinking models, content may be None/empty — try reasoning fields
        if not content:
            content = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()

        if not content:
            logger.error("Intent detection returned empty response")
            raise RuntimeError("Intent detection returned empty response")

        mode = _extract_mode(content)
        if mode is None:
            logger.warning(
                f"Could not extract mode from response: {content[:200]}"
            )
            raise RuntimeError("Failed to parse intent detection response")

        return IntentResult(mode=cast(Literal["classic", "layered", "chat"], mode))
