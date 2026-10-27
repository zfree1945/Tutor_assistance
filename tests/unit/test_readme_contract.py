"""Ensure the public README points to the actual application and local docs."""
import ast
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]


def test_readme_matches_pages_modules_documentation_and_license_state():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://github.com/zfree1945/Tutor_assistance/releases/download/v1.0.3/%E5%AE%B6%E6%95%99%E7%AD%9B%E5%8D%95%E5%99%A8-v1.0.3-Windows-x64.zip" in readme
    assert "https://github.com/zfree1945/Tutor_assistance/releases/tag/v1.0.3" in readme
    assert "/releases/download/v1.0.2/" not in readme
    assert not list(ROOT.glob("*.zip"))
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    pages = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id == "PAGES" for name in node.targets))
    assert all(page in readme for page in pages)
    for name in ("pipeline", "segmentation", "local_parser", "matching", "maps", "map_ui", "map_component", "privacy", "export"):
        assert (ROOT / "tutor_screen" / f"{name}.py").is_file()
        assert f"{name}.py" in readme
    for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme):
        parsed = urlsplit(link)
        if not parsed.scheme and parsed.path:
            assert (ROOT / parsed.path).is_file(), link
    license_file = ROOT / "LICENSE"
    assert "MIT" in readme and "(LICENSE)" in readme
    assert "Copyright (c) 2026 Zheng Fengyi" in license_file.read_text(encoding="utf-8")
    assert hashlib.sha256(license_file.read_bytes()).hexdigest() == "a17e9846dc2f3a46344306be211a5da89bdf840b01c15a7bec034acf5343371c"
