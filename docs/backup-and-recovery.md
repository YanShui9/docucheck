# 资料、状态与恢复边界

日期：2026-10-03。本阶段已实现并演练**M5 合成资料/报告 ZIP 的验证与新目录恢复**。MySQL + 原件 + 身份服务的一致恢复是下面的运维手册，**本轮未覆盖现有数据库进行恢复演练**；正式部署仍须在隔离环境实测 RPO/RTO。

## 1. 需要分别保存的内容

| 内容 | 持久位置 | 恢复要求 |
| --- | --- | --- |
| Case、快照、证据、事实、Run/attempt、Outbox、权限、修正/清理审计 | MySQL `docucheck` | 逻辑备份、迁移版本及应用版本一起保存。 |
| 上传原件 | `DOCUCHECK_DATA_DIR/uploads/` | 保持数据库相对 storage_key 不变，并核对原件 SHA-256。数据库没有原文件副本。 |
| Redis 消息/AOF | Compose Redis 卷 | 队列不作为业务真值；可能重复投递，DB 领取与 fence 防过期写入。 |
| OIDC realm/用户/主体 | 当前 Keycloak H2 数据卷；首次 import 位于 data/oidc-import | 主体的 issuer/sub 对应项目归属，不能随意换主体重新建账号后假设权限相同。 |
| `.env`、身份导入、私有 CA、密钥 | 本机受限文件或部署密钥服务 | 单独加密备份、限权；不进入公开 ZIP 或版本仓库。 |
| M5 合成原件/标签/报告 | delivery_bundle 显式白名单 | 只用于开发验证和回放，不自动导入生产数据库。 |

Docker 卷提供持久性，卷本身不提供异机备份。复制运行中的数据库/H2 文件可能不一致；本机演示建议短暂停写后备份。[Docker 卷备份](https://docs.docker.com/engine/storage/volumes/)、[Keycloak 导入导出](https://www.keycloak.org/server/importExport)。

## 2. 一致备份的顺序

1. 记录应用源码、镜像摘要、`alembic current`、数据根目录和时间。确认没有必须继续的模型请求；停止本机 API 和解析进程。
2. 停止 worker、dispatcher、cleaner。等待任务自然结束或显式取消；强杀中的外部请求结果可能未知，恢复后不应自动重试并再次计费。
3. 确认无写入/删除后，备份 MySQL 与 uploads；备份过程中不执行 DDL/迁移。停止 Keycloak/Redis 后才复制各自卷。
4. 保存文件哈希与备份清单，复制到与开发盘不同的受限存储。用新实例试恢复后，原服务再恢复正常运行。

MySQL 8.4 的 `mysqldump --single-transaction` 可提供 InnoDB 一致视图，不能保证外部原件同时一致；本手册额外暂停应用写入。例子使用 `--result-file`，避免 Windows PowerShell 重定向改变 SQL 编码。[MySQL mysqldump](https://dev.mysql.com/doc/refman/8.4/en/mysqldump.html)。

以下是**计划执行的手动操作**，不是本轮已执行证明。先在自己的备份盘创建全新目录，示例目录 `E:/DocuCheckBackups/<timestamp>` 不在项目 data 内：

```powershell
$backupDir = Join-Path 'E:/DocuCheckBackups' (Get-Date -Format 'yyyyMMdd-HHmmss')
if (Test-Path -LiteralPath $backupDir) { throw '备份目录已存在' }
New-Item -ItemType Directory -Path $backupDir | Out-Null
docker compose --profile agent stop worker dispatcher cleaner
docker compose --profile identity stop keycloak
docker compose stop redis
```

数据库 dump 在容器内生成，再用 `docker compose cp` 复制原始字节。临时 option file 只在容器内，权限 600；这里要求随机口令由字母/数字/下划线/连字符组成，不含引号、换行或反斜杠。若采用其他字符口令，改用正确转义的受限 option file，不拼接到命令行。

```powershell
$dumpScript = @'
set -eu
umask 077
option=$(mktemp /tmp/docucheck-client.XXXXXX)
trap 'rm -f "$option"' EXIT
printf '[client]\nuser=root\npassword=%s\n' "$MYSQL_ROOT_PASSWORD" > "$option"
mysqldump --defaults-extra-file="$option" --single-transaction --routines --events --triggers --hex-blob --no-tablespaces --set-gtid-purged=OFF --databases docucheck --result-file=/tmp/docucheck-backup.sql
'@
docker compose exec -T mysql sh -c $dumpScript
if ($LASTEXITCODE -ne 0) { throw 'MySQL 备份失败，停止后续流程' }
docker compose cp mysql:/tmp/docucheck-backup.sql (Join-Path $backupDir 'mysql.sql')
if ($LASTEXITCODE -ne 0) { throw 'MySQL 备份复制失败' }
Copy-Item -LiteralPath './data/uploads' -Destination (Join-Path $backupDir 'uploads') -Recurse
Get-FileHash -LiteralPath (Join-Path $backupDir 'mysql.sql') -Algorithm SHA256
```

若自定义了数据目录，将 uploads 路径改为**经过确认的实际绝对路径**。另外对所有 uploads 文件建立相对路径/字节数/SHA 清单，对照 Document.source_sha256。`.env` 与身份卷另放受限备份；不要发送或展示 SQL、密码或令牌。容器临时 dump 在确认备份成功后由操作者删除。

Keycloak/Redis 当前演示卷名通常为 `docucheck_keycloak_data`、`docucheck_redis_data`，必须用 `docker volume ls` 核实项目名。卷归档只在相应服务停止后进行，可用 Docker 文档的临时容器挂载源卷只读、备份目录可写的 tar 方法；目标保存的是私有运行数据，不进入公开资料包。Keycloak realm 导出不是整个身份数据库的完整备份替代。

## 3. 隔离恢复，而不是覆盖原库

恢复动作会写数据库；只有确定了**新实例、新卷、新端口和备份版本**后才执行。仓库提供 `ops/compose.restore.yaml`，只启动 MySQL，默认端口 23307、独立卷 `mysql_restore`。从受限备份/密钥服务准备一个恢复专用 `.env`，不要打印文件。

```powershell
# 在未被使用的新 Compose project 下运行。恢复环境不会连接原 mysql_data 卷。
docker compose -p docucheck-restore --env-file E:/DocuCheckBackups/restore.env -f ops/compose.restore.yaml up -d mysql
docker compose -p docucheck-restore --env-file E:/DocuCheckBackups/restore.env -f ops/compose.restore.yaml ps
docker compose -p docucheck-restore --env-file E:/DocuCheckBackups/restore.env -f ops/compose.restore.yaml cp E:/DocuCheckBackups/SELECTED/mysql.sql mysql:/tmp/restore.sql
```

等该实例健康后，在新实例内执行以下导入；option file 的口令字符条件与备份部分相同。SQL 中的 schema 为 docucheck，新 MySQL 实例必须为空或经确认可覆盖；不要把命令改成原 Compose 项目执行。

```powershell
$restoreScript = @'
set -eu
umask 077
option=$(mktemp /tmp/docucheck-restore-client.XXXXXX)
trap 'rm -f "$option"' EXIT
printf '[client]\nuser=root\npassword=%s\n' "$MYSQL_ROOT_PASSWORD" > "$option"
mysql --defaults-extra-file="$option" < /tmp/restore.sql
'@
docker compose -p docucheck-restore --env-file E:/DocuCheckBackups/restore.env -f ops/compose.restore.yaml exec -T mysql sh -c $restoreScript
if ($LASTEXITCODE -ne 0) { throw '隔离恢复失败，不启动业务进程' }
```

随后在新文件根目录恢复 uploads 和独立身份服务，使用新的 `DOCUCHECK_MYSQL_PORT=23307`、`DOCUCHECK_DATA_DIR` 和相应 OIDC 配置，先保持 worker/cleaner 不启动。核验：

身份服务须保留原逻辑 issuer 和用户 sub。仅把演示 Keycloak 改到新端口，会改变 issuer；本项目主体键包含 issuer/sub，权限不会自动迁移。企业恢复应保持同一逻辑身份 URL；若确需改变 issuer/sub，另制显式权限迁移与越权验证方案。本手册不自动修改成员关系。

- 应用版本/依赖、迁移头和 Alembic 版本表一致。历史备份先用对应应用验证，不直接拿最新迁移改写原备份。
- Case/revision/快照/Run/修正数量及关联完整；全部原件存在，SHA 与 DB 一致。
- 原登录主体可读取应有项目，另一个用户仍不能越权；当前与历史快照的证据定位可读。
- 选一个合成 Case，查看历史八字段、原件、修正审计和导出；读取历史不发送模型请求。
- 不启动 cleaner，直至确认所有待删 Case/清理清单；不要让恢复后的错误清单自动删原件。
- Redis 若丢消息，dispatcher 可重派**仍为 queued、当前快照/attempt 有效**的 delivered Outbox；已执行或中断请求通过 DB 状态、租约和 fence 核对，未知请求只能人工决定是否显式重试。该机制不保证外部模型“恰好一次”。

记录恢复耗时、数据缺失和采样验证结果，才可决定是否切换入口。当前未声明高可用、时间点恢复、异机灾备或生产 RPO/RTO 达标。

## 4. M5 合成归档的实际恢复入口

```powershell
python.exe -m evals.delivery_bundle --verify data/m5-c/docucheck-m5-development.zip
python.exe -m evals.delivery_bundle --restore data/m5-c/docucheck-m5-development.zip --destination data/m5-restored
```

verify 在任何恢复写入前检查整个条目清单、大小/压缩边界、哈希、历史基线、凭据和路径。拒绝符号链接、大小写重复、未知条目、篡改原件及虚假的独立验收声明。恢复不读取 SQL，不创建 Run，不覆盖已有目录；中途文件写入失败会留下 `.restore-incomplete` 标记，不能把该目录当作恢复成功。
