"""``card-grading-engine`` application package.

Puts the repository root on ``sys.path`` so the geometry modules under ``src/``
import cleanly whether the app is started as ``uvicorn app.main:app`` from the
project root or imported by a test.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__all__ = ["ROOT"]
