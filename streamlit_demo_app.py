"""Read-only demo entry point for Streamlit Community Cloud.

Use this only when you want a lightweight public demo that never crawls sites
and only reads demo_data/lumitrack_demo.sqlite.
"""

from __future__ import annotations

import os

os.environ["LUMITRACK_DEMO_MODE"] = "1"

import app  # noqa: E402,F401
