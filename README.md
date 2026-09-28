# DocuCheck

> 最新进度（2026-09-28）：M2 八字段采购审查的离线代码链路已接通；后端 240 项测试与 `ruff check backend evals` 通过。用户执行的真实 DeepSeek 单家和三家合成包探针均为 `completed`，分别与标注一致（8/8、24/24）；三家逐家抽取、核验的轨迹未发现跨供应商引用。M2 合成开发包范围的功能验收通过，费用、完整时延及独立资料集质量仍待验收。详情见 [M2 验收记录](docs/m2-offline-acceptance-2026-09-27.md)。M3 的正式 API/worker 和 M4 前端尚未开始；前端开始前先讨论设计规范与样式。下方按日期保留历史过程记录。

> M3 前审查的可复现正确性问题已完成离线修复：比较词归属、完整答复覆盖、供应商来源隔离、宽 XLSX 行分批和已知超预算预检，以及最终可见答案评测。当前后端 **269 项测试**与 Ruff 通过；脚本化开发包 2/2 任务、32/32 可见状态、0/76 引用错误。容量预检只提供保守下界，真实模型修复后表现与正式上传链路尚未复验。详见 [M1/M2 审查及后续修复](docs/m1-m2-pre-m3-audit-2026-09-28.md)。

> M3 第一小步已实现 [Run 生命周期契约](docs/superpowers/plans/2026-09-28-m3-run-lifecycle.md)：领取、完成、取消、澄清恢复、重试均检查活跃 attempt、fence 与快照边界。后端 **275 项测试**和 Ruff 通过；MySQL 持久化、正式 API 与 worker 尚未接线。本轮未运行 Docker 或编写前端。

> M3 第二小步的 [MySQL 核心表和 Alembic 迁移](docs/m3-2-persistence-schema-2026-09-28.md)已通过离线检查（后端 **280 项测试**、Ruff、SQLite 升降迁移）。用户本机 Docker MySQL 8.4 容器显示 `healthy`，Alembic 升级无报错，`current` 为 `0001_core (head)`。当前只有数据表定义和迁移，事务 repository、正式 API 和 worker 尚未接线。

> M3 第三小步的 [Run repository](docs/m3-3-run-repository-2026-09-28.md)已完成离线实现：提交事务、新问题与 retry/resume 幂等、原子 Run 状态转换；后端 **293 passed, 1 skipped**，Ruff 通过。跳过的真实 MySQL 并发测试待用户按记录执行。正式 API、worker 和前端尚未接线。

> 最新协作状态（2026-09-25）：[M1 最小真实 Agent](docs/m1-acceptance-2026-09-25.md)在模拟包与内存字段范围内完成验收；真实模型连续两问通过，后端离线测试 90/90、全目录 Ruff 与依赖检查通过。正式上传、其他七字段和独立质量评测尚未完成。后续仍按[分步协作计划](docs/step-by-step-plan.md)一次做一个小步骤。任何前端工作开始前须先暂停并讨论设计规范与样式。

> 2026-09-26：[M2-0 XLSX 内存分列证据修正](docs/m2-0-acceptance-2026-09-26.md)和 [M2-1 答复引用范围修正](docs/m2-1-acceptance-2026-09-26.md)已通过离线回归（当前 110 项测试）。下一小步为 M2-2 解析覆盖；M2 完整采购审查尚未完成。

> 2026-09-26：[M2-2a PDF 源文字覆盖校验](docs/m2-2a-pdf-source-coverage-2026-09-26.md)已通过离线回归（当前 113 项测试），能发现原 PDF 可提取文字在 MinerU 结果中静默漏抽。下一小步为 M2-2b XLSX 范围声明校验；整个 M2-2 与完整采购审查仍未完成。

> 2026-09-26：[M2-2b XLSX 范围声明校验](docs/m2-2b-xlsx-dimension-2026-09-26.md)已通过离线回归（当前 117 项测试），能发现错误范围声明导致的单元格漏读。下一小步为 M2-2c PDF 非文字区域；整个 M2-2 仍未完成。

> 2026-09-26：[M2-2c PDF 嵌入图片覆盖检查](docs/m2-2c-pdf-embedded-images-2026-09-26.md)已通过离线回归（当前 118 项测试）。正文与图片混合的 PDF 不再仅凭正文被判为解析完整。下一小步为 M2-2d 矢量绘制与表格结构；整个 M2-2 仍未完成。

> 2026-09-26：[M2-2d PDF 矢量绘制与注释覆盖检查](docs/m2-2d-pdf-vector-and-annotation-coverage-2026-09-26.md)已通过离线回归（当前 125 项测试）。可见线框、实际调用的 Form 绘图及注释不会仅凭正文判为完整。下一小步拟处理 XLSX 其他结构；无框表格语义和任务级限额仍待解决，M2-2 尚未验收。

> 2026-09-26：[M2-2e XLSX 图片与图表覆盖检查](docs/m2-2e-xlsx-drawing-coverage-2026-09-26.md)已通过离线回归（当前 127 项测试）。工作表实际引用的绘图被忽略时会标为 `partial`，并保留已读单元格。下一小步拟处理批注及旧式绘图；整个 M2-2 仍未完成。

> 2026-09-27：[M2-2f XLSX 常规批注与旧式绘图覆盖检查](docs/m2-2f-xlsx-comments-legacy-drawings-2026-09-27.md)已通过离线回归（当前 131 项测试）。实际引用的批注和旧式绘图不再仅凭单元格判为完整。下一小步拟检查隐藏行列；M2-2 尚未验收。

> 2026-09-27：[M2-2g XLSX 隐藏行列覆盖检查](docs/m2-2g-xlsx-hidden-rows-columns-2026-09-27.md)已通过离线回归（当前 144 项测试）。隐藏与零尺寸行列不再仅凭已读单元格判为完整。下一小步拟检查隐藏工作表；M2-2 尚未验收。

DocuCheck 是一个正在开发的本地采购资料初审 Agent 工作台。目标交互是上传资料后提问，由单 Agent 调用受控工具并返回带来源的答复；八字段正式结论由规则引擎计算。现有资产包括基础环境、单家与三家模拟文档、PDF/XLSX 解析、快照证据、词法检索、DeepSeek Provider，以及八字段抽取/规则/Agent 离线链路。真实模型曾在 M1 的内存问题和追问上运行；M2 的真实单家、三家合成包探针均已完成。数据库首版迁移已在用户本机 MySQL 上验证；Linux worker 只有启动与健康检查骨架，正式 API 和前端尚未实现。

AI 协作上下文见 [CLAUDE.md](CLAUDE.md)；Codex 入口为 [AGENTS.md](AGENTS.md)。当前设计基线是 [Agent 架构 v1.0](docs/superpowers/specs/2026-09-24-agent-first-design.md)，涵盖模型、状态、工具、上下文、RAG、恢复和评测；阶段条件见 [路线图](docs/development-roadmap.md)。

2026-09-24 已按 Agent 为中心重排计划。[M1 实施计划](docs/superpowers/plans/2026-09-24-agent-mvp.md)中的模拟包内存链路已完成验收；M2 离线链路与已执行的真实探针见 [验收记录](docs/m2-offline-acceptance-2026-09-27.md)。历史阶段 0/1 名称不代表新的里程碑进度。

## 当前范围

首版处理一份办公笔记本采购需求及最多三家供应商的报价和规格书，比较八个字段，并保留可以回到原件的证据。实施顺序见 [开发路线 v2.0](docs/development-roadmap.md)；历史基础环境设计见 [阶段 0 设计](docs/superpowers/specs/2026-09-24-phase-0-foundation-design.md)。

## 本地 Python 环境

在 Miniconda PowerShell 中运行：

```powershell
conda env create -f backend/environment.yml
conda activate docucheck
python --version
```

已在开发机核对 Python 3.12.14。`environment.yml` 固定 Python 主次版本和样例所需的 Python 包版本。

如果 PyCharm 自动创建了 `.venv`，请在项目解释器设置中选用已有的 `docucheck` Conda 环境。项目要求 Python 3.12，`pyproject.toml` 已与此对齐。

运行开发样例检查：

```powershell
python -m pytest backend/tests/test_dev_pack.py -q
```

环境文件现固定了样例构建和检查所用的 Python 包版本。应用运行依赖及 Windows/Linux 的完整锁定文件仍待兼容性验证。

现有解析代码位于 `backend/app/parsing.py`。在已有的 `docucheck` Conda 环境中，可调用独立 MinerU 环境的可执行文件生成模拟包证据索引：

```powershell
python -m backend.app.parse_dev_pack --mineru-executable "E:\developTools\miniconda3\envs\docucheck-mineru\Scripts\mineru-kit.exe" --output data/dev-pack-evidence.json
```

当前这是本机开发命令；Linux worker 中的解析依赖和正式任务接线尚未完成。输出在被 Git 忽略的 `data/` 中，每条 PDF 证据包含页码与 bbox，每条 XLSX 证据包含工作表与单元格。

## 数据与状态

[`examples/dev-pack-001/`](examples/dev-pack-001/README.md) 保存完全模拟的开发样例和八条标准答案。独立验收集尚未建立，文档中的质量与性能数值均为目标，尚无实测结论。真实资料必须由资料持有人明确授权后使用。

## 本地基础服务

Docker Desktop 使用 Linux Engine。在项目根目录创建 `.env`（可从 `.env.example` 复制），将两个示例口令替换为本地随机口令后运行：

```powershell
docker compose up -d --build
docker compose ps
```

Compose 启动 MySQL 8.4、Redis 7.4 和 Linux Celery worker。MySQL 仅映射到本机 `127.0.0.1:3307`，Redis 仅映射到 `127.0.0.1:6379`；项目文件共享目录是 `data/`，在 worker 内为 `/data`。数据库使用独立的 Docker 卷。首次配置的详细验证见 [环境记录](docs/environment/2026-09-24.md)。

停止服务可运行 `docker compose stop`；此命令保留数据库卷。

目前只有基础服务和 worker 健康检查。Case API、实际处理任务、数据库迁移与前端尚未实现。
