"""Debounced origin autocomplete; REST credentials stay on the server."""
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitAPIException

from tutor_screen.maps import AmapClient, MapAPIError

ASSETS = Path(__file__).parent
KEY = "map_origin_autocomplete"


def _register():
    return st.components.v2.component(
        "tutor_origin_search",
        html='<div class="origin-search"></div>',
        js=ASSETS.joinpath("origin_search.js").read_text(encoding="utf-8"),
        css=ASSETS.joinpath("origin_search.css").read_text(encoding="utf-8"),
    )


_COMPONENT = _register()


def query_changed():
    state = st.session_state
    query = str(state.get(KEY, {}).get("query", "")).strip()
    state.map_origin_query = query
    state.map_origin_candidates = []
    state.map_origin_selected = None
    state.map_origin_message = ""
    settings = state.map_settings
    if not query or not settings.credentials.web_key:
        return
    try:
        state.map_origin_candidates = AmapClient(settings.credentials.web_key).search(query, settings.default_city)
        if not state.map_origin_candidates:
            state.map_origin_message = "没有找到地点，请换个关键词。"
    except MapAPIError as exc:
        state.map_origin_message = str(exc)


def selection_changed():
    state = st.session_state
    selection = state.get(KEY, {}).get("selection")
    state.map_origin_selected = None
    if not isinstance(selection, dict) or selection.get("query") != state.get("map_origin_query", ""):
        return
    index = selection.get("index")
    candidates = state.get("map_origin_candidates", [])
    if type(index) is int and 0 <= index < len(candidates):
        state.map_origin_selected = candidates[index]


def render_origin_search():
    state = st.session_state
    settings = state.map_settings
    # Requery an existing keyword after saving a different city/key.
    scope = (settings.default_city, settings.credentials.web_key)
    if state.get("_origin_search_scope") != scope:
        state._origin_search_scope = scope
        query_changed()
    data = {
        "query": state.get("map_origin_query", ""),
        "options": [point.label + (f" · {point.city}" if point.city else "") for point in state.map_origin_candidates],
        "selected": state.map_origin_selected.label if state.get("map_origin_selected") else "",
        "message": state.get("map_origin_message", ""),
        "disabled": not bool(settings.credentials.web_key),
    }
    kwargs = dict(key=KEY, data=data, default={"query": "", "selection": None},
                  on_query_change=query_changed, on_selection_change=selection_changed,
                  width="stretch", height="content")
    try:
        _COMPONENT(**kwargs)
    except StreamlitAPIException as exc:
        if "is not registered" not in str(exc):
            raise
        _register()(**kwargs)
    if not settings.credentials.web_key:
        st.caption("请先保存 Web 服务 API Key，再搜索出发点。")
    return state.get("map_origin_selected")
