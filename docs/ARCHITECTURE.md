# 架构与代码分类

应用保持 `app.py + tutor_screen/` 的运行布局，避免移动业务模块后影响配置位置、静态资源和既有导入。整理主要统一了测试目录、文档与脚本入口。

## 业务模块

| 类别 | 文件 | 职责 |
| --- | --- | --- |
| 页面与会话 | `app.py` | 五个页面、导航、输入提交、状态恢复和操作回调 |
| 数据模型 | `models.py` | 订单、提取字段、时间安排和筛选条件 |
| 批处理 | `pipeline.py` | 订单处理队列、进度通知、降级及旧批次修复 |
| 订单边界 | `segmentation.py` | 编号识别、粘连订单切分与边界核对 |
| 模型请求 | `llm_client.py` | DeepSeek 配置、请求、JSON 校验和模型边界提取 |
| 原文提取 | `local_parser.py`、`text_fields.py`、`locations.py` | 本地字段识别和原文证据 |
| 价格与日程 | `pricing.py`、`schedules.py` | 时薪换算、区间、星期、窗口和备选安排 |
| 请求脱敏 | `privacy.py` | 模型请求前处理地址和联系方式 |
| 匹配与导出 | `matching.py`、`export.py` | 本地条件匹配、状态及 CSV |
| 本地持久化 | `config_store.py`、`batch_store.py`、`history_store.py` | 配置、当前批次和日志 |
| 地图 | `maps.py`、`map_store.py`、`map_ui.py` | 高德接口、缓存、常用地点与状态 |
| 地图前端 | `map_component.py`、`map_frontend.js`、`map_frontend.css` | Streamlit 组件、标记和事件 |
| 界面辅助 | `ui_theme.py`、`ui_theme.css`、`results_layout.py`、`presentation.py` | 主题、响应式结果布局和中文提示 |

表中未带路径的业务文件均位于 `tutor_screen/`。JavaScript 和 CSS 必须与相应 Python 模块一起保留。

## 数据流程

```text
粘贴 / TXT 上传
    → 识别订单边界
    → 本地脱敏 → DeepSeek 字段提取
    → 本地规则核对、补充或降级
    → 完整订单与成功批次快照
    → 已应用条件在本机匹配
    → 可选高德地址定位与距离计算
    → 地图 / 分组卡片 / CSV

导入和关键操作 → 独立的开发日志快照
```

订单原文与地址保留在本机用于核对。脱敏只作用于发送到模型的文本；高德需要接收用于定位的地址，详见 [数据说明](DATA.md)。

## 整理前后目录

| 原目录 | 整理后 | 内容 |
| --- | --- | --- |
| `tests/` | `tests/unit/` | 核心规则、存储与页面状态回归 |
| `test/` | `tests/acceptance/` | 地图、日志、负载、故障及浏览器验收 |
| `test_v1.0.1/` | `tests/regression/` | 历史字段、标题地址与批处理回归 |
| 多处运行说明 | `README.md`、`docs/` | 当前运行方式与开发说明 |
| 历史证据生成入口 | `scripts/run_tests.py` | 无需 Git 仓库的统一测试入口 |

回归测试辅助模块与相应目录放在一起。`pytest.ini` 提供项目根目录及验收、回归辅助模块的导入路径。生成产物统一写入 `artifacts/`。
