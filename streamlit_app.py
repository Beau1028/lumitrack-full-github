"""Streamlit entry point for the full LumiTrack app."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import traceback

import streamlit as st


try:
    import app  # noqa: F401
except Exception as exc:
    error_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    logging.exception("LumiTrack failed while rendering the Streamlit app.")
    try:
        app_home = Path(os.getenv("ESCAPE_ROOM_MONITOR_HOME", "."))
        log_dir = app_home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "render_error_latest.txt").write_text(
            error_text,
            encoding="utf-8",
        )
    except OSError:
        pass
    st.markdown(
        """
        <style>
        .block-container { max-width: 980px; padding-top: 2rem; }
        [data-testid="stAlert"] * { color: inherit !important; }
        pre, code { white-space: pre-wrap !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.error("LumiTrack 화면을 그리는 중 오류가 발생했습니다.")
    st.caption(
        "서버에 render_error_latest.txt 파일로도 저장했습니다. "
        "아래 오류가 안 보이면 hetzner_render_error.sh 결과를 보내 주세요."
    )
    st.code(error_text, language="text")
