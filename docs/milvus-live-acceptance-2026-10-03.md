# Milvus SDK、账号与真实向量验收

日期：2026-10-03。改版第二大步后端的 Milvus 基础模块；服务与 SDK 已实连，**真实中文 Embedding、业务 RAG、动态指标和新记忆机制仍未实现**。

## 1. 代码与环境变更

- `backend/app/dev_milvus_setup.py`：默认预览无网络/无写文件，`--apply` 才保存本机秘密并配置账号；初次默认 root 轮换必须显式 `--bootstrap-default-root`，仅允许空实例。重复执行沿用已保存凭据，不重置既有账号密码。
- 凭据生成在服务器变更之前写入本机 `.env`，便于初始化或轮换中断后的重试。生成值不打印、不入报告；保留了原非 Milvus 配置值，实测比对为 True。
- 初始化只允许回环地址和固定 docucheck_probe 库/账号；其他用户/库或 default 库已有集合时拒绝继续。它是独占本机开发栈工具，不能用于已有共享生产实例。
- root 管理密码已随机轮换；专用账号 docucheck_probe、角色 docucheck_probe_role，只获目标库的 13 项显式 RPC 权限，没有实例管理员授权。管理员配置不传给业务 Agent。
- Milvus 2.6.25 + pymilvus 2.6.17：使用现有锁作为安装约束，新增 cachetools/grpcio/pandas/protobuf/pymilvus 五项；charset-normalizer 从本机 3.5.1 对齐到已有锁的 3.5.2。dev/Windows 锁现在 102 包，可选 vector 锁 17 包；共享版本和实际安装一致，worker 75 包锁保持现状。环境引导文件已加入 SDK。
- 探针由四条扩为五条，实际覆盖另一项目、另一任务、旧快照三种过滤；默认 preview 仍无网络。
- CLI 临时屏蔽 SDK Python 日志，避免异常记录泄露 RPC 参数；只返回稳定错误码，并在退出时恢复日志设置。

## 2. 实连发现的问题与修正

第一版权限验证把“无权列集合”等同于抛错；实际 Milvus 会按权限过滤集合列表，因此测试失败。改为 root 在 default 库创建一份唯一命名的临时合成数据，再用验收账号尝试读取。

第二次发现跨库 describe_collection 仍成功。读取 SDK 源码确认该调用确实发出 RPC；查询服务角色后查明默认 public 具有三项跨全部数据库的元数据授权：DescribeCollection、IndexDetail、ListAliases。public 是每个用户隐式拥有的角色，因此只限制新角色并不够。

修复在确认独占新实例后撤销这三项已知 public 全局默认授权，保留非默认自定义授权。先新增失败回归、再实现，随后重新运行真实测试。初始化重放成功，public 全局剩余授权实查为 0，专用库角色实查为 13 项。

此修复针对本项目新建实例；没有更改用户原 Milvus 容器/卷。参考 [官方角色撤权接口](https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Authentication/revoke_privilege.md)与[服务端授权源码](https://github.com/milvus-io/milvus/blob/v2.6.25/internal/proxy/privilege_interceptor.go)。

## 3. 验证结果

| 验证 | 实际结果 |
| --- | --- |
| 完整后端离线回归 | **809 passed、25 skipped，223.60 秒，offline_network_attempts=0** |
| 单独启用 Milvus 集成 | **3 passed，5.75 秒** |
| 本步离线模块 | 初始化 12 项 + 探针 32 项通过 |
| Ruff、pip check | All checks passed / No broken requirements found |
| SDK 闭包/共有版本核对 | 17 项安装版本与锁一致，所有锁共有版本一致 |
| 默认预览/账号初始化重放 | 预览无网络；初始化多次重放完成 |
| Strong 向量检索 | 1024 维五条合成数据，只命中同范围 [1,2] |
| 删除后可见性 | 删除 2 后只剩 [1] |
| 清理核对 | default、docucheck_probe 均 0 个遗留集合 |
| 权限拒绝 | 匿名/旧默认密码被拒绝；验收账号跨库结构、内容读取及账号管理均被拒绝 |
| 服务状态 | running/healthy、OOMKilled=false、RestartCount=0；观测约 153MiB/2GiB |

离线跳过中含原 22 项外部服务测试与新增 3 项 Milvus 实连测试；实连三项另行显式执行成功。没有重跑原 MySQL/OIDC/模型服务联调，本轮大模型/Embedding 调用为 0。空载/小数据观测不是完整索引或模型负载峰值。

SDK 闭包本轮只在现有 Windows Conda 实际安装/验证；新增锁的 Linux 新环境安装及索引 worker 镜像仍待后续，历史 M5 三环境记录不替代新锁验收。

### 复跑

默认无网络：

```powershell
python.exe -m backend.app.dev_milvus_setup
python.exe -m backend.app.milvus_probe
```

已有配置重放和真实合成向量：

```powershell
python.exe -m backend.app.dev_milvus_setup --apply
python.exe -m backend.app.milvus_probe --send
```

只在首次新空实例显式添加 `--bootstrap-default-root`；不要用它覆盖共享实例。

真实集成测试（顺序执行，可整段粘贴）：

```powershell
$env:DOCUCHECK_RUN_MILVUS_TESTS = '1'
try {
    python.exe -m pytest backend/tests/test_milvus_live.py -q
} finally {
    Remove-Item Env:DOCUCHECK_RUN_MILVUS_TESTS -ErrorAction SilentlyContinue
}
```

测试只使用两库中的唯一临时合成集合，并在 finally 清理；不上传采购文件，不调用模型。

## 4. Attu 连接说明与剩余范围

已核对本机 Attu 2.6.4，**没有操作或验证其 GUI 登录**。可按以下设置连接：

- 地址：127.0.0.1:19530。
- 管理账号：root；密码读取本机 `.env` 的 DOCUCHECK_MILVUS_ROOT_PASSWORD，旧默认口令已失效。
- 数据库：docucheck_probe。
- 验收账号也保存在 DOCUCHECK_MILVUS_TOKEN，格式为 `用户名:密码`；它权限有限，Attu 某些实例管理功能可能被拒绝。

Milvus 是检索索引，MySQL 仍保存权威业务数据。下一阶段需要真实中文 Embedding、版本化文档分块与索引任务、原文/权限回读、BM25Plus + 向量融合、召回评测；随后继续聊天/记忆与动态指标审查。当前五条人工构造向量成功不能证明语义召回质量或完整业务权限已经验收。
