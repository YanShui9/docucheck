# 最新版本完整联调：第 1 项完成记录

日期：2026-10-03。范围：按用户要求逐项推进，本次完成“最新版本完整联调”。其余五项不在本次完成范围内。

## 结论与版本

当前后端、重建后的 worker/dispatcher/cleaner、真实模型和既有 M4 工作台已完成合成开发资料范围内的联调。发现并修复一处 Case 列表版本显示问题。此次通过表示当前开发版本链路可运行；独立封存、正式独立质量验收和生产发布仍未完成。

| 项目 | 本次版本或范围 |
| --- | --- |
| Git 基点 | `efcc62ab25e9e06dc4b4bba4a16d04fcfcd8c867`；工作区含此前尚未提交的实现，基点本身不代表本次源码 |
| 后端源码 | `backend/app` 的 76 个 Python 文件，规范化逐文件 SHA-256 清单指纹 `8f696a670171a972e1e2af78029cee2411008ec56b1fbf5d6276ab91bbfc2b2d` |
| 规则 / 模型 | `fields-v4` / 配置的 `deepseek-flash`，OpenAI 兼容接口；没有独立认证上游模型身份 |
| 数据库迁移 | `0011_document_model_binding (head)` |
| Worker 镜像 | `docucheck-current-validation:20261003`，镜像 ID `sha256:c82173b4b1ef547ee35beb5e79ea76b7f640ff883eb66d1c9a499a1894aa2363` |
| Worker 依赖 | Python 3.12.15，75 项锁定依赖逐项匹配，`pip check` 通过 |
| 本机环境 | 已有 `docucheck` / `docucheck-mineru` Conda 环境，主 Python 3.12.14；Node 24.14.1，npm 11.11.0 |
| 资料 | 12 包、13 家、104 格的 AI 复核且作者已见的合成开发资料；独立人工封存仍为 0 |

前端修复发生在真实质量和性能测试之后；后端及模型协议绑定的源码未改变。质量和性能原始报告保持原字节，不用后来的界面修复重新命名历史成绩。最终前端文件指纹见[机器记录](../evals/reports/m5-current-version-integration-2026-10-03.json)。

## 实际执行

1. 检查源码、Git、依赖和已有服务，发现原 Compose worker 仍是旧源码，且原业务库有历史待处理任务。
2. 重建使用完整锁定依赖的镜像，启动独立 Compose 项目 `docucheck-validate-20261003`：MySQL `23308`、Redis `26380` 以及新的 worker、dispatcher、cleaner。原 `docucheck` 服务、业务库和卷保持原状态。文件存储仍使用本机 `data/` 根目录，测试以随机 Case/存储键隔离，未宣称文件系统完全独立。
3. 在独立业务库迁移至 head，运行全部 22 项显式服务测试；启动指向该库的 API、MinerU 解析进程和既有 Vite 前端。OIDC 复用本机身份服务。
4. 先固定协议，再依次执行 12 包真实质量核验和固定五文件场景的 10 次性能测试；没有自动隐藏或重试失败。本轮这些预定任务均成功。
5. 验证追问、排队取消、未知供应商澄清、原文支持的同源事实修正；通过真实浏览器检查矩阵、导出、账号隔离和新建上传到修正后历史读取的链路。
6. 对显示问题先写回归并观察失败，再修复、重跑前端测试/类型检查/构建；另由只读 AI 审查者复核源码、版本、报告和导出一致性。

重建而非仅重启 worker，采用 [Docker Compose 对镜像重建及容器重建的说明](https://docs.docker.com/reference/cli/docker/compose/up/)作为操作参考；任务确认、重投和幂等行为的审查参考 [Celery 官方任务文档](https://docs.celeryq.dev/en/stable/userguide/tasks.html)。本次证据来自下方实测记录。

## 验证结果

| 验证 | 结果 | 实际覆盖与限制 |
| --- | --- | --- |
| 外部服务测试 | **22 passed，0 skipped**，pytest 显示 131.49 秒 | 真实 MySQL、Redis/Celery、解析、API/权限、持久化、修正后台链路和清理/崩溃恢复；这些测试使用本地测试 Provider，不另计付费模型请求 |
| 真实模型质量 | 任务 **12/12**，状态 **104/104**，必要证据 **89/89** | 开发集；危险“满足” **0**，引用错误 **0/244**；分母保留，不能冒充独立未见集 |
| 固定五文件性能 | **10/10** 成功且不超过 120 秒 | 最低 50.531 秒，中位数 53.5545 秒，最近秩 P95/最高 62.125 秒；预定门槛 9/10；均为新 Case，复用已加载的解析器资源 |
| 额外业务边界 | **4/4** 通过 | 范围追问、排队取消、未知供应商、同源修正；不并入原 104 格质量分母 |
| 排队取消 | **39.0 ms** | 只测派发前取消，未测正在进行的上游推理中断；该 Run `usage=null`，不算零费用 |
| 修正后规则重算 | **74.33 ms，0 次模型调用** | 同一原文支持旧、新容量时保留二者，结果为“资料冲突”，`needs_review` |
| 前端最终验证 | **19 passed / 4 文件**，类型检查及生产构建通过 | 修复前初始 18 项记录另行保留；先失败 1 项再通过的回归可追溯 |
| 静态 / 依赖 | Ruff、Conda 及镜像依赖检查通过 | 本轮未重跑历史的全后端 765 项；此前离线回归与本轮 22 项服务测试分别记录 |

性能测试的重复 160 格、150 条必要证据仅用于固定场景一致性检查，不增加独立质量样本数。未做并发压测、全类型采购资料覆盖或冷机 MinerU 安装。

## 浏览器与业务结果

使用真实 OIDC 演示账号完成以下检查：

- Alice 的两供应商矩阵有 16 格，逐格与 API 原始质量观察和页面 JSON 导出一致。
- 实际页面生成的 JSON/XLSX 导出载荷落盘后重新打开：16 格结果及正式标记、31 条来源逐字段一致，XLSX 三个工作表为“运行信息”“核验结果”“原文来源”。浏览器自动化原生保存曾报告完成但未找到文件，因此使用页面实际 Blob 字节保存并校验；**未宣称原生浏览器保存已经验证**。
- 页面检查 PDF 原件页及 XLSX 定位。PDF 截图可检查原件页；保存的 XLSX 截图没有清楚呈现单元格原文，不单独作为预览正确的证明。导出载荷的原文、单元格定位与原始 API 来源已核对。
- Bob 用真实令牌读取 Alice 的 Case、指定快照证据及指定快照原件，均为 404；项目列表为空，成功读取自己的项目接口。检查使用真实资源路径，不以不存在的路由返回 404 代替授权测试。
- 从空 Case 创建、上传三份新原件并解析，取得当前版本 3 的八字段完整结果；原件 SHA-256 与输入清单一致。对仅支持 512 GB 的规格事实试图改为 1 TB，被 `unsupported_correction` 拒绝，Case 版本仍为 3。
- 对报价原文同时支持的 512 GB / 1 TB 提交事实修正，产生版本 4；版本 3 答复和业务结果保留。再运行版本 4 的完整审查，Run `completed`、八格处理完成，存储仍为“资料冲突”。

版本 3 历史结果全部标为非正式。版本 4 虽然是当前版本，但修正处于 `needs_review`，按现有快照级契约八格也全部非正式。**任务完成不等于人工确认已经解除**。后端契约见 [api_queries.py](../backend/app/db/api_queries.py)，旧、新值保留见 [correction_recheck.py](../backend/app/db/correction_recheck.py)。

## 本次修复与复审

问题：事实修正后主区域已读取新版本，左侧 Case 列表仍显示旧版本。原因是 `selectCase` 刷新了文档/历史数据，却未同步列表中该 Case 的 `revision`。

修复：[App.vue](../frontend/src/App.vue) 在已有 `selectionVersion` 防止迟到响应覆盖选择的守卫内，仅更新对应 Case 的版本号。新增 [App.test.ts](../frontend/src/App.test.ts) 回归，验证列表升版时用户选定的历史快照仍保留。没有改动界面规范、样式、后端或历史数据。

测试先得到 1 failed / 4 passed，失败仅为列表旧版本；修复后完整前端 19 项通过，类型检查和构建成功；浏览器重新选择该 Case，列表和当前区域均显示版本 4，历史版本 3 仍可读取。

限定范围的另一位只读 AI 审查者未发现可证实的 P0/P1/P2 应用问题；复核了版本指纹、22 项记录、质量汇总、十次时延及 16 格/31 来源的导出一致性。其指出的最终前端记录和截图限制已在本报告及机器记录中明确。它不替代独立人员复核或安全审计。

## 模型用量与费用

| 场景 | 模型调用 | 输入 token | 输出 token |
| --- | --- | --- | --- |
| 12 包质量 | 110 | 245081 | 24491 |
| 10 次性能 | 151 | 371870 | 38166 |
| 四项边界中的推理 | 13 | 30918 | 2752 |
| 浏览器两次完整审查 | 19 | 38346 | 4391 |
| **有上报的合计** | **293** | **686215** | **69800** |

价格、缓存命中/未命中分类和实际账单未核对，金额保持未知。已上报的调用 `unknown_usage_calls=0`；排队取消记录的空用量单列，不替换成零账单。

## 证据与当前运行状态

本轮新证据目录：[data/m5-current-validation-20261003](../data/m5-current-validation-20261003/)。协议、原始任务、报告、失败回归证据和浏览器读回保留在本机被 Git 忽略的目录；未写入 API key、密码或浏览器身份状态文件。

- [22 项服务测试 XML](../data/m5-current-validation-20261003/service-tests.xml)、[日志](../data/m5-current-validation-20261003/service-tests.log)
- [质量原始报告](../data/m5-current-validation-20261003/quality/report.json)、[性能原始报告](../data/m5-current-validation-20261003/performance/report.json)、[边界报告](../data/m5-current-validation-20261003/boundaries/report.json)
- [最终前端记录](../data/m5-current-validation-20261003/frontend-final-verification.json)、[初始前端记录](../data/m5-current-validation-20261003/frontend-verification.json)
- [导出一致性](../data/m5-current-validation-20261003/browser-export-consistency.json)、[矩阵读回](../data/m5-current-validation-20261003/browser-matrix-readback.json)、[Bob 隔离](../data/m5-current-validation-20261003/browser-bob-isolation.json)
- [历史/修正](../data/m5-current-validation-20261003/browser-history-and-correction.json)、[费用未知的用量汇总](../data/m5-current-validation-20261003/usage-summary.json)
- [源码与运行服务](../data/m5-current-validation-20261003/service-versions.json)、[机器汇总与证据哈希](../evals/reports/m5-current-version-integration-2026-10-03.json)
- [收尾哈希与文档链接检查](../data/m5-current-validation-20261003/final-audit.json)

收尾运行状态以本轮机器记录的最后检查为准。独立联调栈使用 23308/26380，原栈使用 13307/6379；原栈旧 worker 未替换。API/前端若保持运行，当前页面读取独立联调库。普通终端以后直接启动 API 不会自动继承本轮隔离参数；本机辅助入口 `data/m5-current-validation-20261003/runtime.py` 留作显式选择，不改变 `.env`。

本轮构建实际解析到 `python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016`，但 Dockerfile 的浮动基础镜像声明未锁定。镜像成功构建不代表完整冷安装和生产部署条件均已完成。

## 接下来五项

按用户要求继续逐项进行。下一项为第 2 项；不能将作者已见的 12 个开发包更名为独立未见样本。

| 项目 | 当前缺口 |
| --- | --- |
| 2. 独立资料复核与封存 | 准备隔离于调试的新资料、独立人员逐格核对与至少 6 包封存；当前独立人工封存 0 |
| 3. 实际模型账单 | 核对价格、缓存分类、账单与请求量；金额未知 |
| 4. 源码交付及远程 CI | 当前修改未整体提交/推送，GitHub Actions 首次远程运行尚未完成 |
| 5. 完整部署复现 | 本轮 worker 锁定镜像已构建；Conda/MinerU 冷安装、基础镜像声明固定及完整启动复现仍待验收 |
| 6. 灾备与生产条件 | 业务数据库、上传原件、OIDC 恢复演练及正式部署条件仍未完成 |
