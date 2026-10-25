"""Prepare a local virtualenv and start Streamlit without shell commands."""
import hashlib
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HELP = """Usage: double-click the root startup .bat file, or run start_app.py [Streamlit options].

Python 3.12 is required. The first run creates .venv and installs requirements.txt.
Later runs reuse this environment. No application settings or credentials are changed.
"""


def prepare_environment(root: Path) -> Path:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("请先安装 Python 3.12，再重新双击启动脚本。")
    root = root.resolve()
    requirements = root / "requirements.txt"
    if not requirements.is_file() or not (root / "app.py").is_file():
        raise RuntimeError("请保留完整项目目录，不要单独移动启动脚本。")
    environment = root / ".venv"
    python = environment / "Scripts" / "python.exe"
    if not python.is_file():
        print("首次运行：正在创建本机 Python 环境…", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(environment)], cwd=root, check=True)
    marker = environment / ".tutor_requirements.sha256"
    digest = hashlib.sha256(requirements.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    try:
        installed = marker.read_text(encoding="ascii").strip() == digest
    except OSError:
        installed = False
    if installed:
        ready = subprocess.run(
            [str(python), "-c", "import streamlit, openai, pydantic, httpx"],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        installed = ready.returncode == 0
    if not installed:
        print("正在安装或检查应用依赖，首次运行需要联网，请稍候…", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(requirements)],
            cwd=root, check=True,
        )
        marker.write_text(digest + "\n", encoding="ascii")
    return python


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if "--help" in arguments or "-h" in arguments:
        print(HELP)
        return 0
    try:
        python = prepare_environment(ROOT)
        print("正在启动家教筛单器。浏览器通常会自动打开；使用期间请保留此窗口。", flush=True)
        return subprocess.run(
            [str(python), "-m", "streamlit", "run", str(ROOT / "app.py"),
             "--server.headless=false", *arguments],
            cwd=ROOT,
        ).returncode
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            print("环境准备失败，请检查上方的安装信息和网络连接后重试。", file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
