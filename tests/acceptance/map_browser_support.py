"""Offline browser harness: copy source only and deny every real service call.

The fake SDK exercises our component's JavaScript contract. It does not claim to
validate AMap's cartography, production availability, entitlement or quotas.
"""
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]


def start_isolated_server(tmp_path, wrapper):
    project = tmp_path / "maps_browser_project"
    project.mkdir()
    shutil.copy2(ROOT / "app.py", project / "app.py")
    shutil.copytree(
        ROOT / "tutor_screen", project / "tutor_screen",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # Theme configuration is part of the app UI. Copy only the public config,
    # never the .streamlit directory (which may contain real secrets.toml).
    theme_config = ROOT / ".streamlit" / "config.toml"
    if theme_config.exists():
        (project / ".streamlit").mkdir()
        shutil.copy2(theme_config, project / ".streamlit" / "config.toml")
    (project / "wrapper.py").write_text(wrapper, encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = None
    log = (tmp_path / "maps_server.log").open("wb")

    def stop():
        nonlocal process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def start():
        nonlocal process
        stop()
        process = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "wrapper.py",
             "--server.address=127.0.0.1", f"--server.port={port}",
             "--server.headless=true", "--browser.gatherUsageStats=false"],
            cwd=project, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("The isolated maps test server exited before startup")
            try:
                with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as reply:
                    if reply.status == 200:
                        return f"http://127.0.0.1:{port}"
            except OSError:
                pass
            time.sleep(0.2)
        pytest.fail("The isolated maps test server did not become ready")

    return project, start, stop, log


def deny_external_requests(context, *, sdk_source=None):
    """Only localhost is networked; supply the SDK as a local response."""
    blocked = []

    def route_request(route):
        url = route.request.url
        if url.startswith(("http://127.0.0.1:", "ws://127.0.0.1:", "data:", "blob:")):
            route.continue_()
        elif sdk_source is not None and url.startswith("https://webapi.amap.com/maps"):
            route.fulfill(status=200, content_type="application/javascript", body=sdk_source)
        else:
            blocked.append(url)
            route.abort()

    context.route("**/*", route_request)
    return blocked
