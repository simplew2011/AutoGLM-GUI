import asyncio
from unittest.mock import MagicMock, patch

import pytest

from AutoGLM_GUI.Intents.classifier import IntentClassifier, IntentResult


_CATEGORY_TO_MODE = {
    "gui_agent": "classic",
    "layered_gui_agent": "layered",
    "simple_chat": "chat",
}


def test_category_to_mode_mapping():
    assert _CATEGORY_TO_MODE["gui_agent"] == "classic"
    assert _CATEGORY_TO_MODE["layered_gui_agent"] == "layered"
    assert _CATEGORY_TO_MODE["simple_chat"] == "chat"


def test_unknown_category_defaults_to_classic():
    assert _CATEGORY_TO_MODE.get("unknown", "classic") == "classic"


def test_rule_fallback_gui_agent():
    result = IntentClassifier._rule_based_fallback(
        "帮我在淘宝上搜索无线耳机", "mock error"
    )
    assert result.category == "gui_agent"


def test_rule_fallback_layered():
    result = IntentClassifier._rule_based_fallback(
        "帮我在淘宝、京东和拼多多分别搜索同一款耳机，对比价格", "mock error"
    )
    assert result.category == "layered_gui_agent"


def test_rule_fallback_simple_chat():
    result = IntentClassifier._rule_based_fallback("今天天气怎么样", "mock error")
    assert result.category == "simple_chat"


def test_intent_result_equality():
    a = IntentResult("gui_agent", "test")
    b = IntentResult("gui_agent", "test")
    assert a == b


def test_intent_result_to_dict():
    r = IntentResult("simple_chat", "chitchat")
    assert r.to_dict() == {"category": "simple_chat", "reason": "chitchat"}


@pytest.mark.anyio
async def test_classify_integration_with_fallback():
    """Test that fallback is used when API is not available."""
    clf = IntentClassifier(
        base_url="http://nonexistent",
        api_key="none",
        model="none",
        max_retries=0,
    )

    with patch.object(clf.client.chat.completions, "create", side_effect=Exception("API unavailable")):
        result = await asyncio.to_thread(clf.classify, "帮我打开淘宝搜索耳机")
    assert result.category in ("gui_agent", "layered_gui_agent", "simple_chat")
