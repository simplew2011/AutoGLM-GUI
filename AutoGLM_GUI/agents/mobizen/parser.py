"""MobiZen-GUI response parser and action converter.

Parses the model output format:
  Thought: <reasoning>
  Action: <action description>
  <tool_call>
  {"name": "mobile_use", "arguments": {...}}
  </tool_call>

Converts MobiZen actions to AutoGLM-GUI ActionHandler-compatible format.
"""

import json
import re
from typing import Any


class MobiZenParser:
    """Parse MobiZen model output and convert actions."""

    def parse_response(self, raw_content: str) -> tuple[str, str, dict[str, Any]]:
        """Parse raw model response into (thinking, action_description, action_dict).

        Args:
            raw_content: Full raw model output text.

        Returns:
            (thinking, subtask, action_dict) where action_dict is
            {"arguments": {"action": "...", "coordinate": [...], ...}}
        """
        thought = ""
        subtask = ""
        action: dict[str, Any] = {}

        # Extract thought (text before "Action")
        try:
            if "\nAction" in raw_content:
                thought = (
                    raw_content.split("\nAction")[0]
                    .replace("Thought", "")
                    .replace(":", "")
                    .replace("：", "")
                    .strip()
                )
            else:
                thought = raw_content.split("<tool_call>")[0].strip()
        except Exception:
            pass

        # Extract action description (between "Action" and "<tool_call>")
        try:
            if "\nAction" in raw_content:
                subtask = (
                    raw_content.split("\nAction")[-1]
                    .replace(":", "")
                    .replace("：", "")
                    .replace('"', "")
                    .split("\n<tool_call>")[0]
                    .strip('"')
                    .strip()
                )
        except Exception:
            pass

        # Extract tool_call JSON
        try:
            if "<tool_call>" in raw_content and "</tool_call>" in raw_content:
                action_str = (
                    raw_content.split("<tool_call>")[1]
                    .split("</tool_call>")[0]
                    .strip()
                    .replace("'", '"')
                )
                # Use regex to find the JSON object (handle nested braces)
                pattern = r"\{.*?\}}"
                result = re.search(pattern, action_str, re.DOTALL)
                if result:
                    action_str = result.group()
                action = json.loads(action_str)
        except (json.JSONDecodeError, IndexError, AttributeError):
            action = {}

        return thought, subtask, action

    def convert_action(self, mobizen_action: dict[str, Any]) -> dict[str, Any]:
        """Convert MobiZen action dict to AutoGLM-GUI format.

        MobiZen format:
          {"arguments": {"action": "click", "coordinate": [x, y], ...}}

        AutoGLM-GUI format:
          {"_metadata": "do", "action": "Tap", "element": [x, y]}
          {"_metadata": "finish", "message": "..."}

        Args:
            mobizen_action: Action dict in MobiZen format.

        Returns:
            Action dict compatible with ActionHandler.execute().
        """
        args = mobizen_action.get("arguments", {})
        action_type = args.get("action", "")

        if action_type == "click":
            coord = args.get("coordinate", [0, 0])
            return {
                "_metadata": "do",
                "action": "Tap",
                "element": [int(coord[0]), int(coord[1])],
            }

        elif action_type == "long_press":
            coord = args.get("coordinate", [0, 0])
            return {
                "_metadata": "do",
                "action": "Long Press",
                "element": [int(coord[0]), int(coord[1])],
            }

        elif action_type == "swipe":
            start = args.get("coordinate", [0, 0])
            end = args.get("coordinate2", [0, 0])
            return {
                "_metadata": "do",
                "action": "Swipe",
                "start": [int(start[0]), int(start[1])],
                "end": [int(end[0]), int(end[1])],
            }

        elif action_type == "type":
            text = args.get("text", "")
            return {"_metadata": "do", "action": "Type", "text": text}

        elif action_type == "system_button":
            button = args.get("button", "")
            if button == "Back":
                return {"_metadata": "do", "action": "Back"}
            elif button == "Home":
                return {"_metadata": "do", "action": "Home"}
            else:
                # Menu, Enter etc. - fallback to Back
                return {"_metadata": "do", "action": "Back"}

        elif action_type == "wait":
            wait_time = args.get("time", 1)
            return {
                "_metadata": "do",
                "action": "Wait",
                "duration": f"{wait_time} seconds",
            }

        elif action_type == "answer":
            text = args.get("text", "Task completed")
            return {"_metadata": "finish", "message": text}

        elif action_type == "terminate":
            status = args.get("status", "success")
            return {"_metadata": "finish", "message": status}

        else:
            return {
                "_metadata": "finish",
                "message": f"Unknown action: {action_type}",
            }

    @staticmethod
    def format_assistant_response(
        thought: str, subtask: str, mobizen_action: dict[str, Any]
    ) -> str:
        """Format the assistant message for context history.

        Builds the expected format:
          Thought: <thought>
          Action: <subtask>
          <tool_call>
          <json>
          </tool_call>
        """
        tool_call_json = json.dumps(mobizen_action, ensure_ascii=False)
        return (
            f"Thought: {thought}\n"
            f"Action: {subtask}\n"
            f"<tool_call>\n"
            f"{tool_call_json}\n"
            f"</tool_call>"
        )
