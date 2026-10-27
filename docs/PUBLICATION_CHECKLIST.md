# GitHub 仓库准备检查清单

日期：2026-10-25。以下记录源码整理与发布前检查；随后依据使用者的明确授权创建公开仓库并发布 v1.0.2。

## 内容范围

- 保留应用源码、公开主题、源码启动脚本、开发脚本、运行及开发依赖、现有全部 65 个测试文件和 docs 中的 Markdown 文档。
- 排除 `.local/`、`.venv/`、环境变量文件、私密 Streamlit 配置、数据库、密文、个人订单、照片、历史截图、缓存和测试产物。
- 自带环境 ZIP 留在原发布附件目录，未放入此仓库；`.gitignore` 额外排除压缩包、`runtime/`、`dist/` 和 `build/`。
- 原目录与已有启动包保持原样，新内容仅写入本次指定的仓库副本。

## 许可证

使用 [SPDX MIT 标准原文](https://raw.githubusercontent.com/spdx/license-list-data/main/text/MIT.txt)，仅将版权行替换为 `Copyright (c) 2026 Zheng Fengyi`，其余文本不变。

对应 LICENSE 已加入 README 链接和本地验证。复制、修改和分发条件以 [LICENSE](../LICENSE) 原文为准。

## 代码与依赖

运行依赖保留 Streamlit、Pandas、Pydantic、HTTPX 和 OpenAI SDK 的已验证版本。应用未直接使用的 python-dotenv 和 tenacity 不再作为直接运行依赖列入；开发工具单独列在 `requirements-dev.txt`。

应用功能代码和公开主题保持与源目录一致。测试文件总数保持 65；仅更新已改名的启动入口引用、MIT 文档校验以及正常 Git 克隆目录的检查兼容性。

`scripts/check_project.py` 允许正常仓库的 `.git/` 元数据，并跳过其中的内容；私密运行数据仍被拦截。这样后续初始化或克隆 GitHub 仓库不会使结构测试失败。

## 验证

本轮在此仓库副本执行：

```powershell
python -m pytest --collect-only -q
python -m pytest -q --tb=short --junitxml=artifacts/repository-preparation-final.xml
```

实际使用现有 Windows Python 3.12.14 开发环境从副本根目录运行，未将该虚拟环境复制进仓库。测试收集为 1063 项，JUnit 汇总为 1063 通过、0 失败、0 错误、0 跳过。以上是本轮实际执行结果，未沿用旧文档数字。

已完成：

- 65 个测试文件及辅助文件全部保留，12 份 Markdown 文档均保留。
- 28 个应用源码与公开主题文件逐字节一致，没有修改应用行为。
- LICENSE 与 SPDX 原文逐字节比对，仅版权行替换为指定内容。
- 本机实际保存的 DeepSeek、高德凭据在候选仓库文件中未命中；疑似硬编码密钥字面值检查未命中。检查未输出密钥值。
- `.gitignore` 通过临时 Git 仓库验证：运行数据、凭据、压缩包和运行环境被忽略，LICENSE 与公开源码可被跟踪。
- 屏蔽已移除的 dotenv、tenacity 后，应用主要模块仍可正常导入。
- 未发现内容完全相同的重复文件。个人图片、虚拟环境、数据库和 Release ZIP 未复制。
- 原有 Release ZIP 的 SHA256 保持不变；它仍位于原发布附件目录。
- 全量测试完成后，生成的截图、下载文件、JUnit XML、字节码和 pytest 缓存移到临时归档，不留在待发布仓库中。

这些测试使用模拟服务和临时数据，不等同于真实账号可用性、在线模型准确率或跨电脑兼容性验证。

发布地址：[源码仓库](https://github.com/zfree1945/Tutor_assistance)、[v1.0.2 Release](https://github.com/zfree1945/Tutor_assistance/releases/tag/v1.0.2)。ZIP 作为 Release 附件，不纳入 Git 历史；提交作者使用 GitHub 隐私邮箱。
