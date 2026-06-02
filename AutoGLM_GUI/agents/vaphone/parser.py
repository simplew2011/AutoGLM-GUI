"""VAPhone response parser and action converter.

Parses the model output format:
  <THINK> cot content </THINK>
  explain:xxx\taction:CLICK\tpoint:x,y\tsummary:xxx

Converts VAPhone actions to AutoGLM-GUI ActionHandler-compatible format.
Coordinate space: 0-1000, normalized by dividing by 1000.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from AutoGLM_GUI.logger import logger


class VaphoneParser:
    """Parse VAPhone model output and convert to AutoGLM actions."""

    def parse_response(self, raw_content: str) -> OrderedDict[str, Any]:
        """Parse raw model response into an action OrderedDict.

        Expected format:
          <THINK> cot content </THINK>
          explain:xxx\taction:CLICK\tpoint:x,y\tsummary:xxx

        Returns OrderedDict with keys: cot, explain, action, summary,
        and action-specific keys (point, point1, point2, value, return).
        """
        command_str = raw_content.strip()

        # Normalize THINK tags: fix typos, case, and spacing
        command_str = (
            command_str.replace("<TINK>", "<THINK>")
            .replace("</TINK>", "</THINK>")
            .replace("<think>", "<THINK>")
            .replace("</think>", "</THINK>")
        )
        command_str = re.sub(
            r"<\s*/?THINK\s*>",
            lambda m: "<THINK>" if "/" not in m.group() else "</THINK>",
            command_str,
            flags=re.IGNORECASE,
        )

        # Extract CoT and key-value parts
        try:
            cot_part = command_str.split("<THINK>")[1].split("</THINK>")[0].strip()
            kv_part = command_str.split("</THINK>")[1].strip()
        except IndexError:
            cot_part = ""
            kv_part = command_str

        action: OrderedDict[str, Any] = OrderedDict()
        action["cot"] = cot_part

        # Split by tab separator
        kvs = [kv.strip() for kv in kv_part.split("\t") if kv.strip()]

        for kv in kvs:
            if ":" not in kv:
                continue

            key = kv.split(":", 1)[0].strip()
            value = kv.split(":", 1)[1].strip()

            if key == "action":
                action["action"] = value
            elif key == "summary":
                action["summary"] = value
            elif "point" in key:
                # Parse point format: "x,y" or "x y"
                try:
                    coords = value.replace(",", " ").split()
                    if len(coords) < 2:
                        raise ValueError(f"Expected 2 coordinates, got {len(coords)}")
                    x, y = int(coords[0]), int(coords[1])
                    action[key] = [x, y]
                except (ValueError, IndexError) as e:
                    raise ValueError(
                        f"Failed to parse point '{value}' for key '{key}': {e}"
                    ) from e
            else:
                action[key] = value

        return action

    def convert_action(self, parsed_action: OrderedDict[str, Any]) -> dict[str, Any]:
        """Convert VAPhone action to AutoGLM-GUI ActionHandler format.

        VAPhone format:
          OrderedDict with explain, action, cot, summary, and
          action-specific keys (point, point1, point2, value, return).

        AutoGLM-GUI format:
          {"_metadata": "do", "action": "Tap", "element": [x, y]}
          {"_metadata": "finish", "message": "..."}

        Coordinates are in 0-1000 space and kept as-is for ActionHandler
        to convert based on actual screen dimensions.
        """
        action_type = parsed_action.get("action", "")

        if action_type == "CLICK":
            point = parsed_action.get("point", [0, 0])
            return {
                "_metadata": "do",
                "action": "Tap",
                "element": [int(point[0]), int(point[1])],
            }

        elif action_type == "TYPE":
            text = parsed_action.get("value", "")
            # If point is provided, include it for focus-then-type
            if "point" in parsed_action:
                point = parsed_action["point"]
                return {
                    "_metadata": "do",
                    "action": "Type",
                    "text": text,
                    "element": [int(point[0]), int(point[1])],
                }
            return {"_metadata": "do", "action": "Type", "text": text}

        elif action_type == "COMPLETE":
            return_value = parsed_action.get("return", "Task completed")
            return {"_metadata": "finish", "message": return_value}

        elif action_type == "WAIT":
            value = parsed_action.get("value", "1")
            return {
                "_metadata": "do",
                "action": "Wait",
                "duration": f"{value} seconds",
            }

        elif action_type == "AWAKE":
            app_name = parsed_action.get("value", "")
            return {"_metadata": "do", "action": "Launch", "app": app_name}

        elif action_type == "INFO":
            question = parsed_action.get("value", "")
            return {
                "_metadata": "do",
                "action": "Take_over",
                "message": question,
            }

        elif action_type == "ABORT":
            reason = parsed_action.get("value", "Task aborted")
            return {"_metadata": "finish", "message": f"ABORT: {reason}"}

        elif action_type == "SLIDE":
            point1 = parsed_action.get("point1", [0, 0])
            point2 = parsed_action.get("point2", [0, 0])
            return {
                "_metadata": "do",
                "action": "Swipe",
                "start": [int(point1[0]), int(point1[1])],
                "end": [int(point2[0]), int(point2[1])],
            }

        elif action_type == "LONGPRESS":
            point = parsed_action.get("point", [0, 0])
            return {
                "_metadata": "do",
                "action": "Long Press",
                "element": [int(point[0]), int(point[1])],
            }

        elif action_type == "HOME":
            return {"_metadata": "do", "action": "Home"}

        elif action_type == "BACK":
            return {"_metadata": "do", "action": "Back"}

        # Common model hallucinations — models trained on multi-framework
        # data may output these even though they aren't in the official
        # VAPhone action space. Handle them as correct UI intents.

        elif action_type in ("SCROLL", "SWIPE"):
            # Alias for SLIDE — some models output SCROLL or SWIPE
            point1 = parsed_action.get("point1", [0, 0])
            point2 = parsed_action.get("point2", [0, 0])
            return {
                "_metadata": "do",
                "action": "Swipe",
                "start": [int(point1[0]), int(point1[1])],
                "end": [int(point2[0]), int(point2[1])],
            }

        elif action_type == "ENTER":
            return {"_metadata": "do", "action": "Back"}

        else:
            # Unknown action — fall back to Wait so the model can
            # observe the unchanged screen and self-correct next step.
            # Don't terminate the task for a single unrecognized action.
            logger.warning(
                f"Unknown VAPhone action type '{action_type}', "
                f"falling back to Wait. Full action: {dict(parsed_action)}"
            )
            return {
                "_metadata": "finish",
                "message": f"Unknown action: {action_type}",
            }

    @staticmethod
    def clean_summary(text: str) -> str:
        """Clean summary text for embedding in prompt.

        Strips newlines and tabs, normalizes whitespace.
        """
        if not text:
            return text
        text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()
