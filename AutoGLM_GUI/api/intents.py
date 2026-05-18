"""Intent detection API routes."""

from fastapi import APIRouter, HTTPException

from AutoGLM_GUI.Intents import IntentDetector
from AutoGLM_GUI.config_manager import config_manager
from AutoGLM_GUI.schemas import IntentDetectRequest, IntentDetectResponse

router = APIRouter()


@router.post("/api/intents/detect", response_model=IntentDetectResponse)
async def detect_intent(request: IntentDetectRequest) -> IntentDetectResponse:
    try:
        detector = IntentDetector(config_manager)
        result = await detector.detect(request.message)
        return IntentDetectResponse(mode=result.mode)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
