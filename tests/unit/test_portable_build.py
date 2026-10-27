"""Packaging uses public inputs and wheel inventories, never a user's live tree."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("portable_build", ROOT / "scripts/build_portable.py")
portable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portable)


def test_runtime_requirements_exclude_dev_tools_and_platform_extras(tmp_path):
    file = tmp_path / "requirements.txt"
    file.write_text('# comment\nstreamlit==1.64.0\npytest\nplaywright\nhttpx; python_version < "3.0"\n', encoding="utf-8")
    assert [item.name for item in portable.runtime_requirements(file)] == ["streamlit"]


def test_dependency_closure_includes_requested_extras_only():
    data = {
        "root": SimpleNamespace(version="1.0", requires=['child[feature]>=1', 'unwanted; extra == "other"']),
        "child": SimpleNamespace(version="1.0", requires=['optional; extra == "feature"']),
        "optional": SimpleNamespace(version="1.0", requires=[]),
    }
    assert set(portable.dependency_closure([Requirement("root")], data.__getitem__)) == {"root", "child", "optional"}


def test_repeated_dependency_constraints_are_all_validated():
    distribution = SimpleNamespace(version="1.0", requires=[])
    with pytest.raises(ValueError, match="version"):
        portable.dependency_closure([Requirement("child<1"), Requirement("child>=1")], lambda _: distribution)


def test_copy_application_ignores_live_credentials_photos_and_local_environment(tmp_path):
    source, output = tmp_path / "source", tmp_path / "bundle/app"
    for name in ("tutor_screen", ".streamlit", ".local", ".venv"):
        (source / name).mkdir(parents=True)
    (source / "app.py").write_text("print('app')", encoding="utf-8")
    (source / "tutor_screen/map_frontend.js").write_text("// public", encoding="utf-8")
    (source / ".streamlit/config.toml").write_text('[theme]\nbase="light"\n', encoding="utf-8")
    for name in (".local/config.dpapi", ".streamlit/secrets.toml", ".env", "personal.jpg", ".venv/python.exe"):
        (source / name).write_bytes(b"SYNTHETIC-PRIVATE")
    portable.copy_application(source, output)
    assert sorted(str(path.relative_to(output)).replace("\\", "/") for path in output.rglob("*") if path.is_file()) == [
        ".streamlit/config.toml", "app.py", "tutor_screen/map_frontend.js",
    ]


def test_copy_distribution_omits_tests_bytecode_local_urls_and_external_scripts(tmp_path):
    source, destination = tmp_path / "site", tmp_path / "out"
    entries = ["package/__init__.py", "package/tests/test_it.py", "package/__pycache__/cache.pyc", "package-1.dist-info/LICENSE", "package-1.dist-info/direct_url.json", "../Scripts/tool.exe"]
    for entry in entries:
        path = source / entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data", encoding="utf-8")
    distribution = SimpleNamespace(files=entries, locate_file=lambda path: source / path)
    assert portable.copy_distribution(distribution, destination) == 2
    assert (destination / "package-1.dist-info/LICENSE").exists()
    assert not (destination / "package/tests").exists()


def test_bundle_validation_rejects_private_data(tmp_path):
    (tmp_path / ".local").mkdir()
    with pytest.raises(ValueError, match="Private"):
        portable.validate_bundle(tmp_path)


def test_archive_keeps_one_root_and_refuses_overwrite(tmp_path):
    root = tmp_path / "App"
    for name in ("runtime/python.exe", "runtime/python312._pth", "app/app.py", "app/tutor_screen/map_frontend.js", "快速开始.bat", "LICENSE"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public", encoding="utf-8")
    archive = tmp_path / "release.zip"
    portable.create_archive(root, archive)
    with zipfile.ZipFile(archive) as content:
        assert all(name.startswith("App/") for name in content.namelist())
    with pytest.raises(FileExistsError):
        portable.create_archive(root, archive)


def test_portable_entry_uses_only_bundled_python():
    assert '"..\\runtime\\python.exe" -m streamlit' in portable.BAT
    assert "pip install" not in portable.BAT
    assert "py -3.12" not in portable.BAT
    assert "--server.address=127.0.0.1" in portable.BAT


def test_build_release_contains_application_license_and_no_saved_user_data(tmp_path, monkeypatch):
    source = tmp_path / "source"
    for name in ("tutor_screen", ".streamlit", ".local"):
        (source / name).mkdir(parents=True)
    (source / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    (source / "app.py").write_text("# application", encoding="utf-8")
    (source / "tutor_screen/map_frontend.js").write_text("// public", encoding="utf-8")
    (source / ".streamlit/config.toml").write_text('[theme]\nbase="light"', encoding="utf-8")
    (source / "requirements.txt").write_text("", encoding="utf-8")
    (source / ".local/current_batch.json").write_text('"SYNTHETIC-PRIVATE"', encoding="utf-8")
    monkeypatch.setattr(portable, "dependency_closure", lambda _: {})
    def runtime(_base, destination):
        destination.mkdir()
        (destination / "python.exe").write_bytes(b"synthetic runtime")
        (destination / "python312._pth").write_text("../app", encoding="ascii")
    monkeypatch.setattr(portable, "copy_python", runtime)
    archive = portable.build(source, tmp_path / "release")
    assert archive.name == "TutorAssistant-v1.0.3-Windows-x64.zip"
    with zipfile.ZipFile(archive) as content:
        prefix = "TutorAssistant-v1.0.3/"
        assert content.read(prefix + "LICENSE") == (ROOT / "LICENSE").read_bytes()
        instructions = content.read(prefix + "使用说明.txt").decode("utf-8-sig")
        assert "MIT" in instructions and "尚未选择" not in instructions
        assert json.loads(content.read(prefix + "runtime_inventory.json"))["application_version"] == "1.0.3"
        assert not any(".local" in name for name in content.namelist())
