# DocuCheck

DocuCheck 是一个正在开发的本地采购资料初审工作台。当前仓库处于阶段 0：环境验证和模拟文档基线。API、worker 和前端尚未实现。

## 当前范围

首版处理一份办公笔记本采购需求及最多三家供应商的报价和规格书，比较八个字段，并保留可以回到原件的证据。设计与阶段计划见 [开发路线](docs/development-roadmap.md) 和 [阶段 0 设计](docs/superpowers/specs/2026-09-24-phase-0-foundation-design.md)。

## 本地 Python 环境

在 Miniconda PowerShell 中运行：

```powershell
conda env create -f backend/environment.yml
conda activate docucheck
python --version
```

已在开发机核对 Python 3.12.14。当前 `environment.yml` 只固定 Python 主次版本；应用依赖和 Windows/Linux 锁定文件将在安装兼容性验证后补充。

## 数据与状态

`examples/` 将保存完全模拟的开发样例。独立验收集尚未建立，文档中的质量与性能数值均为目标，尚无实测结论。真实资料必须由资料持有人明确授权后使用。

Docker Desktop Linux Engine 当前无法启动，见 [环境记录](docs/environment/2026-09-24.md)。项目不会因为此故障改为原生 Windows Celery worker。
