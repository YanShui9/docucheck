# 后台运行版本统一与部署验收

2026-10-08，应用源码基准 `b91e3fb6fcab6e34b6b32bf2e0d1c709b41f4337`。用户授权继续上一轮剩余的运行版本同步。本步更新四个后台服务的应用镜像，未修改业务 Python、数据库结构或前端。

## 差异与更新范围

上一轮只记录了worker的单文件差异，本步扩大到四个共用源码的后台角色逐一核对：

| 服务 | 更新前不同的Python文件数 | 更新后 |
| --- | --- | --- |
| worker | 1 | 118/118一致 |
| indexer | 6 | 118/118一致 |
| dispatcher | 6 | 118/118一致 |
| cleaner | 6 | 118/118一致 |

worker差异为 `db/dynamic_repository.py` 的事实修正答复完成度；另外三者还缺少之前的 `agent/assistant.py`、`agent/dynamic.py`、`dev_oidc_realm.py`、`providers/base.py`、`providers/chat_model.py` 变更。源码比较以导入模块定位实际 `/opt/docucheck` 路径，校验完整文件集合后按UTF-8通用换行规范化计算哈希，防止Windows换行差异误报。更新后四个角色合计472项源码哈希全部匹配。

保留各角色原有已安装环境，复制经118文件指纹确认的 `backend/app` 快照，生成三份新镜像；dispatcher和cleaner共享一份核心镜像。构建以本地回退标签为FROM，先确认标签指向原容器的不可变image ID，使用 `--pull=false --network=none`。三次最终构建均通过pip check和四个后台入口导入，没有安装或升级包。

| 角色 | 新镜像ID | 保留的回退标签 |
| --- | --- | --- |
| worker | `sha256:348ee70443ce61ee2949abb06665088db815e7432d00fcd1c6eb2dd03b865699` | `docucheck-worker:pre-sync-b91e3fb-20261008` |
| indexer | `sha256:35dd96d6f73101a0df6d0d3068997e88371bb9c04c595974f1899c9fc2b07f0a` | `docucheck-indexer:pre-sync-b91e3fb-20261008` |
| dispatcher/cleaner | `sha256:63c1e96dd6a77356f187b7cdfc2dbcb058e742c56fe8ba925fbdc73c328d21b6` | 各自 `docucheck-{role}:pre-sync-b91e3fb-20261008` |

镜像保留 `sync-b91e3fb` 标签，同时更新现有Compose使用的服务默认标签。OCI revision标签指向应用构建基准；本步后续Git提交仅包含部署记录，不改变应用源码。

## 部署门禁与保留项

部署前比较合并后的三个Compose文件与四个实际容器：全部环境变量、命令、网络、挂载及非root用户一致，未新增端口或特权。当前queued/running Run、待派发Outbox、当前快照pending/processing解析和待索引任务均为0。先停止dispatcher，再确认worker active/reserved/scheduled和Redis runs队列均为0，随后正常停止并重建四个后台服务。

使用既有 `compose.yaml + compose.vector-app.yaml + compose.model-tls.yaml`，`up --no-deps --no-build --pull never --wait`。worker和indexer为healthy；dispatcher和cleaner保持running，这两者原配置没有Docker healthcheck，不把running称为healthy。所有其他14个容器的ID、镜像、StartedAt及运行/健康状态前后完全一致，其中包含停止的旧实例和一次性初始化容器。

保留worker单独的只读CA挂载及 `SSL_CERT_FILE`。bundle SHA-256仍为 `dc611ea7e32e13cc5f9a3b27aabc0837f2c4a2a86bb04633e10d8bf1869ce636`，容器默认SSL上下文加载151张CA，`CERT_REQUIRED`和主机名校验保持开启。没有进行新的外网TLS握手或模型请求，不能将本轮配置验证当成新的DeepSeek实连成绩。

依赖元数据逐项比较旧回退镜像与新运行容器：worker/indexer各82项、核心镜像77项，名称及版本一致。这里是镜像内安装包清单，不是开发环境requirements锁的包数。

## 业务检查与回归

新worker通过生产 `DynamicRepository.verified/checks` 只读加载既有合成正向修正Run `eb334fa9ebe6415f8a17ff586f0dc789`：保留12箱和8箱、coverage完整、needs_review=true、业务状态资料冲突；公共checks按复核状态标为非正式。以新容器内 `render_review` 生成的答复与数据库既有答复完全一致，包含“指标审查完成”和“结论仍需人工复核”，没有“部分完成/未覆盖项”的错误提示。本检查没有再次提交人工修正或生成业务Run。

| 验证 | 本步实测 |
| --- | --- |
| 离线定向回归 | **167 passed / 网络尝试0**，70.81秒；覆盖动态审查/修正、Assistant、Provider契约、索引器、派发器和清理器。 |
| Ruff及依赖 | backend/evals/scripts Ruff、宿主pip check、构建pip check和实际worker pip check通过。 |
| 排空及源码 | Celery active/reserved/scheduled均0、runs队列0后更新；四容器472哈希一致。 |
| 状态保留 | 既有waiting_user的Run/attempt/fence一致；五条索引ready、manifest/fence/attempts一致；Outbox派发状态分布一致。全部文档parse状态/claim/lease指纹一致。 |
| 服务边界 | API live/ready 200、匿名 `/api/v1/me` 401；其他容器前后记录完全一致。 |

本轮操作没有主动调用DeepSeek或BGE Embedding。索引器正常后台轮询元数据/历史generation清理不纳入查询请求数；本步未独立计量全机后台请求。没有重复前端构建、全量后端、真实MySQL并发、浏览器聊天或模型审查验收；相应前轮记录保留各自的时间和范围。

## 失败记录与校正

- 首次构建把 `FROM sha256:{image-id}` 写作基础引用；当前构建器将其解释为 `docker.io/library/sha256:{tag}`，尝试远程认证并超时。改为已验证ID的本地镜像标签后，三份镜像成功。首轮失败23.391秒及日志保留；`--network=none`只约束RUN步骤，不代表首次基础镜像解析没有外网尝试。
- 初次部署前查询没有按当前revision限定解析状态，误把历史快照保留的510条pending和1条processing当作在途任务，门禁拒绝。核对 `ParseRepository.claim` 的当前快照约束后收窄运维门禁；当前快照解析在途为0，历史状态未修改且部署前后指纹一致。
- 首次只读业务断言把内部规则计算的 `is_formal` 当成公共结果。核对生产 `DynamicRepository.checks` 后改为通过公共读取检查 `needs_review` 对正式性标志的限制，最终通过。未改业务规则或既有报告，失败脚本/日志保留。
- 首次Docker信息采集使用Windows默认文本解码，遇到非GBK字节失败；采集脚本改为显式UTF-8后成功。没有输出Docker完整环境或凭据。

## 启动、回退与交付边界

本机当前新镜像已生成并绑定默认服务标签，保留三个overlay启动：

```powershell
docker compose -f compose.yaml -f compose.vector-app.yaml -f compose.model-tls.yaml --profile agent up -d --no-deps --no-build --pull never --wait worker dispatcher cleaner indexer
```

回退前同样先检查任务并排空，暂停dispatcher；把本机四个 `pre-sync-b91e3fb-20261008` 标签重新标记为对应 `docucheck-{role}:latest`，然后使用上述三个overlay重新创建服务。镜像回退会重新带入已记录的旧源码差异。不要通过Git回退、`docker restart`或省略TLS/vector overlay代替镜像回退；不要删除卷、资料或凭据。本轮保留了回退镜像及标签，未实际执行回退演练。

这是在本机已验证运行环境上的应用版本同步，不能替代标准Dockerfile从基础镜像和依赖锁进行干净重建、镜像安全扫描/发布或生产高可用验收。独立封存集、多品类真实质量、容量与灾备仍按既有发布门槛处理。

[脱敏JSON证据](repair-runtime-sync-evidence-2026-10-08.json)保存前后版本、门禁、构建/回归结果及原始证据哈希；完整脚本、日志、源码快照和运行指纹位于被忽略的 `data/verification/worker-sync-live-2026-10-08/`。后台已知源码差异本步清零。

## 独立复核

按代码复核流程完成只读独立检查，未发现阻断问题。复核者重新计算41项证据哈希、仓库及构建快照的118文件哈希，并逐键比较四容器472项记录；核对其他14个容器、配置指纹、任务状态、构建与部署日志及生产读取路径，结果一致。复核没有独立重跑测试，也没有操作服务、网络、模型或数据库。
