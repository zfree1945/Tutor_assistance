"""Regression checks for the portable, publishable project layout."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("project_check", ROOT / "scripts/check_project.py")
project_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project_check)


def test_application_snapshot_resources_and_documentation_are_complete():
    assert project_check.check_project(ROOT) == []


def test_publishing_check_detects_copied_private_runtime_data(tmp_path):
    private = tmp_path / ".local"
    private.mkdir()
    (private / "order_history.sqlite3").write_bytes(b"SYNTHETIC-PRIVATE-DATA")
    metadata = tmp_path / ".git"
    metadata.mkdir()
    (metadata / "ignored_metadata.py").write_text("invalid Python ???", encoding="utf-8")
    errors = project_check.check_project(tmp_path, strict=True)
    assert any(".local" in error for error in errors)
    assert not any(error.endswith(": .git") or "ignored_metadata.py" in error for error in errors)


def test_documentation_check_detects_missing_and_outside_links(tmp_path):
    (tmp_path / "README.md").write_text("[missing](docs/missing.md)\n[outside](../private.txt)", encoding="utf-8")
    errors = project_check.check_project(tmp_path)
    assert any("docs/missing.md" in error for error in errors)
    assert any("../private.txt" in error for error in errors)


def test_launch_scripts_work_without_git_from_another_directory(tmp_path):
    for script, arguments, expected in [
        ("run_tests.py", ["--collect-only", "tests/unit/test_sidebar_brand.py"], "1 test collected"),
        ("start_app.py", ["--help"], "Usage:"),
    ]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *arguments],
            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert expected in result.stdout
    assert not (tmp_path / ".git").exists()
