# Auto Mode (自动模式) Design

> **Date:** 2026-05-18
> **Status:** Approved

## Goal

新增自动模式（Auto Mode），用户输入任务后系统自动进行意图识别，选择合适的 agent 模式（经典/分层/对话）执行，用户可确认或纠正意图后开始执行。

**Architecture:** 前端编排两阶段。后端新增独立的意图识别模块（`AutoGLM_GUI/Intents/`），通过 `POST /api/intents/detect` 返回检测到的模式。前端展示意图结果，用户确认/纠正后，以确认后的模式创建 task session 执行任务。现有三种模式完全不动。

**Tech Stack:** Python FastAPI (backend), React + TypeScript (frontend), OpenAI-compatible LLM API (intent detection)

---

## 1. Backend: Intent Detection Module

### 1.1 New files

**`AutoGLM_GUI/Intents/__init__.py`** — exports `IntentDetector`, `IntentResult`

**`AutoGLM_GUI/Intents/types.py`**

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class IntentResult:
    mode: Literal["classic", "layered", "chat"]
```

**`AutoGLM_GUI/Intents/detector.py`**

- `IntentDetector` class, initialized with config_manager
- `async detect(message: str) -> IntentResult` — calls OpenAI-compatible API
- System prompt describes three mode characteristics, instructs model to return `{"mode": "classic|layered|chat"}`
- Text-only, no image support

### 1.2 API Endpoint

**`POST /api/intents/detect`** (new router: `api/intents.py`, registered in `api/__init__.py`)

- Request: `{"message": "帮我打开淘宝搜耳机"}`
- Response: `{"mode": "classic"}`
- Validation: message max 10000 chars, non-empty

### 1.3 Configuration

New fields in `ConfigModel` / `config_manager.py`:

| Field | Env Var | Default |
|-------|---------|---------|
| `intent_base_url` | `AUTOGLM_INTENT_BASE_URL` | `""` |
| `intent_api_key` | `AUTOGLM_INTENT_API_KEY` | `""` |
| `intent_model_name` | `AUTOGLM_INTENT_MODEL_NAME` | `""` |

Frontend config dialog: add "意图识别模型" tab in settings.

---

## 2. Frontend: Auto Mode

### 2.1 Mode Tab Order

`auto | classic | chatkit | chat` (auto first, leftmost)

In `chat.tsx`: `chatMode` state extended to `'auto' | 'classic' | 'chatkit' | 'chat'`, URL param `?mode=auto`

### 2.2 New Component: `AutoModePanel.tsx`

State machine with 4 phases:

| Phase | What happens |
|-------|-------------|
| `input` | User selects device (required, for classic/layered fallback), types message, hits send |
| `detecting` | Shows loading spinner, calls `POST /api/intents/detect` |
| `result` | Shows intent result card with confirm/switch buttons |
| `executing` | Creates task session with confirmed mode, submits task, streams events |

### 2.3 Intent Result Card UI

```
┌─────────────────────────────────────┐
│ 🤖 意图识别结果：经典模式            │
│                                     │
│ [确认执行] [切换为分层代理] [切换为对话] │
└─────────────────────────────────────┘
```

### 2.4 Execution Phase

After confirmation:
1. `createTaskSession(deviceId, serial, confirmedMode)` — session key: `auto-task-session:{deviceId}`
2. `submitTaskSessionTask(sessionId, message)` — submit original message
3. `streamTaskEvents(taskId, ...)` — SSE streaming
4. Render mode-appropriate UI using existing hooks/components:
   - `classic` → phone mirror + action steps (useTaskSessionConversation patterns)
   - `layered` → tool call expansion cards
   - `chat` → text-only conversation

### 2.5 Files Changed

| File | Change |
|------|--------|
| `frontend/src/routes/chat.tsx` | Add `'auto'` mode, reorder tabs, route to AutoModePanel |
| `frontend/src/components/AutoModePanel.tsx` | **New** — auto mode panel |
| `frontend/src/api.ts` | Add `detectIntent(message)` function |
| `frontend/src/lib/locales/zh.ts` | Add auto mode i18n keys |
| `frontend/src/lib/locales/en.ts` | Add auto mode i18n keys |

### 2.6 i18n Keys (chatkit namespace)

| Key | zh | en |
|-----|----|----|
| `chatkit.autoMode` | 自动模式 | Auto Mode |
| `chatkit.autoModeDesc` | 智能识别意图，自动选择最佳执行模式 | Smart intent recognition, auto-select best execution mode |
| `chatkit.intentDetected` | 意图识别结果 | Intent Detection Result |
| `chatkit.confirmExecute` | 确认执行 | Confirm |
| `chatkit.switchToClassic` | 切换为经典模式 | Switch to Classic |
| `chatkit.switchToLayered` | 切换为分层代理 | Switch to Layered |
| `chatkit.switchToChat` | 切换为对话模式 | Switch to Chat |
| `chatkit.detecting` | 正在识别意图... | Detecting intent... |

---

## 3. Error Handling

- Intent detection API failure → show error, allow user to manually choose mode
- Intent model not configured → show "请先配置意图识别模型" error
- Device not selected but intent needs device → prompt user (shouldn't happen since device is pre-selected)

---

## 4. Testing

- Unit tests for `IntentDetector.detect()` with mock LLM responses
- Integration test for `POST /api/intents/detect` endpoint
- Frontend: test AutoModePanel state transitions
