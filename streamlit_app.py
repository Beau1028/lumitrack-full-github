"""Streamlit entry point for the full LumiTrack app."""

from __future__ import annotations

import logging
import traceback

import streamlit as st


try:
    import app  # noqa: F401
except Exception as exc:
    logging.exception("LumiTrack failed while rendering the Streamlit app.")
    st.error("LumiTrack 화면을 그리는 중 오류가 발생했습니다.")
    st.caption(
        "이 화면이 보이면 흰 화면 대신 원인을 잡을 수 있습니다. "
        "아래 오류를 Codex에게 보내 주세요."
    )
    st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
