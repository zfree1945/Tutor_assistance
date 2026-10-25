"""Run tests without requiring Git metadata or a fixed virtualenv location."""
import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--without-browser", action="store_true", help="Skip browser test files")
    options, pytest_args = parser.parse_known_args()
    (ROOT / "artifacts").mkdir(exist_ok=True)
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short"]
    if options.without_browser:
        command.append("--ignore-glob=*browser*.py")
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    return subprocess.call([*command, *pytest_args], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
