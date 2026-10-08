# 交接修复后的实连验收：数据库与运行版本

日期：2026-10-08。基准提交：`0aecca8`。执行 [实连验收计划](superpowers/plans/2026-10-07-repair-live-acceptance.md)第一部分；第二部分的登录、浏览器和真实模型完整流程尚未验收。本轮按用户约定单独提交本地 Git，提交号以 Git 日志为准。

机器可读结果及当前改动哈希见 [验收证据](repair-live-database-evidence-2026-10-08.json)。此前 R13 全仓安全扫描仍绑定当时的源码基线，本轮补做三个变更 Python 文件的定向扫描。

## 发现及修复

### 1. 批次并发保存和读取使用旧视图

首轮真实 MySQL 测试为 **24 passed / 1 failed**：两个执行者同时保存同一批次时，第二个执行者抛出原始数据库主键重复异常。

`DynamicRepository._guard` 在等待 Case 锁前可能已经建立 MySQL REPEATABLE READ 读视图。后续普通 SELECT 即使在取得锁之后，仍可能看不到前一事务提交的批次：保存时重复 INSERT，读取时返回 None。修复为在原有 Case → Run → 权限 → 清单锁之后，对目标批次执行当前锁定读取。

新增三项确定性 MySQL 回归，修复前全部失败：同输出并发保存、不同输出并发保存、旧视图读取刚提交的批次。测试显式核对 REPEATABLE READ，避免服务器换隔离级别后失去验证意义。

### 2. 不同 Case 首批写入的间隙锁死锁

独立审查指出，缺失主键的 FOR UPDATE 读取可能产生间隙锁；两个不同 Case 的首批写入可以相互等待。新增测试在两次 INSERT 前同步，真实复现 MySQL **1213**。

修复为只重试整个已经回滚的批次数据库事务，最多三次。每次重新检查 Case 版本/删除状态、Run/attempt/fence、快照、权限和清单绑定。同输出仍幂等成功，不同输出仍返回 `batch_source_changed`。

- 仅 MySQL 1213 可以重试；锁超时1205、连接失败、其他方言和业务错误直接保留。
- 事务中没有模型或其他外部请求；不自动重复模型调用，也不重试结果未知的提交。
- 三次仍失败时抛出异常，不无限重试或伪造完成。
- 跨 Case 回归需要空的隔离 schema；已有业务批次时明确跳过，不清空用户批次来满足测试。

新增五项离线测试验证重试上限、错误类型和重试中 claim 失效。最后独立复查未发现可确认的阻断问题。

原理参考 [MySQL 一致性读取](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)、[锁定读取](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)及[间隙锁/插入意向锁](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)。本轮没有更改数据库全局隔离级别。

## 最终验证

| 检查 | 实际结果 |
| --- | --- |
| 完整后端离线回归 | **1248 passed / 41 skipped / 0 failed**；网络尝试0 |
| pip check / 完整 Ruff | 通过 |
| 独立临时 MySQL schema | 迁移至 `0016_assistant_runs`；16个模块 **29 passed / 0 skipped / 0 failed** |
| 定向离线回归 | 重试与动态绑定共 **8 passed**，网络尝试0 |
| 临时数据库清理 | finally 清理成功，独立查询剩余 `docucheck_accept_*` schema数量为0 |
| 三个变更 Python 文件 | Semgrep实际覆盖3个源码文件，错误0/发现0；Gitleaks trace覆盖3文件、发现0 |
| 后台运行代码 | worker/dispatcher/indexer/cleaner每个容器的118个Python文件与宿主逐字节一致 |
| 基本 HTTP 边界 | live/ready 200；未登录 `/api/v1/me` 401；OIDC discovery/JWKS 200 |

41项离线跳过不算通过；其中本轮单独运行的 MySQL 测试另列29项，其余外部链路仍需显式验收。此次没有前端源码修改，也没有把此前前端测试数字当成本轮结果。

真实 MySQL 回归使用 UUID 命名的临时 schema和应用账户；正式 dispatcher不连接临时库，所有 Provider 为脚本模型。验收结束撤销该临时 schema的账户权限并删除该库，未清理用户 Case/原件。原有1个 `waiting_user` Run保留。

原始报告保存在本机忽略目录，发布证据记录其哈希：

- `data/verification/repair-live-batch-final-offline-2026-10-08.json`
- `data/verification/repair-live-mysql-final2-2026-10-08/report.json`
- `data/verification/repair-runtime-final-2026-10-08.json`
- `data/verification/repair-security-delta-2026-10-08/summary.json`

首轮主键重复、三项旧视图失败及跨Case死锁的失败报告均保留，没有覆盖成成功。定向扫描的首次路径检查因Windows反斜杠和额外空ignore文件而失败；规范化路径后核对原始成功扫描报告、三个源码覆盖及快照哈希通过，没有将校验器报错称为扫描器发现。

## 运行同步和环境问题

初次发现运行中的worker有9个后端文件与当前源码不同，不能沿用旧服务验收结论。已经告知用户后同步4个后台服务，并启动当前代码的OIDC API。

跨日期恢复环境后，标准 Docker 构建访问 `auth.docker.io` 被拒绝。本轮使用此前已构建的本机依赖镜像，`--pull=false --network=none` 只复制当前app并执行入口导入检查，再启动4个服务。实际镜像ID见机器证据。这是本地代码同步，不是本轮从干净环境构建生产镜像的证明。

API当前监听 `127.0.0.1:8000`，为工具终端中的进程，并非Windows服务。原后台派发、清理服务继续正常工作。indexer的healthy只代表配置检查；本轮没有启动Milvus/Ollama或证实索引成功。

前端启动失败：Windows排除端口范围 **5141–5240** 包含5173，Vite报 `EACCES`。没有删除系统保留端口或放宽登录回调；网页和模型闭环未运行。后台隐藏启动曾被自动审批策略拒绝，改用可观察的工具终端启动API成功，前端仍受端口问题阻塞。

## 复核入口与下一部分

离线复核，在已激活docucheck Conda环境的根目录执行，报告使用未存在的文件名：

```powershell
python.exe -X utf8 scripts/verify_offline.py --report data/verification/repair-live-recheck.json
python.exe -m pytest backend/tests/test_dynamic_batch_retry.py backend/tests/test_dynamic_run_binding.py -q -p scripts.offline_guard
```

MySQL复核需先准备独立临时schema，通过 `DOCUCHECK_DATABASE_URL` 指向它，执行Alembic升级，再开启 `DOCUCHECK_RUN_MYSQL_TESTS=1`。16个模块的完整命令在机器证据对应本机原始报告中；连接密码不写入报告或命令文本。不要为了重现间隙锁清空已有业务库。

下一部分先处理本机前端端口与OIDC允许的登录回调，然后验收真实登录、清单保存/确认/历史、记忆有效期、报告/修正/导出，并恢复BGE/Milvus做检索及真实模型抽样。需要调用模型时，先说明合成资料范围、目的及费用未知，记录失败、请求数和token。

本部分完成不代表整个实连计划完成；独立人工封存、多品类准确率、实际费用账单、远程CI、生产部署和灾备仍是后续条件。
