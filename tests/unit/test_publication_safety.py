"""Exercise publication ignores with Git using an isolated temporary repository."""
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_git_ignores_credentials_databases_and_exports(tmp_path):
    shutil.copy2(ROOT / ".gitignore", tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    private_paths = [
        ".venv/python.exe", "__pycache__/module.pyc", ".pytest_cache/state",
        ".local/model_config.dpapi", ".local/current_batch.json", ".local/map_data.sqlite3",
        ".env", ".env.production", ".streamlit/secrets.toml", "output.log",
        "orders.sqlite3", "test/artifacts/export.csv", "test_v1.0.1/artifacts/screen.png",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"], cwd=tmp_path,
        input=b"\0".join(name.encode("utf-8") for name in private_paths) + b"\0",
        capture_output=True, check=True,
    )
    assert result.stdout.decode("utf-8").split("\0")[:-1] == private_paths


def test_git_keeps_public_application_configuration_and_tests(tmp_path):
    shutil.copy2(ROOT / ".gitignore", tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"], cwd=tmp_path,
        input=b"app.py\0requirements.txt\0.streamlit/config.toml\0tests/test_core.py\0",
        capture_output=True,
    )
    assert result.returncode == 1
    assert not result.stdout
