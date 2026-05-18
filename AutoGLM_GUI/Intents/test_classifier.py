"""Unit tests for intent classifier - JSON extraction, validation, rule fallback."""

import json
from pathlib import Path

import pytest

from classifier import IntentClassifier, IntentResult
from utils import extract_json, extract_code_block, parse_few_shot_examples


# ========== JSON 提取测试 ==========

class TestExtractJson:
    def test_plain_json(self):
        raw = '{"category": "gui_agent", "reason": "test"}'
        result = extract_json(raw)
        assert result["category"] == "gui_agent"

    def test_json_with_whitespace(self):
        raw = '  \n  {"category": "simple_chat"}  \n  '
        result = extract_json(raw)
        assert result["category"] == "simple_chat"

    def test_json_in_markdown_block(self):
        raw = "```json\n{\"category\": \"layered_gui_agent\", \"reason\": \"test\"}\n```"
        result = extract_json(raw)
        assert result["category"] == "layered_gui_agent"

    def test_json_in_text(self):
        raw = "分类结果如下：\n{\"category\": \"gui_agent\", \"reason\": \"shopping\"}"
        result = extract_json(raw)
        assert result["category"] == "gui_agent"

    def test_invalid_json_returns_none(self):
        raw = "这不是 JSON 格式的内容"
        assert extract_json(raw) is None

    def test_empty_string(self):
        assert extract_json("") is None


# ========== 结果校验测试 ==========

class TestValidateResult:
    def setup_method(self):
        self.clf = IntentClassifier()

    def test_valid_result(self):
        result = self.clf._validate_result({"category": "gui_agent", "reason": "test"})
        assert result["category"] == "gui_agent"
        assert result["reason"] == "test"

    def test_invalid_category_fallback(self):
        result = self.clf._validate_result({"category": "unknown_type"})
        assert result["category"] == "simple_chat"

    def test_missing_fields(self):
        result = self.clf._validate_result({})
        assert result["category"] == "simple_chat"
        assert result["reason"] == ""


# ========== 规则回退测试 ==========

class TestRuleBasedFallback:
    def test_gui_agent_shopping(self):
        result = IntentClassifier._rule_based_fallback(
            "帮我在淘宝上搜索无线耳机", "mock error"
        )
        assert result.category == "gui_agent"

    def test_gui_agent_navigation(self):
        result = IntentClassifier._rule_based_fallback(
            "用高德地图导航到机场", "mock error"
        )
        assert result.category == "gui_agent"

    def test_gui_agent_social(self):
        result = IntentClassifier._rule_based_fallback(
            "在微信上给张三发消息", "mock error"
        )
        assert result.category == "gui_agent"

    def test_layered_gui_agent_complex_gui_long_horizon(self):
        result = IntentClassifier._rule_based_fallback(
            "帮我在小红书、抖音和大众点评分别搜索附近适合约会的餐厅，对比评分和评论后选择一家并导航过去", "mock error"
        )
        assert result.category == "layered_gui_agent"

    def test_layered_gui_agent_multi_app_comparison(self):
        result = IntentClassifier._rule_based_fallback(
            "帮我打开淘宝和京东分别搜索同一款耳机，对比价格、评价和配送时间后选择更合适的下单", "mock error"
        )
        assert result.category == "layered_gui_agent"

    def test_code_request_is_simple_chat_under_new_semantics(self):
        result = IntentClassifier._rule_based_fallback(
            "帮我写一个Python排序算法并调试bug", "mock error"
        )
        assert result.category == "simple_chat"

    def test_ambiguous_task_defaults_to_gui_agent(self):
        result = IntentClassifier._rule_based_fallback(
            "在淘宝搜索耳机并比较一下价格", "mock error"
        )
        assert result.category == "gui_agent"

    def test_single_app_multi_step_is_gui_agent(self):
        result = IntentClassifier._rule_based_fallback(
            "在大众点评找附近的餐厅，看看评分高的", "mock error"
        )
        assert result.category == "gui_agent"

    def test_prompt_defines_layered_gui_agent_as_complex_gui_agent(self):
        content = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")
        system = extract_code_block(content, "## 系统提示词")
        assert "复杂的手机控制需求" in system
        assert "多应用" in system
        assert "边界模糊" in system or "不确定" in system or "优先" in system

    def test_simple_chat_default(self):
        result = IntentClassifier._rule_based_fallback(
            "今天天气怎么样", "mock error"
        )
        assert result.category == "simple_chat"

    def test_simple_chat_translation(self):
        result = IntentClassifier._rule_based_fallback(
            "翻译这句话为英文", "mock error"
        )
        assert result.category == "simple_chat"


# ========== IntentResult 对象测试 ==========

class TestIntentResult:
    def test_to_dict(self):
        result = IntentResult("gui_agent", "test reason")
        d = result.to_dict()
        assert d == {"category": "gui_agent", "reason": "test reason"}

    def test_repr(self):
        result = IntentResult("simple_chat", "chitchat")
        assert "simple_chat" in repr(result)

    def test_str(self):
        result = IntentResult("gui_agent", "操作淘宝")
        assert str(result) == "[gui_agent] 操作淘宝"

    def test_eq_same(self):
        a = IntentResult("gui_agent", "操作淘宝")
        b = IntentResult("gui_agent", "操作淘宝")
        assert a == b

    def test_eq_different(self):
        a = IntentResult("gui_agent", "操作淘宝")
        b = IntentResult("simple_chat", "常识问答")
        assert a != b

    def test_eq_non_intent_result(self):
        result = IntentResult("gui_agent", "操作淘宝")
        assert result != "not an IntentResult"


# ========== Prompt 覆盖测试 ==========

class TestPromptCoverage:
    """Verify prompt.md contains all required sections and boundary definitions."""

    PROMPT_PATH = Path(__file__).parent / "prompt.md"

    def setup_method(self):
        self.content = self.PROMPT_PATH.read_text(encoding="utf-8")

    def test_has_system_prompt_section(self):
        assert "## 系统提示词" in self.content

    def test_has_user_template_section(self):
        assert "## 用户输入模板" in self.content

    def test_has_few_shot_section(self):
        assert "## Few-Shot 示例" in self.content

    def test_system_prompt_has_decision_tree(self):
        system = extract_code_block(self.content, "## 系统提示词")
        assert "第一优先级" in system
        assert "第二优先级" in system
        assert "第三优先级" in system

    def test_system_prompt_has_boundary_definitions(self):
        system = extract_code_block(self.content, "## 系统提示词")
        assert "关键边界" in system

    def test_system_prompt_has_output_format(self):
        system = extract_code_block(self.content, "## 系统提示词")
        assert "category" in system
        assert "reason" in system
        assert "category" in system

    def test_few_shot_examples_parse_correctly(self):
        examples = parse_few_shot_examples(self.content)
        assert len(examples) >= 9

    def test_all_few_shot_reasons_under_15_chars(self):
        examples = parse_few_shot_examples(self.content)
        for user_input, cat, reason in examples:
            assert len(reason) <= 15, \
                f"Reason '{reason}' for '{user_input}' is {len(reason)} chars, max 15"

    def test_few_shot_has_boundary_cases(self):
        """Verify at least one 'information lookup' boundary case exists."""
        examples = parse_few_shot_examples(self.content)
        boundaries = [e for e in examples if "查" in e[0] and "百度" in e[0] and e[1] == "simple_chat"]
        assert len(boundaries) >= 1, "Missing boundary case: using Baidu to search info should be simple_chat"


# ========== 默认参数测试 ==========

class TestClassifierDefaults:
    def test_deterministic_parameters(self):
        clf = IntentClassifier()
        assert clf.temperature == 0.0, "temperature should be 0 for deterministic output"
        assert clf.top_p == 0.01, "top_p should be 0.01 for deterministic output"
        assert clf.max_tokens == 128, "max_tokens should be 128 for short JSON output"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
