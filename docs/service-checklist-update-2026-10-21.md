# 清单问答修复的运行服务更新

2026-10-21，用户在看到“我苹果的采购数量是多少”仍返回资料检索无结果后，明确授权“先更新版本再继续看看情况”。本轮将API与worker从旧运行版本`4b54090`更新到已验证提交`516023dceac4208a7353c4995312d68e1cb08c47`。功能与原离线门禁见[源码交付记录](repair-remaining-chat-materials-2026-10-20.md)。

## 实际应用

- 从Git提交导出独立源码，未加载工作区另项“未确认资料关联的助手引导”修改；原有六份WIP的Git状态保持一致。
- worker以原镜像的依赖为基础，仅替换应用源码；构建禁止联网，`pip check`通过。131份Python源码与部署快照逐项一致，换行归一后与Git提交逐项一致。
- 现有任务、待派发记录、Redis队列及Celery active/reserved/scheduled为空后切换。原环境、用户、命令、挂载、网络、重启策略和健康检查均已核对。
- API改用`data/verification/checklist-service-deploy-20261021/start_api.py`，实际PID为26784；7个关键模块的文件路径及摘要均来自新快照。配置在进程内从既有worker取得，没有读取或修改根`.env`，临时环境文件已移除。
- 前端仍由已有Vite在`http://127.0.0.1:15173/`提供；前端相对提交无未保存修改。本轮没有修改页面交互或样式。
- 无新增迁移，业务库仍为`0018_upload_intents`。

本轮切换前曾被配置/状态一致性检查阻止；第一次新容器缺少原来由Compose提供的健康检查，检查失败后自动恢复旧worker。补齐相同的exec健康检查后切换成功。首次API启动还因Windows换行归一与原始字节摘要混用而被断言阻止，统一为部署快照字节核对后正常启动。失败过程未提交业务任务或调用模型。

## 当前验证

| 检查 | 实际结果 |
| --- | --- |
| HTTP | `/health/live`、`/health/ready`为200；匿名`/api/v1/me`为401；前端首页200。 |
| 运行身份 | API固定516023d，7个模块与快照一致；worker健康，131份源码与快照一致。 |
| 清单问答工具路径 | 在新worker内以脚本Provider运行`read_checklist`→`answer_checklist`，输入采购数量人工规则“等于20箱”，返回“你已确认的苹果采购数量是20箱”，附可读清单来源。2次脚本决策，真实模型请求0。 |
| 旧历史 | 新代码成功重验并读取一条已有completed助手结果。 |
| 数据保护 | 采购资料、清单、关联、消息及Run等业务表的行数和内容摘要一致；654份原始文件逐项摘要一致。索引服务持续运行，`index_manifests`的清理调度字段发生变化，该表仅核对行数293，不声称其全行摘要不变。 |
| 其他服务 | indexer、cleaner、MySQL、Redis、Keycloak、Milvus、etcd的容器身份未变；dispatcher按切换流程暂停后恢复。 |

本轮针对已通过完整离线门禁的不可变源码执行部署验证，没有重跑源码全量，也没有通过浏览器或真实模型重问用户的任务。脚本检查证明工具路径可运行，不能证明模型每次都会选择正确工具。旧聊天记录保留原回答；后续须提交新问题观察模型行为。

## 启动与回退

API停止后，可从仓库根运行：

```powershell
& data/verification/checklist-service-deploy-20261021/start_entries.ps1
```

该入口固定读取本轮快照；worker的`docucheck-worker:latest`已指向新镜像。旧`direct-answer-deploy-20261020/start_entries.ps1`仍属历史入口，不应再次用于当前启动。

已保留`docucheck-worker:pre-checklist-20261021`旧镜像与旧API源码快照。成功核验后移除了停止的旧worker容器，避免两个相同Compose服务标签影响后续启动；挂载资料未删除。如需回退，先停止API并排空任务，使用当前相同配置按旧镜像重建worker，再启动本轮API入口的`--rollback`模式；不只切换镜像标签就声称已回退。

本机证据目录：`data/verification/checklist-service-deploy-20261021/`，包括`before.json`、`applied.json`、`api-runtime.json`、`verified.json`、`source-commit-verification.json`、`build-healthcheck.json`及旧容器移除记录。真实模型新增0，未推送GitHub。
