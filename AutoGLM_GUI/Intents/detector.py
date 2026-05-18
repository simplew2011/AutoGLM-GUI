from __future__ import annotations

import json
from typing import Any

from AutoGLM_GUI.Intents.types import IntentResult
from AutoGLM_GUI.logger import logger


INTENT_SYSTEM_PROMPT = """You are an intent classifier for a phone automation system. Given a user message, classify it into one of three modes:

- "classic": The user wants to perform GUI automation on a phone (e.g., open apps, tap buttons, navigate, type text, perform actions on the phone screen).
- "layered": The user wants to perform a complex multi-step phone task that may require planning and decomposition (e.g., research tasks, multi-app workflows, data gathering across apps).
- "chat": The user wants a pure conversation, asking questions, or chatting (no phone automation needed).

Analyze the user's message and respond with ONLY a JSON object in this exact format:
{"mode": "classic"}

Do not include any other text, explanation, or formatting."""


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
            "max_tokens": 50,
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
            content = data["choices"][0]["message"]["content"].strip()

        try:
            parsed = json.loads(content)
            mode = str(parsed.get("mode", "")).strip()
            if mode not in ("classic", "layered", "chat"):
                logger.warning(
                    f"Unexpected intent mode from LLM: {mode!r}, falling back to 'classic'"
                )
                mode = "classic"
            return IntentResult(mode=mode)
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(
                f"Failed to parse intent detection response: {e}, content: {content[:200]}"
            )
            raise RuntimeError(
                f"Failed to parse intent detection response: {e}"
            ) from e
