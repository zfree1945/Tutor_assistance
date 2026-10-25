"""Only report the desktop breakpoint; card sizing remains responsive CSS."""
import streamlit as st
from streamlit.errors import StreamlitAPIException


def _register():
    return st.components.v2.component(
        "results_viewport", html="<span></span>",
        js="""const instances = new WeakMap();
        export default function({parentElement, data, setStateValue}) {
          let state = instances.get(parentElement);
          if (!state) {
            state = {query: matchMedia('(min-width:1200px)'), value: data.desktop};
            state.report = () => {
              if (state.query.matches !== state.value) {
                state.value = state.query.matches;
                state.emit('desktop', state.value);
              }
            };
            state.cleanup = () => {
              state.query.removeEventListener('change', state.report);
              instances.delete(parentElement);
            };
            state.query.addEventListener('change', state.report);
            instances.set(parentElement, state);
          }
          state.emit = setStateValue;
          state.report();
          return state.cleanup;
        }""",
    )


_VIEWPORT = _register()


def desktop_results() -> bool:
    state = st.session_state.get("results_viewport", {})
    desktop = state.get("desktop", False) is True
    with st.container(key="results_viewport_probe"):
        arguments = dict(key="results_viewport", data={"desktop": desktop},
                         default={"desktop": False}, on_desktop_change=lambda: None,
                         height=0, width="stretch")
        try:
            result = _VIEWPORT(**arguments)
        except StreamlitAPIException as error:
            if "is not registered" not in str(error):
                raise
            result = _register()(**arguments)
    return result.desktop is True
