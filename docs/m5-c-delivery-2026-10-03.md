# M5-C 开发交付与复现验证报告

日期：2026-10-03。范围：M5 第三个大步骤——依赖复现、离线 CI、开发证据归档、恢复说明和面试交付。前两个大步骤的真实模型记录见 [M5-B 报告](m5-b-development-validation-2026-10-03.md)；本次机器记录见 [M5-C 验证汇总](../evals/reports/m5-c-delivery-2026-10-03.json)。

**M5-C 开发交付已完成；M5 正式独立验收仍未完成。** 本轮没有新增真实模型调用，没有改变前端设计，也没有覆盖现有数据库。开发资料是作者已见、AI 复核的合成集，独立人工封存为 0，实际模型账单及缓存计费分类仍未知。

## 1. 这一步实际做了什么

| 交付 | 实现与用途 |
| --- | --- |
| [离线测试网络保护](../scripts/offline_guard.py) | 在 pytest 收集测试前阻断网络连接和 DNS；即使业务代码捕获了网络异常，测试会话仍失败。保留 MockTransport、ASGI 进程内测试和标准库本机 socketpair IPC。 |
| [统一验证入口](../scripts/verify_offline.py) | 检查 Python 3.12 与中文字体，清除外部服务 opt-in、模型配置及 tracing 环境变量，顺序执行 pip check、Ruff、离线 pytest，独占创建结果文件。 |
| [开发完整锁](../backend/requirements-dev.lock.txt)、[Windows 完整锁](../backend/requirements-windows.lock.txt) | 固定 97 个直接及传递依赖；Conda 仍为本机主环境，隔离 venv 用于验证新安装。 |
| [worker 完整锁](../backend/requirements-worker.lock.txt) | 固定 75 个运行依赖；[worker Dockerfile](../backend/Dockerfile.worker) 改为安装完整锁并执行 pip check。 |
| [GitHub Actions](../.github/workflows/offline-ci.yml) | 后端离线检查、现有前端安装/测试/构建分两个 job；只读仓库权限，Action 使用完整 commit SHA，不提供业务凭据。 |
| [开发证据归档工具](../evals/delivery_bundle.py) | 默认预览；显式 build、verify、restore。固定白名单、逐文件哈希、ZIP 路径与容量校验、敏感信息检查，新目录恢复且拒绝覆盖。 |
| 四份手册与隔离恢复配置 | 安装/启动、备份/恢复、十分钟演示、架构答辩；[恢复 Compose](../ops/compose.restore.yaml) 使用独立卷和本机端口 23307。 |

新增测试 **34 项**：[离线保护 12 项](../backend/tests/test_offline_verification.py)、[归档恢复 22 项](../backend/tests/test_delivery_bundle.py)。包括被捕获的网络错误、真实 ASGI TestClient、路径逃逸、重复条目、符号链接、伪造独立验收声明、敏感内容、篡改、缺文件、已有目录及源码版本变化等边界。

业务源码 `backend/app/` 的 76 个 Python 文件与 M5-B 最终实测版相同，路径/内容清单指纹为：

```text
f3938cda07b0b365ed3477b6ede3ae11fbdea4c08fe7b2483a1e85a264e9ce71
```

Git HEAD 为 `efcc62ab25e9e06dc4b4bba4a16d04fcfcd8c867`，分支为 `phase-0-foundation`；当前交付还包含未提交的 M3–M5 改动。必须同时保留当前工作区、锁和报告，不能只检出这个旧 HEAD 就称为同一版本。没有自动提交、推送或发布。

## 2. 实际测试结果

三个环境均使用 `python -m scripts.verify_offline --report <新结果文件>`；每个结果包括 pip check、全目录 Ruff、pytest 输出和网络尝试计数。

| 环境 | 安装与隔离情况 | 后端结果 | pytest 用时 | 网络尝试 |
| --- | --- | --- | --- | --- |
| 原本机 Conda，Python 3.12.14 | 保留原环境，未升级其中 SDK | **724 passed、22 skipped** | 135.88 秒 | 0 |
| Windows 新 venv，Python 3.12.14 | 无系统 site-packages；97 个锁定版本全部匹配 | **724 passed、22 skipped** | 141.24 秒 | 0 |
| Linux 新 venv，Python 3.12.15 | Debian trixie；中文 TTC 字体；Docker `--network none`；97 个锁定版本全部匹配 | **724 passed、22 skipped** | 84.83 秒 | 0 |

原始记录：[本机](../data/m5-c/offline-host.json)、[Windows 隔离环境](../data/m5-c/offline-clean-windows-final.json)、[Linux 隔离环境](../data/m5-c/linux-results/offline-linux-final.json)。**22 个跳过均为需要显式开启的 MySQL、Redis、Celery、MinerU 等外部服务测试，本轮没有把它们算作通过；没有字体导致的跳过。** 外部链路已测范围沿用 M5-B 和此前阶段报告。

Windows/Linux 新环境的 OpenAI SDK 为 3.23.0、LangSmith 为 0.14.3；原 Conda 环境的旧版本保留。新 Linux 环境也先单独安装 75 个 worker 锁定依赖并通过 pip check，再安装开发锁执行完整回归。安装读回记录见机器汇总。

前端在新目录安装，无现有 node_modules 复制：Node 24.14.1、npm 11.11.0；`npm ci --ignore-scripts` 成功，4 个测试文件、**18 项测试通过**，`vue-tsc --noEmit` 和 Vite 生产构建通过。npm 当时报告 0 个已知漏洞，同时保留 whatwg-encoding/glob 的弃用提示；这不等于生产供应链审计。证据见 [前端验证记录](../data/m5-c/frontend-verification.json)。本轮未启动前端开发服务器。

## 3. CI 与环境复现的边界

CI 使用 Ubuntu 24.04、Python 3.12.14、Node 24.14.1，后端安装文泉驿字体并使用与本机相同的验证入口。实际本地 Linux 测试是 Debian/Python 3.12.15，两者分别记录。工作流已经配置与静态校验，**尚未推送仓库或运行远程 GitHub job**，不能称为云端 CI 已绿。配置方式参考 [GitHub Python CI](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)，完整 SHA 固定参考 [GitHub Actions 安全指南](https://docs.github.com/en/actions/reference/security/secure-use)，字体包参考 [Ubuntu 文泉驿](https://packages.ubuntu.com/noble/fonts-wqy-zenhei)。

Linux 校验镜像 `docucheck-m5-c-check:20261003` 实际 ID：

```text
sha256:fc5ec287b9b75e4113011bd845b588e76d4caf342eaf2ca34ced0b94900f6529
```

它以本机已有 worker 镜像 `sha256:80965e9ed93b66739a1603d9e6a275a92fcd8d7cd856d623f9063aee2e433346` 为基础，创建新的 venv 安装锁；没有重新制作 Conda/MinerU 环境或冷下载模型资产，也没有重启现有 worker/dispatcher/cleaner。Docker Hub 查询新的 Python 基础镜像 digest 因连接超时失败，未填入猜测的 digest。生产 Dockerfile 中的可变基础标签仍是发布前事项，不能据此宣称整个生产镜像已可逐字节复现；应按 [Docker 固定基础镜像建议](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions) 完成实际构建和验证。

软件 socket 保护用于避免测试误访问服务，不是恶意代码沙箱；Linux 额外通过 `--network none` 验证。CI 安装依赖会访问包仓库，离线限制针对随后执行的后端测试。

## 4. 实际归档和恢复结果

最终归档：[docucheck-m5-development.zip](../data/m5-c/docucheck-m5-development.zip)。

```text
SHA-256: 7627a71b702237eb4b9722569918505fb9f5fa3c7ee480387dc6d9bfa3b55b8d
ZIP 大小: 1,362,843 bytes
白名单载荷: 227 files / 4,420,532 bytes
恢复后: 228 files（包括 bundle-manifest.json）
```

已经实际 build、verify，并恢复到新的 `data/m5-c/restored-development-final/`；随后重新运行候选资产校验：**12 包、13 家供应商、104 格、38 原件、36 页 PDF，错误 0**。记录：[构建/核验/恢复](../data/m5-c/bundle-verification-final.json)、[恢复后资料校验](../data/m5-c/restored-corpus-validation-final.json)。较早草稿归档及其报告另名保留，未冒充最终归档。

载荷包含合成原件、拟定 gold、AI 开发复核、六轮完整质量记录、十次最终性能记录、边界测试及早期失败、M5-B 冻结汇总、依赖锁和四份手册。未携带 `.env`、业务上传目录、身份导入文件、私有 CA 或数据库。该 ZIP 是开发证据包，项目源码、M5-C 验证汇总和本报告仍单独交付。

哈希保证与可信基准的一致性；它不是数字签名，也不证明任意提供者的文件来源。资料校验的 `business_acceptance_measured=false` 指资产工具未测业务质量；真实开发质量仍由 M5-B 独立评分工具的历史运行报告表达。

## 5. 审查中发现并解决的问题

| 问题 | 修复与验证 |
| --- | --- |
| 禁网误伤 Windows asyncio 的标准库 socketpair | 只允许标准库内部代码对自身绑定的 loopback 地址创建 IPC；普通 localhost 连接仍拒绝，真实 ASGI TestClient 回归通过。 |
| 子 pytest 扫描系统 Temp 中无关的 PyCharm socket | 子测试明确 `--rootdir`、`--confcutdir` 到自己的临时目录；保留首次 722 passed/1 failed 记录。 |
| 网络计数尾行与 pytest 进度粘连 | 显式先写空行，再写独立计数行；增加真实子进程断言。Windows/Linux 的 723 passed/1 failed 记录保留。 |
| Windows Docker COPY 使 Python 文件呈可执行模式 | 只在临时验证镜像中规范文件权限；没有关闭 Ruff 或给业务文件加虚假 shebang。最初 182 个 EXE002 检查失败保留。 |
| 恢复资料校验文档命令参数错误 | 改为候选工具实际支持的 `--root`、`--output`，按最终路径执行成功。 |
| 恢复身份服务后可能无法访问旧 Case | 明确权限身份依赖 OIDC issuer/sub；变更 issuer 需要保留逻辑地址或另行验证权限迁移，不能只换端口就宣称恢复完整。 |

本阶段为实现者自审及自动验证，没有另外的独立代码审查者。上述历史失败和最终输出均保留在机器汇总所列文件中。最终文档/链接/哈希/Ruff/diff 检查另见 `data/m5-c/final-audit.json`。

## 6. 交付使用方式

1. [安装和启动](reproduction.md)：先按锁安装 Python/Node 环境、检查字体；再按需要启动 OIDC、MySQL、Redis、解析、API 与 worker，按健康检查确认。凭据仅在本机配置。
2. [备份与恢复](backup-and-recovery.md)：区分开发 ZIP 恢复与业务数据库/原件/身份服务恢复；后者使用独立实例，不覆盖原数据库。数据库原始导出参考 [MySQL mysqldump](https://dev.mysql.com/doc/refman/8.4/en/mysqldump.html)，卷备份参考 [Docker Volumes](https://docs.docker.com/engine/storage/volumes/)，身份服务操作参考 [Keycloak 导入导出](https://www.keycloak.org/server/importExport)。
3. [十分钟演示](interview-demo.md)：空 Case 上传、八字段矩阵、来源追问、证据约束下的修正、不可变历史与导出；离线历史、脚本模型和已授权真实模型分别说明。
4. [架构与面试答辩](architecture-and-interview.md)：受控 LangGraph Agent、检索/全量覆盖、模型候选/规则结论分工、预算、上下文、Outbox、幂等、fence 与中断重试。

## 7. 仍需正式完成的条件

- 独立人员真实复核和至少六包封存；当前 AI 开发复核不能替代这一项。
- 可核对的真实账单、缓存计费拆分，以及独立资料质量/性能复测。
- 业务数据库、上传原件和 OIDC 身份一致性灾备演练，测量 RPO/RTO；本轮只实际恢复了开发 ZIP，恢复 Compose 仅通过配置校验。
- 全新 Conda/MinerU 冷安装、模型资产下载、未来 worker 完整镜像构建、基础镜像固定与漏洞门禁。
- 远程 CI 首次执行、生产 TLS/SSO、对象存储、容量/并发与正式部署验收。

这些条件不妨碍当前开发交付与面试演示准备完成，也不应包装成已经通过企业生产验收。M5 保持原来的三个大步骤；下一项可使用现有十分钟脚本进行演练，无需新增一串开发小阶段。
