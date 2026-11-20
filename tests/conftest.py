from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# the app's modules live at the repository root
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
# no system tray in tests (and no display needed to import the GUI module)
os.environ.setdefault("PYSTRAY_BACKEND", "dummy")
# the app finds its own folder (settings, language files) from the started script
sys.argv[0] = str(REPO / "main.py")


def load_fixture(name: str) -> Any:
    with (FIXTURES / name).open(encoding="utf8") as file:
        return json.load(file)
