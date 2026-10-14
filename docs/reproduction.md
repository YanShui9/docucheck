# 安装、离线验证与本地启动

适用日期：2026-10-13。Windows + Miniconda 是开发入口；Linux Celery worker 由 Docker 运行。各命令在仓库根目录执行，前端命令除外。项目是开发中的中文采购助手，具备身份、权限、异步任务和证据版本；当前没有公网演示站。

先按第 1 节运行无需模型和 Docker 的离线验证。完整网页需要配置额外服务，启用 worker 后会产生实际模型调用。品目对应前端与指定合成应用闭环已通过[10 月 11 日验收](document-item-binding-c-acceptance-2026-10-11.md)；当前语义与可靠性缺陷按[收尾计划](superpowers/plans/2026-10-13-project-consolidation.md)修复。安装、健康检查和历史回放均不等于稳定版本验收，现场须核对实际源码与迁移。

## 1. 先复现代码测试

新机器取得完整源码；已有工作区不必重复克隆。运行数据与账号在本机创建，避免复制他人的 `.env`、`data/`、`.venv` 或 `node_modules/`。

```powershell
git clone https://github.com/YanShui9/docucheck.git
Set-Location docucheck
```

```powershell
conda create -n docucheck-repro python=3.12.14 pip -y
conda activate docucheck-repro
python.exe -m pip install --no-deps -r backend/requirements-dev.lock.txt
python.exe -m pip check
```

开发、Windows、基础 worker 和向量 worker 使用仓库中的不同完整锁，以各锁文件为准。`backend/environment.yml` 是 Conda 引导文件，精确复现再安装完整锁。`--no-deps` 要求锁闭包完整，随后 `pip check` 检查缺包及版本约束。历史新环境验收与当前源码 CI 分别记录，不能把旧锁的通过成绩自动转移到新依赖。

Milvus SDK 为 2.6.17，可选闭包见 `backend/requirements-vector.lock.txt`（17 包，与主锁共有版本一致）；本机与服务 2.6.25 实连及安装后完整离线回归已通过。新增 SDK 闭包的 Linux 新环境安装尚未实测，历史 M5-C 三环境结果对应旧 97 包锁，不能当作新锁验收。详见 [Milvus 实连报告](milvus-live-acceptance-2026-10-03.md)。

Windows 默认使用 `C:/Windows/Fonts/simhei.ttf`；如不存在，设置自己有使用权限的中文 TrueType 字体：

```powershell
$env:DOCUCHECK_CJK_FONT = 'C:/Windows/Fonts/simhei.ttf'
python.exe -m scripts.verify_offline --report data/verification/backend-first.json
```

Linux CI 安装 `fonts-wqy-zenhei`，字体路径为 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`。缺字体直接失败，不能用跳过资料测试冒充完成。不同字体生成的 PDF 哈希可能不同；复现历史资料请恢复原归档，不重新生成后冒充同一份原件。

脚本执行依赖检查、Ruff、全部后端离线测试；清除全部 `DOCUCHECK_*` 运行配置（只保留显式测试字体），包括评测令牌、OIDC口令、真实存储/解析路径，以及模型/数据库/跟踪设置，并在 pytest 收集前拦截 TCP/DNS。MockTransport 与进程内 ASGI 测试保留。Windows 标准库 socketpair 的内部通信是严格限定的例外，普通 localhost 请求仍被拒绝。拦截器是测试防误调用机制，**不是对恶意代码的安全沙箱**；Linux 演练额外使用 Docker `--network none`。已存在的报告不覆盖，下一轮使用新文件名。

前端使用 Node 24.14.1、npm 11.11.0 及 `package-lock.json`：

```powershell
Set-Location frontend
npm.cmd ci --ignore-scripts
npm.cmd test
npm.cmd run build
Set-Location ..
```

以上流程不启动服务、不需要 Docker/MySQL、账号或模型 key。GitHub Actions 的两个 job 使用相同后端命令和现有前端测试/构建，Action 固定完整 commit SHA、权限为只读；网络安装依赖发生在测试准备阶段。[GitHub Python CI](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)、[Action 安全配置](https://docs.github.com/en/actions/reference/security/secure-use)。

## 2. 恢复历史开发资料

**创建新归档只在拥有原始合成资料、历史报告及对应实测源码的开发机执行。** 默认命令只预览，`--build` 才创建归档；每次选择新文件，不覆盖已经测量的交付版本。交付后的审查已修改六个业务文件；M5-B 的真实模型成绩仍属于原来的源码指纹。当前工作区不能用于重打那一版实测包；应通过 `--repo` 显式指定保留的实测资料目录：

```powershell
$measuredRepo = 'data/m5-c-recheck/source-for-rechecked-bundle'
python.exe -m evals.delivery_bundle --repo $measuredRepo
$bundleOutput = Join-Path 'data' ('m5-delivery-' + [guid]::NewGuid().ToString('N') + '.zip')
python.exe -m evals.delivery_bundle --repo $measuredRepo --build --output $bundleOutput
```

**接收者的新机器只做 verify/restore，不运行需要原始 `data/` 的 preview/build。** 先把取得的 ZIP 放到自己选定的位置，并与可信渠道提供的整体 SHA-256 比较；下面路径按实际交付文件修改：

```powershell
$archivePath = 'data/m5-c-recheck/docucheck-m5-development.zip'
Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
python.exe -m evals.delivery_bundle --verify $archivePath
$restoreDir = Join-Path 'data' ('m5-restored-' + [guid]::NewGuid().ToString('N'))
python.exe -m evals.delivery_bundle --restore $archivePath --destination $restoreDir
python.exe -m evals.candidate_corpus --root (Join-Path $restoreDir 'data/m5-candidates') --output (Join-Path $restoreDir 'restored-corpus-validation.json')
```

恢复目标必须不存在；不会把旧结果导入当前数据库。上面的 `$measuredRepo` 是原开发机实际保留的目录，不是 Git 克隆会自动提供的内容；接收者只需要取得 ZIP 和当前校验工具。归档包含 12 包合成原件、拟定 gold、AI 复核、各版本整轮观察与报告、交付文档和依赖指纹；不含账号、`.env`、私有 CA、数据库或用户上传目录。标签和报告只用于离线核对，**不作为 Agent 输入**。保留失败任务、未知 usage 和独立封存 0 的声明。ZIP 哈希防意外改动，不能证明提供者身份；来源可信性需另保证。旧归档仍可校验；本次复查的归档版本和当前源码测试分别记录，不把旧模型分数改称新源码实测成绩。

## 3. 配置本地服务

Docker Desktop 切换 Linux Engine；从 `.env.example` 在本机首次建立 `.env`，已有文件保留。为两项 MySQL 口令填入不同的随机值。OIDC 口令由下面的生成器写入本机文件。模型配置由账号持有人自行填写，勿提交或发送 key：

| 配置 | 用途 |
| --- | --- |
| `DOCUCHECK_MYSQL_HOST` / `PORT` | Windows API/解析进程连接 MySQL；默认主机端口 3307，本机因 Windows 保留端口改用 13307。容器内部始终 3306。 |
| `DOCUCHECK_MYSQL_ROOT_PASSWORD` / `PASSWORD` | 数据库管理员和应用账号口令，彼此不同。 |
| `DOCUCHECK_WEB_PORT` | 根目录 `.env` 中的本机前端端口，默认15173；Vite和OIDC生成器/同步器共用。 |
| `DOCUCHECK_MODEL_PROVIDER` | 当前为 `openai-compatible`，没有默认假模型回退。 |
| `DOCUCHECK_MODEL_BASE_URL` / `ID` | 本次实测为 `https://api.deepseek.com` / `deepseek-flash`；更换模型须重新验证工具调用契约。 |
| `DOCUCHECK_MODEL_API_KEY` | 仅本机或目标部署的密钥管理存储。 |
| `DOCUCHECK_MODEL_CONTEXT_WINDOW` | Provider 配置容量；不取消应用自己的 prompt、调用、时间和 token 限额。 |

```powershell
python.exe -m backend.app.dev_oidc_realm
docker compose up -d mysql redis
docker compose --profile identity up -d keycloak
python.exe -m alembic -c backend/alembic.ini upgrade head
python.exe -m alembic -c backend/alembic.ini current
```

当前源码迁移头为 `0018_upload_intents`，增加上传意图与原件引用索引。10月11日指定演示曾应用`0017_item_bindings`；10月13日收尾B已匹配备份并迁移业务实例到0018，最终运行源码41eb967，见[B服务验收](repair-consolidation-b-services-2026-10-13.md)。后续实例须重新核对实际身份，不能只凭源码推断运行版本。2026-10-13 已在临时 MySQL 8.4.11/RR 库验证 `0017 → 0018 → 0017 → head`、引用索引与13项竞争/恢复测试，临时容器已删除；这不替代业务实例应用。新安装执行上述 `upgrade head` 会应用最新迁移；已有实例先匹配备份、排空旧 API/cleaner，确认隔离验收适用于目标环境，再按阶段应用新 API/cleaner。无意图的旧孤儿另行对账，不由本轮默认清理器自动删除。保留过期意图可回收断连上传的迟到文件；有待恢复意图时不得直接降级删除账本。新 v7/v8 规则及引用语境协议写入后，回退代码也必须能读取这些身份，不能仅 checkout 旧源码宣称已安全回退。生成器创建 alice/bob/reviewer 的演示账号，密码只在本机 `.env` 查看。Keycloak `start-dev`、本机 HTTP 和 H2 卷用于演示；正式企业部署需 HTTPS、生产配置、身份数据库和企业 SSO。已有 Keycloak realm 不会因重写导入文件自动更新，不能把重新生成文件当成重置现有账号。

已有本机 Keycloak 更换前端端口时，在根目录 `.env` 设置 `DOCUCHECK_WEB_PORT`，保持当前 Keycloak 运行，执行下列命令，再重启 Vite。端口只接受1–65535的字面十进制值，两端都忽略模式文件和 `.env.local` 对此键的覆盖，不展开 `${...}`；若用进程环境变量覆盖，须在同一个环境内执行同步和前端启动。它只同步既有 `docucheck-web` 的登录/退出回调和来源地址，并读取回验；不生成凭据、不重建 realm 或账号。默认仅绑定 `127.0.0.1`，不要用 `--host`/`--port` 覆盖绕开这份共用配置。

```powershell
python.exe -m backend.app.dev_oidc_realm --sync-client
```

## 4. 开四组进程

| 进程 | 运行位置 | 作用 |
| --- | --- | --- |
| MySQL/Redis/Keycloak | Docker | 状态真值、任务队列、可信登录。 |
| worker/dispatcher/cleaner | Docker | 执行 Agent、派发 Outbox、受控删除清理。 |
| 解析进程与 API | 两个本机终端，docucheck 环境 | MinerU/OpenPyXL 解析、受保护 HTTP API。 |
| Vite | 本机 frontend 目录 | 专业审查工作台，开发代理至 API。 |

PDF 解析依赖单独的 MinerU 环境。本机验证版本为 MinerU 4.0.7、Python 3.12.14，使用 Basic/text 模式；安装方法和解析边界见[环境记录](environment/2026-09-24.md)。新机器可在独立 Conda 环境安装 `mineru==4.0.7` 并核对 `mineru-kit version` 和 `pip check`，首次解析可能下载模型资产。此环境不属于本轮 97 包锁与离线 CI，**未在 M5-C 再安装或重新下载模型**。

```powershell
# 本机终端 A：先在 .env 的 DOCUCHECK_MINERU_EXECUTABLE 配置自己的解析器路径
# 只演示 XLSX 时不需要 MinerU；未配置解析器不会伪造 PDF 解析完成
python.exe -m backend.app.parse_background
```

```powershell
# 本机终端 B：必须已激活 Python 环境
./scripts/start-api-oidc.ps1
```

```powershell
# 项目根目录：这一步允许 worker 执行新 Run 并调用配置的模型
docker compose --profile agent up -d --build worker dispatcher cleaner
docker compose --profile agent ps
```

```powershell
# 本机终端 C
Set-Location frontend
npm.cmd run dev
```

访问 `http://127.0.0.1:15173/`（如设置其他端口，以根 `.env` 的 `DOCUCHECK_WEB_PORT` 为准）。Windows 本机5173落在保留端口段，因此改用15173；Vite启用 `strictPort`，占用时直接失败，不自动漂移。浏览器登录使用 OIDC Authorization Code + PKCE，API 验证签名、issuer、audience、期限，再检查项目/Case 的对象权限。`reviewer` 只是账号名称；项目权限以 owner/editor/viewer 成员关系为准。不要用无鉴权默认兼容模式对外演示。

启用 worker 后，点击问答会发送问题和必要证据片段到模型服务并可能计费。需要只回放历史时不要创建新 Run，保持 worker 停止即可。自签/企业 CA 问题应配置可信证书链，不能关闭 TLS 校验；本机专用证书辅助文件不在公开交付内。

## 5. 启动后的最小检查

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health/live
Invoke-WebRequest http://127.0.0.1:8000/health/ready
Invoke-WebRequest http://127.0.0.1:8000/docs
docker compose ps mysql redis
docker compose --profile agent ps worker dispatcher cleaner
```

`/` 返回 404 是没有根路由；`/docs` 是 API 文档。受保护 API 的无 token 请求应为 401。readiness 不等于模型账号、解析字体和整个业务流程都已通过；还需登录、上传合成资料，确认解析完成后做授权的真实问答。

MySQL 测试只在测试/隔离库上显式开启，离线脚本会清除此开关：

```powershell
$env:DOCUCHECK_RUN_MYSQL_TESTS = '1'
try { python.exe -m pytest backend/tests/test_run_repository_mysql.py -q }
finally { Remove-Item Env:DOCUCHECK_RUN_MYSQL_TESTS -ErrorAction SilentlyContinue }
```

上传恢复与取消的新隔离用例在 `backend/tests/test_upload_intents_mysql.py`。另需开启 `DOCUCHECK_RUN_B_MYSQL_TESTS=1`，并将 `DOCUCHECK_B_MYSQL_URL` 与 `DOCUCHECK_DATABASE_URL` 同时指向同一新建临时库：MySQL、loopback地址、库名匹配 `docucheck_b_[a-f0-9]{12}`，已应用迁移头；使用随机凭据与临时原件目录。设置 `PYTHON_DOTENV_DISABLED=1` 并清除无关运行配置，防止加载业务 `.env`。RR用例会断言测试连接的隔离级别为REPEATABLE-READ；断连用例只KILL本测试连接的ID。未显式开启时四项跳过，跳过不计实连通过。[本轮隔离验收范围](superpowers/plans/2026-10-13-project-consolidation.md)。

停止本机 API/解析/Vite 用各自终端 Ctrl+C；停止容器用 `docker compose --profile agent --profile identity stop`。不要以 `down -v` 代替普通停止，它会删除数据卷。详细数据恢复见[备份与恢复](backup-and-recovery.md)。

## 6. 故障定位

| 现象 | 先检查 |
| --- | --- |
| PDF 长期 pending | 解析终端、MinerU 可执行路径；未配置解析器时不会伪造 complete。 |
| Run 排队不动 | 文件快照是否就绪、dispatcher、Redis、worker 的运行状态；不要反复提交付费任务。 |
| partial / interrupted | 查看 Run 的 error code、输入快照、attempt 与安全诊断；中断的模型请求 outcome/usage 可能未知。 |
| 新上传后结果“历史” | Run 固定旧快照，是预期行为；切换当前快照重新审查。 |
| 401 / 403 | 登录状态、issuer/audience 与项目成员权限；不是猜一个资源 ID 就能读取。 |
| `report_exists` | 为本次离线校验选一个新报告文件名。 |
| 安装/模型 TLS 失败 | 核对可信 CA、代理与时间；保留证书验证。 |
| 子进程启动报 OpenBLAS 内存分配失败 | 先查可用内存；本轮低内存环境下，仅给测试命令临时设置 `OPENBLAS_NUM_THREADS=1` 后上传恢复回归通过。记录该运行条件，不将初始化失败当业务断言通过。 |
