from dataclasses import dataclass
from typing import Literal


@dataclass
class IntentResult:
    mode: Literal["classic", "layered", "chat"]
