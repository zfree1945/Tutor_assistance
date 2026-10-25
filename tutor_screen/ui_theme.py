"""Shared page chrome; native widget colors live in .streamlit/config.toml."""
from pathlib import Path

import streamlit as st


def apply_theme() -> None:
    """Add the approved card/sidebar layout without replacing native controls."""
    st.html(Path(__file__).with_suffix(".css"))


def sidebar_brand() -> None:
    st.html('<div class="sidebar-brand"><strong>家教筛单器</strong></div>'
            '<p class="sidebar-tagline">本地筛单 · 轻松找到合适订单</p>')
