# Milvus 启动故障修复与实连验收

日期：2026-10-08。基线：`6f17627`。**当前向量栈已恢复，原数据卷复用，最终五项实连测试通过。** 本步修改部署配置及测试，没有修改采购业务代码或前端，没有调用DeepSeek。

## 故障原因与处理

上一步两次内置etcd启动均退出134：首次选主前，Milvus开始读取 `by-dev/meta/session/id`，收到 `etcdserver: leader changed` 后在 `Session.Init` panic。核对镜像所报GitCommit `95ec678526` 的[内置etcd启动源码](https://github.com/milvus-io/milvus/blob/95ec678526/pkg/util/etcd/etcd_server.go)，`embed.StartEtcd` 后没有等待 `ReadyNotify`；[会话源码](https://github.com/milvus-io/milvus/blob/95ec678526/internal/util/sessionutil/session_util.go)将获取ID失败直接转成panic。日志与源码共同支持**内置启动路径缺少就绪等待**；没有证据表明数据卷损坏，也不声称所有启动故障或etcd运行期故障都已解决。

采用上游[独立etcd部署方式](https://github.com/milvus-io/milvus/blob/v2.6.25/deployments/docker/standalone/docker-compose.yml)，保持Milvus 2.6.25、etcd 3.5.23和本地文件存储：

- `compose.milvus.yaml` 增加独立etcd，官方Quay镜像按digest固定；etcd复用原 `docucheck-vector_milvus_data` 的 `etcd` 子目录，Milvus继续使用原卷的完整存储路径。
- etcd的 `endpoint health` 成功后才能启动Milvus；Milvus使用外部etcd客户端，关闭内置etcd。etcd没有宿主端口映射，peer只监听自身回环。
- 独立复核发现 `volume.subpath` 不适用于尚无子目录的新卷。补充仅执行 `mkdir -p` 的一次性 `storage-init`，无网络、64MiB限制；必须成功退出后才挂载etcd子目录。不复制、清空或重置旧目录。
- 受限试运行使用 `compose.milvus-smoke.yaml`：Milvus 2GiB、etcd 256MiB，禁用超额swap和自动重启。这是本机演示运行配置，不是生产容量或高可用认证。

旧卷停机后以只读方式完整归档：`data/verification/milvus-startup-fix-2026-10-08/milvus-volume-before.tar`，149,258,240字节、355个归档条目，SHA-256 `ed749a7d3b1180b2f60e860cac1d925bc952abbb0e22611f4a8ed41ab35de06f`。备份包含内部元数据，保存在忽略目录，不进入Git。

## 实测暴露并修复的权限问题

首次恢复后的五项实连为 **1 failed / 4 passed**：专用账号能读取外库集合结构，尚未执行到该测试后面的Query拒绝断言。保留该失败记录，不将其改写成通过。

查询发现 `public` 又持有三项全库默认权限。核对当前[初始化源码](https://github.com/milvus-io/milvus/blob/95ec678526/internal/rootcoord/root_coord.go)和[配置定义](https://github.com/milvus-io/milvus/blob/95ec678526/pkg/util/paramtable/component_param.go)：`common.security.enablePublicPrivilege` 默认true，启动时会重新授予权限。因此此前只在初始化时撤销权限，不能保证重启后的隔离。

本步恢复既定的专用实例隔离策略：

1. 在Compose环境及 `deploy/milvus/user.yaml` 明确关闭 `enablePublicPrivilege`，保持 `authorizationEnabled=true`。
2. 只撤销 `public` 的三项 `db=* / object=*` 默认授权：`DescribeCollection`、`IndexDetail`、`ListAliases`。没有轮换凭据、增加授权或调整其他角色。
3. 停止本向量栈的Milvus和etcd，再按健康门禁重新启动；重启后public授权仍为空，probe角色仍只作用于 `docucheck_probe`。

当前运行镜像未重建，新增环境变量已生效；源码 `user.yaml` 的同步设置将在以后构建镜像时生效。不能声称当前镜像内的user.yaml已经更新。启动修复后额外执行的重启同时验证了权限不会再次默认恢复。

## 验证结果

| 验证 | 本次结果与范围 |
| --- | --- |
| 原数据卷恢复 | 初次启动、加固后重启均healthy；既有probe集合清单前后一致，default库测试集合清理为空。集合清单一致不等于逐条审计所有历史数据。 |
| Milvus基础实连 | `test_milvus_live.py` 3项通过：合成向量Strong读写/删除/清理、专用账号跨库结构与Query/管理拒绝、匿名/旧默认凭据拒绝。 |
| 作用域实连 | `test_hybrid_milvus_live.py` 1项通过：合成向量幂等写入、project/Case/snapshot/供应商/generation过滤及清理。此项不用真实Embedding。 |
| 持久化BGE实连 | `test_persisted_bge_live.py` 1项通过：合成XLSX真实上传解析、本机BGE写入真实Milvus、SQL manifest ready后新Run检索，找回 `1 TB / A1` 原文，维持complete_coverage=false。SQL层是SQLite/TestClient，不是MySQL或真实网页/队列。 |
| 新空卷兼容 | 独立临时Compose项目验证storage-init退出0、etcd healthy；只启动元数据服务，未启动新Milvus实例。测试所有者标签核对后删除本轮临时容器、网络和卷。 |
| 完整离线回归 | **1274 passed / 41 skipped / 网络0**，pytest进程耗时301.046秒（pytest自身报告295.67秒），pip check/Ruff通过。该轮收集于增加第4项部署回归之前；最终4项部署回归及既有初始化/检索边界另行定向 **22 passed / 网络0**，不能把1274称为包含最终新增用例的全量计数。 |
| 最终静态门禁 | Ruff、Compose解析及git diff检查通过。未重复前端测试或构建。 |

两轮实连累计 **6次本机BGE `/api/embed`**（各3次）、DeepSeek0次；本机验收进程网络守卫记录外部连接尝试0。首次失败和最终通过分别保留指标文件；后台indexer可能轮询模型元数据，这不是全机请求总数。两轮临时向量集合均由现有测试finally清理。

API live/ready仍200、匿名/me仍401，既有waiting_user任务保留，MySQL五条failed索引仍为failed；本步没有对它们发起重试，也没有伪造ready。API、前端、应用worker及其他Docker栈未被本步重启。上一步worker有1个Python文件的部署差异仍待统一部署。

## 本机启动与回退

已缓存镜像时，本机受限启动：

```powershell
docker compose -f compose.milvus.yaml -f compose.milvus-smoke.yaml up -d --no-build --wait --wait-timeout 120
```

首次在新机器构建应用镜像时去掉 `--no-build`、使用 `--build`；数据目录由storage-init创建。这里的新卷实测只覆盖元数据初始化，首次账号配置仍按既有显式初始化流程完成，不能照抄本机凭据。

回退部署前必须先停止Milvus与独立etcd，防止两个etcd进程同时写同一member目录。单纯Git回退后可复用同版本卷，但内置启动竞态以及默认public授权问题会重新出现，**不建议以此作为正常恢复方式**。不要执行本向量栈的 `down -v`；不要在运行状态下解压备份覆盖卷。恢复停机备份会丢弃备份后的索引写入，须单独安排停机并核对需要保留的数据，不作为自动回滚步骤。

## 证据及剩余工作

独立只读复核已关闭新卷初始化问题，核对24项证据/源码hash及最终结论，无剩余阻断问题。

[脱敏JSON证据](repair-milvus-startup-evidence-2026-10-08.json)记录配置、镜像、权限、失败/通过测试、原始证据hash与备份hash；原始资料位于被忽略的 `data/verification/milvus-startup-fix-2026-10-08/`。

本轮BGE/Milvus实连检查可以关闭。下一项是核对五条失败索引的资料版本、权限和generation，选择性显式重试并验证在线使用；另需统一应用镜像中的前一步API修正变更。本次不替代独立封存集、多品类准确率、无答案拒答、容量、灾备或生产TLS验收。
