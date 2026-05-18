import re
import json
from typing import Any


def extract_code_block(markdown: str, heading: str) -> str:
    """Extract the first code block content under a given markdown heading."""
    idx = markdown.find(heading)
    if idx == -1:
        return ""
    section = markdown[idx:]
    match = re.search(r"```\n(.*?)```", section, re.DOTALL)
    return match.group(1) if match else ""


def parse_few_shot_examples(content: str) -> list[tuple[str, str, str]]:
    """Parse few-shot examples from prompt markdown into (user_input, category, reason) tuples."""
    section = extract_code_block(content, "## Few-Shot 示例")
    if not section:
        return []

    examples = []
    lines = section.strip().split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("用户输入:"):
            user_input = line.split("用户输入:", maxsplit=1)[1].strip().strip('"\'')
            # 下一行应该是 JSON 响应
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("{"):
                try:
                    resp = json.loads(lines[i + 1].strip())
                    examples.append((
                        user_input,
                        resp.get("category", ""),
                        resp.get("reason", ""),
                    ))
                except json.JSONDecodeError:
                    pass
    return examples


_JSON_PATTERN = re.compile(r"\{[^{}]*\}", re.DOTALL)

def extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    match = _JSON_PATTERN.search(text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None