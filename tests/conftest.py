from __future__ import annotations

import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

warnings.filterwarnings(
    "ignore",
    message="The 'app' shortcut is now deprecated.*",
    category=DeprecationWarning,
    module=r"httpx\\..*",
)
