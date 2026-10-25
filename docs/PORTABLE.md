# Windows 自带环境启动包

便携版把 Python、标准库和运行依赖放在应用自己的目录中。使用者不需要安装系统 Python，也不需要首次联网安装依赖；调用 DeepSeek 和高德仍需要联网并使用自己的凭据。

## 使用者

1. 获取单独提供的 `家教筛单器-v1.0.2-Windows-x64.zip`，完整解压到可写文件夹。
2. 双击解压目录中的 `快速开始.bat`，保留启动窗口。
3. 浏览器通常会自动打开；未打开时，访问窗口显示的本机地址。
4. 在模型设置和地图设置中填写自己的凭据。

这是 Windows x64 包，不适用于 macOS、Linux 或 Windows ARM 原生运行。仅验证了本机 Windows x64；没有声称已完成其他电脑或全部 Windows 版本测试。

GitHub 的“下载源码 ZIP”不等于自带环境启动包。源码仍使用 Python 开发环境；便携 ZIP 应作为单独的发布附件分发。源码仓库为 [Tutor_assistance](https://github.com/zfree1945/Tutor_assistance)，已有便携 ZIP 保持原样，通过 [v1.0.2 Release](https://github.com/zfree1945/Tutor_assistance/releases/tag/v1.0.2) 单独提供，不放入源码仓库。

## 包内目录

```text
TutorAssistant-v1.0.2/
├── 快速开始.bat
├── 使用说明.txt
├── runtime_inventory.json       # 运行依赖名称与版本，不含本机路径或凭据
├── runtime/                     # Python、标准库、依赖与第三方许可
└── app/
    ├── app.py
    ├── tutor_screen/
    └── .streamlit/config.toml    # 仅公开主题
```

程序使用后会在 `app/.local/` 生成使用者自己的配置、订单和日志。不要把使用后的整个文件夹直接转发给其他人；分发原始、未使用的 ZIP。

## 开发者重新构建

在已有 Python 3.12、项目运行依赖和 `packaging` 的 Windows x64 开发环境中执行：

```powershell
.venv\Scripts\python.exe scripts/build_portable.py --source . --output dist
```

构建使用当前解释器配套的 CPython 和已安装依赖，不下载或复制其他人的运行配置。输出目录若已有同名压缩包会拒绝覆盖；请指定新的输出目录。

- 应用输入采用白名单：只取 `app.py`、`tutor_screen/` 的 Python / JS / CSS 和公开主题。
- 通过已安装分发包的依赖声明及文件清单收集运行依赖，保留 `.dist-info` 与第三方许可证；不收集 pytest、Playwright 等开发工具。
- 不复制 `.local/`、`.venv/`、私密配置、照片、订单、日志、测试产物及字节码缓存。
- 使用 `python312._pth` 固定包内搜索路径，隔离系统 Python 路径和环境变量。原理见 [Python Windows 官方说明](https://docs.python.org/3.12/using/windows.html#finding-modules)。

本仓库应用源码采用 [MIT License](../LICENSE)，版权信息为 Copyright (c) 2026 Zheng Fengyi。Python 和依赖保留各自的第三方许可证。

## 本轮验证：2026-10-25

- 从最终 ZIP 解压到含中文、空格的新路径。
- 清空 PATH 中的 Python，并设置无效的 `PYTHONHOME` / `PYTHONPATH`；包内解释器的隔离模式开启，模块搜索路径均位于包内。
- 导入应用、Streamlit、OpenAI SDK、Pandas、PyArrow 等运行模块成功。
- 通过解压后的批处理启动，健康检查返回 HTTP 200；五个真实页面可访问，无页面异常。
- 用模拟密钥验证配置加密保存与导航后的恢复，并验证 CSV 下载；没有调用真实模型或高德服务。
- 对压缩包全部条目做私密路径检查，并在进程内比对开发者本机实际保存的 API 凭据，未命中。没有输出或记录密钥值。

这些检查证明本机隔离环境中的便携启动行为，不替代真实账号可用性、模型准确率或跨电脑测试。
