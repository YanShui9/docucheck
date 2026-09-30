# M3：Agent 服务镜像同步与无模型派发验收

日期：2026-09-30。本步检查运行中的 `worker`、`dispatcher` 与当前后端源码是否一致，并验证更新后的 Outbox → Redis → Celery → MySQL 回写。没有提交真实业务问题，也没有调用模型 API。

## 发现与处理

- 原 `worker` 镜像只有 64 个 `backend/app` Python 文件；当前源码有 68 个。原镜像的 `run_executor.py` 哈希与源码不同，且缺少 `correction_recheck.py`。因此，M3-11d 的修正事实 Agent 逻辑虽然已在源码实现，却不能认为当时运行中的 worker 已部署该逻辑。
- 更新前只读检查：数据库中没有 `queued`/`running` Run 或待派发 Outbox；仅有 1 条历史 `completed` Run、1 条 `delivered` Outbox 和 1 个 `complete` 文档。
- `cleaner` 镜像与当前源码的 68 个 Python 文件整体 SHA-256 摘要一致（`d95822e6352b0511ff8d000bba2bf9777c28647ec1b4396ecd3b3642dcd152a7`），`requirements-worker.txt` 的 SHA-256 也一致（`0be155abf587df25937483d57daf3f9e2beec128cf0560e5eb315430aa606b90`）；三个服务共用 `backend/Dockerfile.worker`。本机离线将这个已核对镜像分别标记为 `docucheck-worker:latest` 和 `docucheck-dispatcher:latest`，停止旧服务后用 `docker compose --profile agent up -d --no-build --no-deps --force-recreate worker dispatcher` 重建容器。
- 更新后两个运行容器各有 68 个 Python 文件，整体摘要均与源码一致；worker `healthy`，Celery 已连接 Redis 并监听 `runs` 队列。MySQL 和 Redis 未重建，`cleaner` 未中断。

## 验收

- 显式启用 `backend/tests/test_outbox_celery_mysql.py`：`1 passed in 4.44s`。测试只提交空快照；日志显示 dispatcher `published=1`，worker 返回 `partial / snapshot_not_ready / model_calls=0`。
- 测试后数据库仍只有原有的 1 条 `completed` Run、1 条 `delivered` Outbox、1 个 `complete` 文档；测试记录已清理。Git 工作区在记录本文前为干净状态。

## 后续部署约束与未覆盖范围

镜像标记只是本次离线复用已逐字节核对的构建产物。常规源码变更后应重新构建镜像、重建容器，并核对运行容器的代码版本；单纯 `docker compose restart` 不会载入新镜像。Docker 官方文档分别说明了[镜像标记](https://docs.docker.com/reference/cli/docker/image/tag/)、[`compose up` 的重建行为与 `--force-recreate`](https://docs.docker.com/reference/cli/docker/compose/up/)及[`compose build`](https://docs.docker.com/reference/cli/docker/compose/build/)。

本次只验证无模型空快照的自动派发与回写。真实上传资料经后台解析后、由更新后的 Celery worker 完成修正快照 Agent Run 的端到端链路仍未验收；真实 MinerU PDF、独立资料集质量、费用和完整时延也未因此通过。前端仍按协作约定先讨论设计规范与样式。
