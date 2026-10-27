"""Check the publishable source tree, Python syntax and local doc links."""
import argparse
import ast
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {".git", ".venv", "venv", ".local", "__pycache__", ".pytest_cache", "artifacts"}
REQUIRED = (
    "app.py", "README.md", "LICENSE", "requirements.txt", "requirements-dev.txt", "pytest.ini",
    "快速开始.bat", "scripts/start_app.py",
    ".gitignore", ".streamlit/config.toml", "tutor_screen/__init__.py",
    "tutor_screen/map_frontend.js", "tutor_screen/map_frontend.css", "tutor_screen/ui_theme.css",
    "docs/ARCHITECTURE.md", "docs/TESTING.md", "docs/DATA.md", "docs/MAPS_V1.0.2.md",
    "docs/AREA_MAP_V1.0.2.md", "docs/AUTO_MAP_V1.0.2.md", "docs/TEST_PLAN.md", "docs/UI_V1.0.2.md",
    "docs/PUBLICATION_CHECKLIST.md",
    "tests/unit", "tests/acceptance", "tests/regression",
)


def check_project(root: Path, *, strict: bool = False) -> list[str]:
    root = root.resolve()
    errors = [f"Missing required path: {name}" for name in REQUIRED if not (root / name).exists()]
    for name in (".local", ".env", ".streamlit/secrets.toml"):
        if (root / name).exists():
            errors.append(f"Private or repository-local path must not be in the source snapshot: {name}")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if strict and path.is_dir() and path.name in IGNORED_DIRS and path.name != ".git":
            errors.append(f"Generated directory in clean snapshot: {relative.as_posix()}")
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if not path.is_file():
            continue
        if (path.name.startswith(".env") or path.suffix in {".dpapi", ".sqlite", ".sqlite3", ".db", ".log"}
                or path.name.startswith("导入日志_")):
            errors.append(f"Runtime data or credentials in snapshot: {relative.as_posix()}")
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(relative))
            except (SyntaxError, UnicodeError) as exc:
                errors.append(f"Invalid Python source: {relative.as_posix()}: {exc}")
        if path.suffix == ".md":
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                target = target.strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                destination = (path.parent / unquote(parsed.path)).resolve()
                if not destination.is_relative_to(root) or not destination.exists():
                    errors.append(f"Broken local link in {relative.as_posix()}: {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Also reject environments, caches and artifacts")
    args = parser.parse_args()
    errors = check_project(ROOT, strict=args.strict)
    if errors:
        print("\n".join(errors))
        return 1
    print("Project structure, Python syntax and local documentation links: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
