# 开发验证入口与真实验收

Python3.12/已安装项目锁；使用当前Conda环境。所有报告用新文件名。工具不安装依赖、不启动服务、不读取凭据。前端profile只执行已安装的测试/构建，没有Python网络守卫，不称绝对网络隔离。

## 常用入口

```powershell
python.exe -m scripts.dev_workflow doctor
python.exe -m scripts.dev_workflow plan --base HEAD
python.exe -m scripts.dev_workflow check --profile docs --report data/verification/workflow-docs.json
python.exe -m scripts.dev_workflow check --profile targeted --tests backend/tests/test_dev_workflow.py backend/tests/test_offline_verification.py --report data/verification/workflow-targeted.json
python.exe -m scripts.dev_workflow check --profile full --report data/verification/workflow-full.json
python.exe -m scripts.dev_workflow check --profile frontend --report data/verification/workflow-frontend.json
```

- doctor只列本机源码/Python/锁/npm可用性，不检查服务、迁移、身份或业务。
- plan包含未跟踪且未忽略文件；以指定提交比较，风险保守建议，不自动执行测试，不免除CI。检查已提交步骤可用对应父提交为base。
- docs检查八份入口的本地文件链接/UTF-8/围栏和LF规范化字节；不验证远程URL、页内锚点、历史全文语义。
- targeted必须显式选择backend/tests中的test_*.py文件；继承配置会被原有sanitized_env清除，pytest网络守卫开启；没有网络计数/实际通过测试不能算成功。
- full复用verify_offline的pip/Ruff/全量pytest；另留*.backend.json原报告，CI原门禁不变。超时和失败不自动重跑或当作成功。
- frontend复用npm test和npm run build，不启动Vite、不npm install。必要安装需按用户约定说明。
- 报告绑定commit、脏工作区diff/未跟踪内容摘要、Python/系统/已安装依赖摘要、范围、退出码与耗时。摘要是身份记录，不是报告签名或安全认证。

## 真实业务端到端协议

统一的是入口和证据规则；本轮没有把所有业务操作包装成自动E2E命令，避免在已有采购工作区暗中写数据。沿[演示](interview-demo.md)、[复现](reproduction.md)和[评估](evaluation.md)执行已明确场景：

1. 先确认运行源码/迁移/服务状态；源码完成、进程存在、HTTP健康均不等于功能部署成功。
2. 使用唯一合成工作区和合成原件，记录创建的项目/Case/Run/索引标识；禁止输入gold/labels等参考答案给Agent。
3. 浏览器或真实HTTP走上传→解析/索引→需求候选→保存确认→审查→来源追问→原件→修正/历史→导出。
4. 业务断言先固定：数量和单位、含税口径、交期起点/日历、缺证据与冲突、品目/权限范围；核对SQL状态、来源哈希、模型/工具轨迹和报告一致性。
5. 记录模型/Embedding真实调用及未知用量，失败算入分母；仅清理由本次协议创建的资源，不清理原Case/索引。已有受控清理机制按授权使用。

MySQL/Milvus/真实模型开关仍使用各测试和评估器的既有显式协议，不从普通开发环境自动继承。参考[A3范围及实连边界](document-item-binding-a3-2026-10-10.md)。

## 回滚

基线标签：workflow-baseline-2026-10-10（90ce7ad）。本地data/backups/workflow-2026-10-10含bundle、manifest和已克隆的验证目录。已提交改动按最近到最早git revert，后续业务提交存在时先检查冲突，不直接reset主工作区。

无需改变主工作区即可检查备份：

```powershell
git bundle verify data/backups/workflow-2026-10-10/docucheck-before-workflow.bundle
git show workflow-baseline-2026-10-10:CLAUDE.md
```

bundle仅是Git源码备份，不含.env、数据库或采购原件；它们本轮未改。原始文档见[历史归档导航](README.md)。
