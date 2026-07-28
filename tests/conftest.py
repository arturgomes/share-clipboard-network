"""Pytest bootstrap: make the vendored ClipCascade Desktop client's `src/`
importable as a plain Python path (mirrors how the vendored app itself runs --
see vendor/ClipCascade_Desktop/src/pyproject.toml `packages` list), without
installing it or touching the vendored tree.
"""

import sys
from pathlib import Path

VENDOR_SRC = (
    Path(__file__).resolve().parent.parent
    / "vendor"
    / "ClipCascade_Desktop"
    / "src"
)

if str(VENDOR_SRC) not in sys.path:
    sys.path.insert(0, str(VENDOR_SRC))
