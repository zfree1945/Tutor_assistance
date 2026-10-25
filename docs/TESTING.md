# 测试说明

## 安装与运行

在项目根目录执行：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe scripts/run_tests.py
```

`scripts/run_tests.py` 使用当前 Python 解释器，从项目根目录运行 pytest，不依赖 `.git/` 或原项目的虚拟环境路径。也可以直接执行 `python -m pytest -q`。

```powershell
# 不启动浏览器
.venv\Scripts\python.exe scripts/run_tests.py --without-browser

# 只检查测试收集
.venv\Scripts\python.exe scripts/run_tests.py --collect-only

# 针对某一类测试
.venv\Scripts\python.exe -m pytest tests/unit -q
.venv\Scripts\python.exe -m pytest tests/acceptance -q
.venv\Scripts\python.exe -m pytest tests/regression -q

# 输出覆盖率和 JUnit XML
.venv\Scripts\python.exe scripts/run_tests.py --cov=tutor_screen --cov=app --cov-branch --cov-report=term --junitxml=artifacts/results.xml
```

未安装 Playwright 或 Chromium 时，不能把浏览器验收视为完成；Windows DPAPI 配置测试要求 Windows。

## 分类

| 目录 | 重点 |
| --- | --- |
| `unit/` | 切分、地址、价格、性别、年级、时间、匹配、批次恢复、配置与页面状态 |
| `acceptance/` | 地图 API 与组件、日志、故障隔离、并发/负载、浏览器布局、CSV 与导航 |
| `regression/` | 早期字段标注、复合标题地址、边界和对抗输入、处理一致性及性能 |

目录分类保留测试历史来源；其中也含 AppTest 和跨模块用例，因此 `unit/` 并不表示每个用例都只执行单一函数。

## 隔离与验证范围

- 配置、日志和地图数据使用临时路径，配置存储用模拟密钥验证真实 DPAPI 加解密。
- 模型调用使用模拟响应或本地提取替身，不使用自己的真实 API Key。
- 浏览器启动临时源码副本与随机本机端口，结束时关闭自己启动的服务。
- 高德验收使用 MockTransport 和离线 JavaScript SDK。截图中的模拟底图不代表真实地图渲染质量。
- `artifacts/acceptance/` 和 `artifacts/regression/` 存放本轮生成的截图、CSV 及性能记录，不参与源码发布。

自动化用例验证程序行为，不等同于真实模型准确率、在线性能或外部账号权限验证。历史人工报告和机器运行产物未作为本次发布副本的源码交付内容。

本次整理的执行结果见 [验证记录](PUBLICATION_CHECKLIST.md)。
