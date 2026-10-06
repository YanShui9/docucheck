# Milvus 基础接入：代码、校验与实连条件

日期：2026-10-03。属于改版四个大步骤中的第二步后端，首个模块；**离线准备与新实例启动/匿名拒绝已验证，SDK/凭据轮换/真实向量/业务 RAG 验收未完成**。

最新更新：SDK、凭据轮换、默认 public 权限收紧及真实合成向量/权限验收现已完成，见 [实连验收报告](milvus-live-acceptance-2026-10-03.md)。本文件以下是此前阶段记录；Attu GUI、真实 Embedding 和业务 RAG 仍待后续。

## 0. 后续新镜像启动补验（最新）

用户提出“在 Docker 拉个新的镜像”后，重新核对[官方 2.6.25 发布](https://github.com/milvus-io/milvus/releases/tag/v2.6.25)（2026-09-29），完成 `docker pull milvusdb/milvus:v2.6.25`；拉取返回成功，digest 为 `sha256:29f7668e64df1c6d5cdadbfbee99f38c72e4001054f149a298725acae57230ad`。Dockerfile 已固定此 tag/digest。该版本脚本仍引用 2.6.24，因此没有直接运行上游脚本替换本地配置。

新增 `compose.milvus-smoke.yaml`，与主配置叠加，把试运行限制为 2GiB 内存、2GiB memory+swap 总预算、restart=no。实际运行：

```powershell
docker compose -f compose.milvus.yaml -f compose.milvus-smoke.yaml up -d --build
```

- 构建、独立卷/网络创建与启动均退出 0；实例名 docucheck-vector-milvus-1；日志启动 banner 确认为 Version: 2.6.25。
- 状态 running、healthy，RestartCount=0、OOMKilled=false。
- `GET http://127.0.0.1:9091/healthz` 返回 200、OK。
- 不带凭据的 `POST http://127.0.0.1:19530/v2/vectordb/collections/list` 返回 code=1800，user hasn't authenticated。它验证匿名被拒绝，不代表凭据已轮换或完整权限隔离已验收。
- Docker 空载内存观测约 85.85→109.3MiB/2GiB；后一次宿主可用约 0.62GiB。只能说明当前空实例可启动，不是向量索引、中文 Embedding、多任务或生产容量验证。
- 当前服务保留运行；只改新部署文件，没有停止已有应用、重启 Docker、删除旧目录或复用旧卷。旧 I/O 错误在本次短时启动未重现，但其底层故障原因仍未查明。

此前把资源建议直接作为阻止试启动的条件过于保守，本次通过受限测试补齐。下面第 2/3 节保留启动前检查结果，第 4 节按已执行与未执行状态更新。

## 1. 本步写了什么

| 文件 | 作用 |
| --- | --- |
| backend/app/vector_store/config.py | 读取并校验 URI、数据库、维度、超时；凭据隐藏；不允许 URI 内嵌密码 |
| backend/app/vector_store/probe.py | 惰性 SDK；合成向量临时集合；过滤、Strong 读、删除可见性及 finally 清理 |
| backend/app/milvus_probe.py | CLI，默认无网络预览；显式 --send 才连接 |
| backend/tests/test_milvus_foundation.py | 31 项离线测试，使用独立 fake 服务行为 |
| compose.milvus.yaml、deploy/milvus/* | 独立 docucheck-vector 栈，固定 2.6.7 基础镜像、Linux named volume、认证配置 |
| .env.example | 新增可选 Milvus 配置项；用户本机 .env 未修改 |

真实业务 Agent 尚未调用此模块。探针没有 Embedding 或大模型调用；四条向量是人工构造，使用 quick-setup 集合验证协议行为，不等于生产 schema、召回率或权限系统验证。

## 2. 环境检查与退出问题

- 本机 Attu：2.6.4；缓存镜像：milvusdb/milvus:v2.6.7；旧实例处于 Exited(1)。Attu 与新服务的实际连接兼容性尚未验收。
- 旧实例使用项目之外的 Windows bind 数据及配置目录。日志记录 embedded etcd 写 WAL 的 input/output error，同时 user.yaml 读取失败。这说明退出时的直接 I/O 故障，尚不能证明底层磁盘、Docker/WSL 等具体根因已修复。
- 新文件采用独立 Linux Docker volume，配置随镜像构建。构建上下文仅 deploy/milvus，不包含项目 .env、上传原件或模型凭据；未读取或搬移旧 Milvus 数据。
- 机器总内存约 15.24GiB，后一次检查可用仅 0.49GiB；Docker 可见约 7.38GiB，还运行两套 MySQL/Redis/worker 和身份服务。**本步没有启动新服务、安装 SDK、停止旧栈或更改 Docker 内存设置。**

[Milvus 2.6 官方安装要求](https://milvus.io/docs/v2.6.x/prerequisite-docker.md)列出 Standalone 8G 最低、16G 推荐，并建议 Windows/WSL 的绑定数据放在 Linux 文件系统。当前资源不能直接视为满足完整开发栈运行要求，需要先释放资源并重新检查。

## 3. 验证结果

Conda docucheck Python 3.12.14：

```powershell
python.exe -m pytest -p scripts.offline_guard backend/tests/test_milvus_foundation.py backend/tests/test_provider_contract.py backend/tests/test_provider_probe.py backend/tests/test_retrieval.py -q
```

结果：**81 passed，3.74 秒；offline_network_attempts=0**。其中新模块 31 项，原有定向回归 50 项。本步没有重跑整个后端或真实 MySQL 测试，不能用此结果覆盖历史全量验收。

- 初始新模块缺失时测试失败，实施后通过；额外发现对搜索 entity 的整字典比较会误拒绝附加元数据，先复现失败，再改为逐个校验必要范围字段，保留越界拒绝。
- project/case/snapshot 三个字段各自有独立过滤测试；缺凭据、配置越界、返回越界、删除后仍可见、碰撞集合、创建确认超时、清理/关闭失败均有覆盖。创建结果未知时报告唯一集合名，供后续核对；不会直接删除未确认归属的集合。
- Ruff：本步 Python 文件与测试 All checks passed。SDK异常边界的宽捕获明确标注理由，只返回稳定错误码，避免输出服务异常中的凭据。
- `docker compose -f compose.milvus.yaml config --quiet`：退出码 0。这是配置解析，**并未构建或启动镜像**。
- `python.exe -m backend.app.milvus_probe`：preview、network_calls=0、business_rag_connected=false，正常退出。

## 4. 下一次实连怎么做

新实例已受限启动。真实索引和 Embedding 验收前仍需检查资源峰值；必要时关闭闲置程序或确认哪些重复服务可退出。不要直接运行破坏数据的 `down -v`、删除旧目录或重置 WSL。以下标明已经执行及剩余事项：

1. 固定兼容的 pymilvus 2.6.x 版本，安装到 docucheck Conda 环境，检查依赖并更新相应锁文件；本步 SDK 尚未安装、锁文件未新增此依赖。
2. **已完成**构建与受限启动：`docker compose -f compose.milvus.yaml -f compose.milvus-smoke.yaml up -d --build`；健康/空载内存见第 0 节。Docker 默认安全配置下可运行，没有添加 seccomp:unconfined。
3. 启用认证后轮换默认管理员密码，创建适当权限的本地验收账号；确认未授权连接被拒绝。将轮换后的凭据写入本机 DOCUCHECK_MILVUS_TOKEN，不发到聊天、不写入仓库。[官方认证说明](https://milvus.io/docs/authenticate.md)。探针需创建、写入、搜索、查询、删除及删除集合的权限，后续业务账号应分开。
4. 在 Attu 连接 127.0.0.1:19530，确认实际服务版本、认证和独立数据空间。
5. `python.exe -m backend.app.milvus_probe --send`。仅合成向量，必须返回 completed、matched_ids=[1,2]、remaining_ids=[1]、collection_removed=true；如失败，按稳定 reason 检查，并核对报告的临时集合。确认 Attu 中无遗留探针集合。

当前仅开放回环 19530 和健康端口 9091，etcd 2379 不向宿主映射。匿名拒绝已实测；默认管理员凭据尚未轮换、带认证向量读写未验证，不能标记安全/业务就绪；也不代表公网生产部署已完成。

## 5. 对照总体方案，仍需完成

后端大步骤整体尚未完成：普通聊天与意图路由、分层持久记忆、真实 Embedding、索引 worker/outbox 与版本删除栅栏、Milvus/BM25Plus 混合召回、动态指标草稿/确认/版本化、受控审查子图及多品类测试。MySQL 仍保存权威业务数据，Milvus 负责检索索引，Attu 用于管理查看。

前端第三大步仍先讨论视觉规范，再写页面；第四大步重新评测食品等类别。原有设备八字段评测成绩不作为新方案成绩。
