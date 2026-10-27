"""A source handoff must contain all application resources and no runtime data."""
import ast
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_source_snapshot_has_every_local_import_and_frontend_resource():
    for path in [ROOT / "app.py", *(ROOT / "tutor_screen").glob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 1:
                module = node.module
            elif node.module and node.module.startswith("tutor_screen."):
                module = node.module.removeprefix("tutor_screen.")
            else:
                continue
            if module:
                assert (ROOT / "tutor_screen" / (module.replace(".", "/") + ".py")).is_file(), (path, module)
    for name in ("map_frontend.js", "map_frontend.css", "origin_search.js", "origin_search.css", "ui_theme.css"):
        assert (ROOT / "tutor_screen" / name).is_file(), name


def test_tracked_snapshot_excludes_credentials_orders_and_generated_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    )
    paths = result.stdout.decode("utf-8").split("\0")[:-1]
    assert "app.py" in paths
    forbidden = {".local", ".venv", "artifacts", "__pycache__", ".pytest_cache", "exports", "runtime", "dist", "build"}
    for name in paths:
        path = Path(name)
        assert not forbidden.intersection(path.parts), name
        assert not path.name.startswith(".env"), name
        assert path.name != "secrets.toml", name
        assert path.suffix.lower() not in {".dpapi", ".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".zip"}, name


def test_published_order_fixtures_use_fictional_places():
    from title_order_samples import EXPECTED as titles
    from wz_schedule_samples import EXPECTED as schedules
    from test_fuzhou_landmark_search import CASES

    addresses = [row["address"] for row in [*titles, *schedules]] + [row[2] for row in CASES]
    assert all(any(marker in value for marker in ("合成", "示例", "示范", "九十九中")) for value in addresses)


def test_source_and_portable_build_identify_the_new_version():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    build = (ROOT / "scripts/build_portable.py").read_text(encoding="utf-8")
    assert readme.startswith("# 家教筛单器 v1.0.3\n")
    assert '"application_version": "1.0.3"' in build
    assert "TutorAssistant-v1.0.3" in build and "v1.0.2" not in build
    assert "docs/CHANGELOG_V1.0.3.md" in readme
    assert 'st.caption("v1.0.3 · 本地解析与筛选")' in (ROOT / "app.py").read_text(encoding="utf-8")
