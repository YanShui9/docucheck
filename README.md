# DocuCheck

DocuCheck 是一个正在开发的本地采购资料初审 Agent 工作台。目标交互是上传资料后提问，由单 Agent 调用受控工具并返回带来源的答复；八字段正式结论由规则引擎计算。阶段 0 的环境验证、模拟文档基线和基础容器服务已完成；阶段 1 已开始实现 PDF/XLSX 解析与证据位置。Linux worker 已有启动与健康检查骨架；Agent、模型调用、API、审核规则和前端尚未实现。

AI 协作上下文见 [CLAUDE.md](CLAUDE.md)；Codex 的入口文件为 [AGENTS.md](AGENTS.md)。

## 当前范围

首版处理一份办公笔记本采购需求及最多三家供应商的报价和规格书，比较八个字段，并保留可以回到原件的证据。设计与阶段计划见 [开发路线](docs/development-roadmap.md)、[单 Agent 设计](docs/superpowers/specs/2026-09-24-single-agent-review-design.md)和 [阶段 0 设计](docs/superpowers/specs/2026-09-24-phase-0-foundation-design.md)。

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

阶段 1 的解析代码位于 `backend/app/parsing.py`。在已有的 `docucheck` Conda 环境中，可调用独立 MinerU 环境的可执行文件生成模拟包证据索引：

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
