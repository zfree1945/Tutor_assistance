"""Build a clean Windows x64 app-local Python distribution from installed wheels."""
import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import tomllib
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
DEV_PACKAGES = {"pytest", "pytest-cov", "playwright", "pyinstaller"}
SKIP_PARTS = {"__pycache__", "tests", "test"}
PRIVATE_PARTS = {".local", ".venv", ".git", "artifacts", ".pytest_cache"}
BAT = r'''@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
cd /d "%~dp0app"
"..\runtime\python.exe" -m streamlit run app.py --server.address=127.0.0.1 --server.headless=false --browser.gatherUsageStats=false %*
if errorlevel 1 (
    echo.
    echo Startup stopped. Keep the entire extracted folder together and check the message above.
    pause
    exit /b 1
)
exit /b 0
'''


def runtime_requirements(path: Path) -> list[Requirement]:
    result = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.url:
            raise ValueError("Direct-URL dependencies are not allowed in the portable build.")
        if canonicalize_name(requirement.name) in DEV_PACKAGES:
            continue
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            result.append(requirement)
    return result


def dependency_closure(requirements, lookup=metadata.distribution):
    pending = list(requirements)
    visited = set()
    selected = {}
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        state = (name, frozenset(requirement.extras))
        distribution = lookup(requirement.name)
        if requirement.specifier and not requirement.specifier.contains(distribution.version):
            raise ValueError(f"Installed runtime version does not satisfy the requirement: {name}")
        if state in visited:
            continue
        visited.add(state)
        selected[name] = distribution
        for raw in distribution.requires or []:
            child = Requirement(raw)
            if child.marker and not any(child.marker.evaluate({"extra": extra}) for extra in ({""} | requirement.extras)):
                continue
            pending.append(child)
    return selected


def copy_application(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    shutil.copy2(source / "app.py", destination / "app.py")
    package = source / "tutor_screen"
    for file in package.rglob("*"):
        relative = file.relative_to(package)
        if not file.is_file() or "__pycache__" in relative.parts or file.suffix not in {".py", ".js", ".css"}:
            continue
        if not file.resolve().is_relative_to(package.resolve()):
            raise ValueError("Application source must not link outside its package.")
        output = destination / "tutor_screen" / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, output)
    theme = source / ".streamlit/config.toml"
    if set(tomllib.loads(theme.read_text(encoding="utf-8"))) != {"theme"}:
        raise ValueError("Only the public theme configuration may be packaged.")
    (destination / ".streamlit").mkdir()
    shutil.copy2(theme, destination / ".streamlit/config.toml")


def copy_distribution(distribution, destination: Path) -> int:
    base = Path(distribution.locate_file("")).resolve()
    count = 0
    if not distribution.files:
        raise ValueError("An installed runtime distribution has no file inventory.")
    for entry in distribution.files:
        relative = Path(str(entry))
        if relative.is_absolute() or ".." in relative.parts or any(part in SKIP_PARTS for part in relative.parts):
            continue
        if relative.suffix in {".pyc", ".pyo"} or relative.name in {"direct_url.json", "INSTALLER", "REQUESTED"}:
            continue
        file = Path(distribution.locate_file(entry)).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            continue
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, output)
        count += 1
    return count


def copy_python(base: Path, runtime: Path) -> None:
    runtime.mkdir()
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        shutil.copy2(base / name, runtime / name)
    shutil.copytree(base / "DLLs", runtime / "DLLs", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(
        base / "Lib", runtime / "Lib",
        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "test", "idlelib", "turtledemo", "tkinter", "ensurepip"),
    )
    (runtime / "Lib/site-packages").mkdir()
    (runtime / "python312._pth").write_text(
        ".\nLib\nDLLs\nLib/site-packages\n../app\nimport site\n", encoding="ascii",
    )


def validate_bundle(root: Path) -> None:
    for file in root.rglob("*"):
        relative = file.relative_to(root)
        if any(part in PRIVATE_PARTS for part in relative.parts):
            raise ValueError(f"Private or generated path in portable bundle: {relative.as_posix()}")
        if file.is_file() and (file.name.startswith(".env") or file.name == "secrets.toml" or file.suffix in {".sqlite", ".sqlite3", ".db", ".dpapi", ".pyc"}):
            raise ValueError(f"Private or generated file in portable bundle: {relative.as_posix()}")
    for name in ("runtime/python.exe", "runtime/python312._pth", "app/app.py", "app/tutor_screen/map_frontend.js", "快速开始.bat"):
        if not (root / name).is_file():
            raise ValueError(f"Missing portable runtime resource: {name}")


def create_archive(root: Path, archive: Path) -> None:
    validate_bundle(root)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                output.write(file, Path(root.name) / file.relative_to(root))


def build(source: Path, output: Path) -> Path:
    if os.name != "nt" or platform.machine().upper() not in {"AMD64", "X86_64"} or sys.maxsize <= 2**32 or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Build this package on Windows x64 using Python 3.12.")
    source, output = source.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "家教筛单器-v1.0.2-Windows-x64.zip"
    if archive.exists():
        raise FileExistsError("Refusing to overwrite an existing portable release; choose a new output directory.")
    distributions = dependency_closure(runtime_requirements(source / "requirements.txt"))
    print(f"Packaging Python {platform.python_version()} and {len(distributions)} runtime distributions.", flush=True)
    with tempfile.TemporaryDirectory(prefix="tutor_portable_build_") as temporary:
        root = Path(temporary) / "TutorAssistant-v1.0.2"
        root.mkdir()
        copy_application(source, root / "app")
        copy_python(Path(sys.base_prefix), root / "runtime")
        for name, distribution in sorted(distributions.items()):
            count = copy_distribution(distribution, root / "runtime/Lib/site-packages")
            print(f"  {name}: {count} files", flush=True)
        (root / "快速开始.bat").write_bytes(BAT.replace("\n", "\r\n").encode("ascii"))
        (root / "使用说明.txt").write_text(
            "家教筛单器 v1.0.2 · Windows x64 自带环境启动包\n\n"
            "1. 解压整个压缩包到自己的可写文件夹。不要只取出其中一个文件。\n"
            "2. 双击“快速开始.bat”，无需安装 Python、pip 或依赖。\n"
            "3. 浏览器通常自动打开；若未打开，请使用窗口中显示的本机地址。使用期间请保留窗口。\n"
            "4. 在模型设置和地图设置填写自己的 DeepSeek、高德凭据；这些服务仍需要联网。\n\n"
            "本包不附带任何使用者的密钥、订单、照片、历史或数据库。\n"
            "使用后配置和业务数据由应用保存在 app/.local/，请勿将使用后的文件夹直接转发。\n"
            "此包自带 Python 及依赖，不读取系统 Python；目前在本机 Windows x64 验证。\n"
            "第三方许可见 runtime/LICENSE.txt 和各包 .dist-info 目录。应用尚未选择开源协议。\n",
            encoding="utf-8-sig",
        )
        (root / "runtime_inventory.json").write_text(json.dumps({
            "application_version": "1.0.2", "python_version": platform.python_version(),
            "platform": "Windows-x64", "requires_system_python": False,
            "dependencies": {name: item.version for name, item in sorted(distributions.items())},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        create_archive(root, archive)
    print(f"Portable release created: {archive}", flush=True)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
