"""Startup prepares only its own environment and preserves user credentials."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("app_launcher", ROOT / "scripts/start_app.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "项目 with spaces"
    root.mkdir()
    (root / "app.py").write_text("", encoding="utf-8")
    (root / "requirements.txt").write_text("streamlit==1.64.0\n", encoding="utf-8")
    (root / ".local").mkdir()
    (root / ".local/model_config.dpapi").write_bytes(b"SYNTHETIC-UNCHANGED")
    return root


@pytest.fixture
def commands(monkeypatch):
    calls = []

    def run(command, **options):
        calls.append(command)
        assert options["cwd"].is_absolute()
        if command[1:3] == ["-m", "venv"]:
            python = Path(command[3]) / "Scripts/python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    return calls


def test_first_launch_creates_environment_installs_dependencies_and_keeps_credentials(project, commands):
    python = launcher.prepare_environment(project)
    assert python == project / ".venv/Scripts/python.exe"
    assert commands[0][1:3] == ["-m", "venv"]
    assert commands[1][1:5] == ["-m", "pip", "install", "-r"]
    assert (project / ".local/model_config.dpapi").read_bytes() == b"SYNTHETIC-UNCHANGED"


def test_next_launch_reuses_dependencies_but_changed_requirements_reinstall(project, commands):
    launcher.prepare_environment(project)
    commands.clear()
    launcher.prepare_environment(project)
    assert len(commands) == 1 and commands[0][1] == "-c"
    (project / "requirements.txt").write_text("streamlit==1.64.0\nhttpx\n", encoding="utf-8")
    commands.clear()
    launcher.prepare_environment(project)
    assert len(commands) == 1 and commands[0][1:4] == ["-m", "pip", "install"]


def test_failed_install_is_not_marked_successful(project, monkeypatch):
    python = project / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["pip"])

    monkeypatch.setattr(launcher.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        launcher.prepare_environment(project)
    assert not (project / ".venv/.tutor_requirements.sha256").exists()


def test_missing_dependency_is_repaired_even_when_requirements_unchanged(project, commands, monkeypatch):
    launcher.prepare_environment(project)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1 if command[1] == "-c" else 0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.prepare_environment(project)
    assert len(calls) == 2 and calls[-1][1:4] == ["-m", "pip", "install"]


def test_launch_passes_streamlit_options_and_runs_from_project_directory(project, commands, monkeypatch):
    monkeypatch.setattr(launcher, "ROOT", project)
    assert launcher.main(["--server.port=8523", "--server.headless=true"]) == 0
    assert commands[-1][1:4] == ["-m", "streamlit", "run"]
    assert commands[-1][-2:] == ["--server.port=8523", "--server.headless=true"]


@pytest.mark.skipif(os.name != "nt", reason="Windows double-click entry point")
def test_batch_entry_runs_from_another_directory_with_unicode_and_spaces(project, tmp_path):
    shutil.copy2(ROOT / "快速开始.bat", project / "快速开始.bat")
    (project / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/start_app.py", project / "scripts/start_app.py")
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(project / "快速开始.bat"), "--help"],
        cwd=tmp_path, capture_output=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout
    assert not (project / ".venv").exists()
