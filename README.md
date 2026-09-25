# DocuCheck

> 最新协作状态（2026-09-25）：[M1 最小真实 Agent](docs/m1-acceptance-2026-09-25.md)在模拟包与内存字段范围内完成验收；真实模型连续两问通过，后端离线测试 90/90、全目录 Ruff 与依赖检查通过。正式上传、其他七字段和独立质量评测尚未完成。后续仍按[分步协作计划](docs/step-by-step-plan.md)一次做一个小步骤。任何前端工作开始前须先暂停并讨论设计规范与样式。

DocuCheck 是一个正在开发的本地采购资料初审 Agent 工作台。目标交互是上传资料后提问，由单 Agent 调用受控工具并返回带来源的答复；八字段正式结论由规则引擎计算。现有资产包括基础环境、模拟文档、PDF/XLSX 解析、快照证据、词法检索和 DeepSeek Provider；单次真实模型工具调用及三批模拟资料抽取均已有用户反馈。内存抽取、证据校验和规则已完成模拟包复核；Agent 循环与 CLI 已通过离线测试及一次完整的模拟包真实两轮对话。Linux worker 只有启动与健康检查骨架；正式 API、数据库迁移和前端尚未实现。

AI 协作上下文见 [CLAUDE.md](CLAUDE.md)；Codex 入口为 [AGENTS.md](AGENTS.md)。当前设计基线是 [Agent 架构 v1.0](docs/superpowers/specs/2026-09-24-agent-first-design.md)，涵盖模型、状态、工具、上下文、RAG、恢复和评测；M1 以外的正式业务能力仍按路线图实施。

2026-09-24 已按 Agent 为中心重排计划。[M1 实施计划](docs/superpowers/plans/2026-09-24-agent-mvp.md)中的模拟包内存链路已完成验收；进入 M2 前先确定下一小步。历史阶段 0/1 名称不代表新的里程碑进度。

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
