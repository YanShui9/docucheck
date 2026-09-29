# DocuCheck

> M2 阶段记录（2026-09-28）：M2 八字段采购审查的离线代码链路已接通；当时后端 240 项测试与 `ruff check backend evals` 通过。用户执行的真实 DeepSeek 单家和三家合成包探针均为 `completed`，分别与标注一致（8/8、24/24）；三家逐家抽取、核验的轨迹未发现跨供应商引用。M2 合成开发包范围的功能验收通过，费用、完整时延及独立资料集质量仍待验收。详情见 [M2 验收记录](docs/m2-offline-acceptance-2026-09-27.md)。下方按日期保留历史过程记录；前端开始前先讨论设计规范与样式。

> M3 前审查的可复现正确性问题已完成离线修复：比较词归属、完整答复覆盖、供应商来源隔离、宽 XLSX 行分批和已知超预算预检，以及最终可见答案评测。当前后端 **269 项测试**与 Ruff 通过；脚本化开发包 2/2 任务、32/32 可见状态、0/76 引用错误。容量预检只提供保守下界，真实模型修复后表现与正式上传链路尚未复验。详见 [M1/M2 审查及后续修复](docs/m1-m2-pre-m3-audit-2026-09-28.md)。

> M3 第一小步已实现 [Run 生命周期契约](docs/superpowers/plans/2026-09-28-m3-run-lifecycle.md)：领取、完成、取消、澄清恢复、重试均检查活跃 attempt、fence 与快照边界。后端 **275 项测试**和 Ruff 通过；MySQL 持久化、正式 API 与 worker 尚未接线。本轮未运行 Docker 或编写前端。

> M3 第二小步的 [MySQL 核心表和 Alembic 迁移](docs/m3-2-persistence-schema-2026-09-28.md)已通过离线检查（后端 **280 项测试**、Ruff、SQLite 升降迁移）。用户本机 Docker MySQL 8.4 容器显示 `healthy`，Alembic 升级无报错，`current` 为 `0001_core (head)`。当前只有数据表定义和迁移，事务 repository、正式 API 和 worker 尚未接线。

> M3 第三小步的 [Run repository](docs/m3-3-run-repository-2026-09-28.md)已完成验收：提交事务、新问题与 retry/resume 幂等、原子 Run 状态转换；离线后端 **293 passed, 1 skipped**，Ruff 通过。用户另行显式运行的真实 MySQL 并发测试 **1 passed**。正式 API、worker 和前端尚未接线。

> M3 第四小步的 [FastAPI 入口](docs/m3-4-api-entry-2026-09-28.md)已完成范围内验收：空 Case、会话、消息提交、Run/事件查询、取消和重试。离线后端 **296 passed, 2 skipped**，Ruff 通过；用户另行运行的真实 MySQL 进程内 API 测试 **1 passed**。文件上传、Agent worker、认证和前端尚未接入。

> M3 第五小步的 [后端上传与输入快照](docs/m3-5-upload-snapshot-2026-09-28.md)已完成范围内验收：PDF/XLSX 预检、角色与供应商绑定、文件哈希及随机路径、版本递增与快照保留。离线后端 **300 passed, 3 skipped**，Ruff 与依赖检查通过；用户运行的真实 MySQL 上传测试另得 **1 passed**。文档仍为 `pending`，解析入库、Agent worker、认证和前端尚未接入。

> M3 第六小步的 [上传资料解析与证据入库](docs/m3-6-evidence-ingestion-2026-09-28.md)已完成范围内验收：按快照解析、原件哈希复核、证据位置及覆盖信息入库、只读证据 API 与本机 CLI。离线后端 **306 passed, 4 skipped**，Ruff 和依赖检查通过；用户运行的真实 MySQL 上传加 XLSX 解析测试另得 **1 passed**。真实 MinerU PDF 尚未实测；Agent worker、认证和前端尚未接入。

> M3 第七小步的 [持久化证据驱动单次 Agent Run](docs/m3-7-persisted-agent-run-2026-09-28.md)已完成单文件来源问答范围内验收：正式上传快照的证据 ID 和来源进入 LangGraph Agent，Run 通过 attempt/fence 保存答复、用量和结果未知标志。后端 **323 passed, 5 skipped**、Ruff 通过；真实 MySQL 集成测试 **1 passed**。本机网络 API 上传及解析报价文件后，真实 `deepseek-flash` Run 为 `completed`，正确引用 `报价!B6` 的 `16 GB`。首次答复仅显示文档 ID 的问题已修正并离线验证，旧 Run 答复不变。Celery、连续追问恢复和前端尚未接入。

> M3 第八小步 A 的 [Outbox 领取与过期恢复](docs/m3-8a-outbox-claims-2026-09-28.md)已完成范围内验收：可领取待发 Run 事件、租约到期重领、拒绝旧领取者确认，MySQL 并发锁竞争定向测试 **1 passed**；后端离线 **326 passed, 6 skipped**。尚未发布 Redis 或接通 Celery worker；本步未运行 Docker、模型和前端。

> M3 第八小步 B 的 [Outbox 发布与 Celery Run 任务](docs/m3-8b-outbox-celery-2026-09-28.md)已通过本机闭环：后端离线 **341 passed, 7 skipped**，真实 MySQL/Redis/Linux Celery 空快照测试 **1 passed**、`model_calls=0`，worker 健康。自动派发已启动；新提交且资料就绪的 Run 将使用配置的模型。强杀 worker 后的 `running` 回收和 Redis 丢消息对账仍待 M3-8c；前端未开始。

> M3 第八小步 C 的 [Run 失联与队列消息恢复](docs/m3-8c-run-recovery-2026-09-29.md)已通过范围内验收：运行中 Run 每 10 秒写心跳、120 秒失联后标为 `interrupted / worker_lost / outcome_unknown`，旧结果被 fence 拒绝；已投递但仍排队的 Outbox 延时重投。离线后端 **350 passed, 8 skipped**，显式 MySQL/API/Redis/Celery 测试 **3 passed**，包括无模型调用的独立进程强制终止场景。Redis 已启用 AOF 卷，迁移到 `0003_recovery_precision`；跨进程多轮会话、后台解析和前端仍待后续。

> M3 第九小步的 [跨进程会话追问与澄清恢复](docs/m3-9-conversation-recovery-2026-09-29.md)已实现同会话同快照的最近四轮问答及核验结果持久化、澄清回复 API、连续澄清与活跃 Run 互斥。离线 **357 passed, 9 skipped**，真实 MySQL 定向 **5 passed**；迁移为 `0004_conversation_memory`。脚本化 Provider 测试没有调用 DeepSeek；后台解析与前端仍待后续。

> M3 第十小步的 [上传后台解析](docs/m3-10-background-parsing-2026-09-29.md)已实现 MySQL 租约领取、失联重领和旧结果栅栏；待解析文档会延后 Agent Run 派发。迁移为 `0005_background_parsing`，离线 **365 passed, 10 skipped**。PDF 的真实 MinerU 命令端到端处理仍待验收；前端尚未开始。

> M3-11a 的 [可复核事实集持久化](docs/m3-11a-fact-persistence-2026-09-29.md)为后续人工修正保存已验证的事实、扫描范围和证据引用；旧版 Run 记忆继续可读。离线 **379 passed, 10 skipped**。人工修正接口、新版本重算和删除清理仍待实现。

> M3-11b 的 [来源事实修正](docs/m3-11b-correction-submission-2026-09-29.md)已接通事实列表、修正提交和审计读取；新值须由同一原文支持，提交生成新 Case 版本并让旧 Run 过期。新版本处于 `pending_recheck`，规则重算前拒绝新 Run。离线 **387 passed, 11 skipped**，真实 MySQL 无模型并发测试 **1 passed**，迁移为 `0006_corrections`；worker/dispatcher 已更新。正式新结论、删除清理与前端仍待后续。

> M3-11c 的 [确定性修正规则复核](docs/m3-11c-correction-recheck-2026-09-29.md)已接通 `POST /api/v1/cases/{case_id}/corrections/{correction_id}/recheck`：新快照重验原文并重跑规则，保留同一原文支持的旧值和新值，标记 `needs_review`。离线 **396 passed, 11 skipped**，MySQL 并发 **1 passed**，迁移到 `0007_correction_reviews`，未调用模型。Agent 尚未消费修正事实，所以新 Run 仍受阻；删除清理与前端待后续。

> 最新协作状态（2026-09-25）：[M1 最小真实 Agent](docs/m1-acceptance-2026-09-25.md)在模拟包与内存字段范围内完成验收；真实模型连续两问通过，后端离线测试 90/90、全目录 Ruff 与依赖检查通过。正式上传、其他七字段和独立质量评测尚未完成。后续仍按[分步协作计划](docs/step-by-step-plan.md)一次做一个小步骤。任何前端工作开始前须先暂停并讨论设计规范与样式。

> 2026-09-26：[M2-0 XLSX 内存分列证据修正](docs/m2-0-acceptance-2026-09-26.md)和 [M2-1 答复引用范围修正](docs/m2-1-acceptance-2026-09-26.md)已通过离线回归（当前 110 项测试）。下一小步为 M2-2 解析覆盖；M2 完整采购审查尚未完成。

> 2026-09-26：[M2-2a PDF 源文字覆盖校验](docs/m2-2a-pdf-source-coverage-2026-09-26.md)已通过离线回归（当前 113 项测试），能发现原 PDF 可提取文字在 MinerU 结果中静默漏抽。下一小步为 M2-2b XLSX 范围声明校验；整个 M2-2 与完整采购审查仍未完成。

> 2026-09-26：[M2-2b XLSX 范围声明校验](docs/m2-2b-xlsx-dimension-2026-09-26.md)已通过离线回归（当前 117 项测试），能发现错误范围声明导致的单元格漏读。下一小步为 M2-2c PDF 非文字区域；整个 M2-2 仍未完成。

> 2026-09-26：[M2-2c PDF 嵌入图片覆盖检查](docs/m2-2c-pdf-embedded-images-2026-09-26.md)已通过离线回归（当前 118 项测试）。正文与图片混合的 PDF 不再仅凭正文被判为解析完整。下一小步为 M2-2d 矢量绘制与表格结构；整个 M2-2 仍未完成。

> 2026-09-26：[M2-2d PDF 矢量绘制与注释覆盖检查](docs/m2-2d-pdf-vector-and-annotation-coverage-2026-09-26.md)已通过离线回归（当前 125 项测试）。可见线框、实际调用的 Form 绘图及注释不会仅凭正文判为完整。下一小步拟处理 XLSX 其他结构；无框表格语义和任务级限额仍待解决，M2-2 尚未验收。

> 2026-09-26：[M2-2e XLSX 图片与图表覆盖检查](docs/m2-2e-xlsx-drawing-coverage-2026-09-26.md)已通过离线回归（当前 127 项测试）。工作表实际引用的绘图被忽略时会标为 `partial`，并保留已读单元格。下一小步拟处理批注及旧式绘图；整个 M2-2 仍未完成。

> 2026-09-27：[M2-2f XLSX 常规批注与旧式绘图覆盖检查](docs/m2-2f-xlsx-comments-legacy-drawings-2026-09-27.md)已通过离线回归（当前 131 项测试）。实际引用的批注和旧式绘图不再仅凭单元格判为完整。下一小步拟检查隐藏行列；M2-2 尚未验收。

> 2026-09-27：[M2-2g XLSX 隐藏行列覆盖检查](docs/m2-2g-xlsx-hidden-rows-columns-2026-09-27.md)已通过离线回归（当前 144 项测试）。隐藏与零尺寸行列不再仅凭已读单元格判为完整。下一小步拟检查隐藏工作表；M2-2 尚未验收。

DocuCheck 是一个正在开发的本地采购资料初审 Agent 工作台。目标交互是上传资料后提问，由单 Agent 调用受控工具并返回带来源的答复；八字段正式结论由规则引擎计算。现有资产包括基础环境、单家与三家模拟文档、PDF/XLSX 解析、快照证据、词法检索、DeepSeek Provider，以及八字段抽取/规则/Agent 离线链路。真实模型曾在 M1 的内存问题和追问上运行；M2 的真实单家、三家合成包探针均已完成。数据库首版迁移已在用户本机 MySQL 上验证；首批 Case/会话/Run、后端文件上传 API、证据读取 API 和单次持久化 Agent Run 执行器已实现，Linux worker 单次 Run 自动执行已接通；前端尚未实现。

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

当前这是本机开发解析命令；Linux worker 已接入 Agent Run，后台上传解析任务尚未接线。输出在被 Git 忽略的 `data/` 中，每条 PDF 证据包含页码与 bbox，每条 XLSX 证据包含工作表与单元格。

## 数据与状态

[`examples/dev-pack-001/`](examples/dev-pack-001/README.md) 保存完全模拟的开发样例和八条标准答案。独立验收集尚未建立，文档中的质量与性能数值均为目标，尚无实测结论。真实资料必须由资料持有人明确授权后使用。

## 本地基础服务

Docker Desktop 使用 Linux Engine。在项目根目录创建 `.env`（可从 `.env.example` 复制），将两个示例口令替换为本地随机口令后运行：

```powershell
docker compose up -d mysql redis
docker compose --profile agent up -d --build worker dispatcher
docker compose --profile agent ps
```

在已激活的 `docucheck` Conda 环境中，迁移数据库并另开本机终端运行解析进程。它负责上传后的 PDF/XLSX 自动解析；关闭终端后进程停止，重启时会继续领取待处理文档。PDF 需指向本机已安装的 MinerU：

```powershell
python.exe -m alembic -c backend/alembic.ini upgrade head
python.exe -m backend.app.parse_background --mineru-executable 'E:\developTools\miniconda3\envs\docucheck-mineru\Scripts\mineru-kit.exe'
```

Compose 启动 MySQL 8.4、Redis 7.4；显式启用 `agent` profile 后启动 Linux Celery worker 和 Outbox dispatcher。dispatcher 会自动处理新提交的 Run，资料就绪时 worker 可调用 `.env` 中配置的模型服务。MySQL 仅映射到本机 `127.0.0.1:3307`，Redis 仅映射到 `127.0.0.1:6379`；项目文件共享目录是 `data/`，在 worker 内为 `/data`。MySQL 和 Redis 使用各自的 Docker 卷；Redis 开启 AOF，每秒刷盘。首次配置的详细验证见 [环境记录](docs/environment/2026-09-24.md)。

仅停止自动处理可运行 `docker compose --profile agent stop dispatcher worker`；停止全部服务可运行 `docker compose --profile agent stop`，这两个命令都保留数据库卷。

当前已有 Case/会话/Run、上传及证据读取 API 和数据库迁移；本机解析进程运行时会自动处理上传文档，手动解析 CLI 可用于排障。Outbox、Redis 发布、Celery Run 自动执行、失联回收和跨进程多轮追问已接通；失联后的模型结果标为未知，只能显式重试。前端尚未实现。
