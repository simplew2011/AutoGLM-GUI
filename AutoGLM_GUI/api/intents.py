"""Intent detection API routes."""

import asyncio

from fastapi import APIRouter, HTTPException

from AutoGLM_GUI.Intents.classifier import IntentClassifier
from AutoGLM_GUI.config_manager import config_manager
from AutoGLM_GUI.logger import logger
from AutoGLM_GUI.schemas import IntentDetectRequest, IntentDetectResponse

router = APIRouter()

_CATEGORY_TO_MODE = {
    "gui_agent": "classic",
    "layered_gui_agent": "layered",
    "simple_chat": "chat",
}


@router.post("/api/intents/detect", response_model=IntentDetectResponse)
async def detect_intent(request: IntentDetectRequest) -> IntentDetectResponse:
    try:
        config = config_manager.get_effective_config()
        base_url = (config.intent_base_url or "").strip()
        model = (config.intent_model_name or "").strip()
        api_key = (config.intent_api_key or "EMPTY").strip()

        if not base_url or not model:
            raise HTTPException(
                status_code=503, detail="Intent detection model is not configured."
            )

        classifier = IntentClassifier(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=config.intent_temperature,
            top_p=config.intent_top_p,
            max_tokens=config.intent_max_tokens,
            frequency_penalty=config.intent_frequency_penalty,
            extra_body=config.intent_extra_body,
        )

        result = await asyncio.to_thread(classifier.classify, request.message)
        mode = _CATEGORY_TO_MODE.get(result.category, "classic")
        logger.info(
            "Intent detected: mode=%s, category=%s, reason=%s",
            mode,
            result.category,
            result.reason,
        )
        return IntentDetectResponse(mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Intent detection failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
