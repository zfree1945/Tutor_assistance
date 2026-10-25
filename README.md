# 家教筛单器 v1.0.2

粘贴家教订单文本，解析成结构化订单并在本地筛选，在地图上查看位置分布。一个单用户、本地运行的 Streamlit 应用，完整使用环境为 Windows。

## 功能特性

- **模型设置**：使用 DeepSeek API 提取订单字段，提供创建 API Key 的快捷入口，配置自动加密保存。
- **地图设置**：配置高德凭据和默认城市，管理常用出发点及地图缓存。
- **订单导入**：粘贴文本或上传 UTF-8 TXT，填写筛选条件后点击“应用筛选”。
- **筛选结果**：查看地图和分组订单列表，修改条件，导出当前筛选结果或明确匹配 CSV。
- **开发日志**：复核原文、字段来源及匹配理由，下载原始文件或操作快照。
- **地图定位**：通过高德 JS API 展示底图、Web 服务查询地址，使用 GCJ-02 坐标；未配置地图仍可解析、筛选和导出。
- **隐私处理**：发送给模型前屏蔽联系方式和详细地址；发送给高德前按规则清洗混入地址的联系方式和费用。定位所需地址仍会发送给高德。

## 快速开始

### 直接使用：自带环境启动包

Windows x64 自带环境启动包通过 [v1.0.2 Release](https://github.com/zfree1945/Tutor_assistance/releases/tag/v1.0.2) 单独提供。下载其中的 `家教筛单器-v1.0.2-Windows-x64.zip`，它不放在源码仓库中。

完整解压启动包，双击其中的 `快速开始.bat`。无需安装 Python 或依赖，使用期间保留启动窗口。浏览器通常会自动打开；若未打开，请访问窗口显示的本机地址，通常为 [http://localhost:8501](http://localhost:8501)。

启动包不附带使用者的密钥、订单或历史。DeepSeek 和高德服务仍需联网，并填写使用者自己的凭据。使用后的数据位于启动包的 `app/.local/`，请勿直接转发使用后的目录。详见 [启动包说明](docs/PORTABLE.md)。

### 从源码运行

先安装 Python 3.12，下载或克隆本仓库后，双击根目录的 [快速开始.bat](快速开始.bat)。首次运行会在本机创建 `.venv` 并安装 `requirements.txt` 中的依赖，需要联网；后续复用环境，依赖变更或缺失时重新安装。

GitHub 的源码 ZIP 不包含 Python 运行环境，与 Release 启动包是两种不同的交付内容。

## 配置与使用

进入“模型设置”，填写自己的 DeepSeek API Key、Base URL 和模型名称，后两项也不能为空。页面提供 [DeepSeek API Key 创建入口](https://platform.deepseek.com/api_keys)。配置使用 Windows DPAPI 按当前 Windows 用户加密保存在本机 `.local/`。

地图功能可选。在“地图设置”填写自己的高德 JS API Key、安全密钥 `securityJsCode`、Web 服务 Key 和默认城市，再保存出发点。页面提供 [高德凭据申请入口](https://console.amap.com/dev/key/app)。Web 服务 Key 用于 Python 后端查询，不传入地图组件或高德 JS SDK；设置页通过密码输入框配置。

**模型设置 → 地图设置（可选）→ 订单导入 → 应用筛选 → 筛选结果 → 开发日志复核。**

新文本需要解析；对已有订单调整筛选条件本身不会再次调用模型。修改输入可能触发新解析，覆盖上一批前需要确认。地图卡片底部依次为“修改筛选条件”“导出当前筛选 CSV”“导出明确匹配 CSV”。

配置加密不等于所有业务数据都加密：批次快照和日志包含订单原文。`.local/`、密钥文件、数据库及运行产物不属于仓库内容。详见 [数据说明](docs/DATA.md)。

## 地图与定位

- 自动地理编码的精确级仅接受“门牌号 / 单元号 / 门址”；地标、道路和区域结果按相应约略精度处理。
- 范围参考点显示“约”，实际距离按未知处理；虚线表示可能匹配，实线表示明确匹配，边框与定位精度独立。
- 定位失败或没有位置的订单仍留在列表，不因地图失败而删除。
- 额度不足时暂停后续查询，不自动重试、不自动购买；问题处理后可主动重试。

详见 [地图指南](docs/MAPS_V1.0.2.md)、[区域定位](docs/AREA_MAP_V1.0.2.md) 和 [自动定位](docs/AUTO_MAP_V1.0.2.md)。

## 项目结构

```text
app.py                         # 页面、导航与会话状态
快速开始.bat                   # 源码版双击启动入口
tutor_screen/
  pipeline.py                  # 批处理、进度、降级和旧批次修复
  segmentation.py              # 订单编号与边界切分
  local_parser.py              # 本地字段提取与修正
  matching.py                  # 本地匹配与排除理由
  maps.py                      # 高德请求、地址清洗与精度
  map_ui.py                    # 地图设置与查询状态
  map_component.py             # 地图组件与前后端事件
  privacy.py                   # 模型请求前脱敏
  export.py                    # CSV 导出与单元格保护
tests/unit/                    # 业务、存储和状态回归
tests/acceptance/              # 地图、日志、故障与浏览器验收
tests/regression/              # 历史字段、边界和批处理回归
scripts/                       # 启动、测试、仓库检查和便携包构建
docs/                          # 架构、地图、数据、解析及测试文档
.streamlit/config.toml         # 不含密钥的公开主题
requirements.txt               # 运行依赖及已验证版本
requirements-dev.txt           # 测试与构建工具
LICENSE                        # MIT 许可证
```

## 测试

保留现有全部 65 个测试文件。开发者安装测试依赖及 Chromium 后运行：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m pytest --collect-only -q
.venv\Scripts\python.exe -m pytest -q
```

2026-10-25 在本副本实测：65 个测试文件，收集 **1063 项用例**，运行结果 **1063 项通过，0 失败、0 错误、0 跳过**。测试使用模拟服务、临时数据和离线地图 SDK，不使用发布者的真实凭据，也不代表真实模型准确率。执行方法见 [测试说明](docs/TESTING.md)，本次结果见 [仓库准备检查清单](docs/PUBLICATION_CHECKLIST.md)。

## 详细文档

- [架构与模块分类](docs/ARCHITECTURE.md)
- [地图使用说明](docs/MAPS_V1.0.2.md)
- [区域与约略定位](docs/AREA_MAP_V1.0.2.md)
- [筛选后自动定位](docs/AUTO_MAP_V1.0.2.md)
- [测试方案](docs/TEST_PLAN.md)
- [界面说明](docs/UI_V1.0.2.md)
- [详细解析规则](docs/PARSING_RULES.md)
- [自带环境启动包](docs/PORTABLE.md)

## 许可证

本项目源码采用 [MIT License](LICENSE)。

Copyright (c) 2026 Zheng Fengyi

Python 和第三方依赖遵循各自的许可证；它们的许可声明不由本项目的 MIT LICENSE 替代。
