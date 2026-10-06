# M4-B：可信工作区与资料入口

日期：2026-09-30。用户已在 M4-A 文字讨论中确认“专业审查工作台”界面方向，并同意进入 M4-B。本步只实现身份、项目/Case 访问、资料上传与版本查看；对话、八字段矩阵、原件定位和导出属于后续 M4-C/D。

## 实现

- 本地 Keycloak OIDC 演示身份：`alice`、`bob`、`reviewer`；浏览器使用授权码加 PKCE，API 用 JWKS 验证 RS256 签名、issuer、audience、有效期与主体。演示密码只在忽略提交的 `.env` 和 realm 导入文件中生成/保存。
- `0010_project_access` 新增项目、成员及 Case 项目归属。项目角色为 owner/editor/viewer；项目与 Case 路由以及会话、Run、文档、证据等子资源在 HTTP 边界统一按所属 Case 检查。无权访问返回 404，未登录返回 401。旧 M3 的无身份模式只允许本机回环地址，不能作为共享部署方式。
- Vue 工作区接入项目和 Case 列表、上传角色/供应商绑定、当前资料解析状态轮询、只读历史快照列表及版本资料查看。历史版本有显著标记，上传仍针对当前版本。
- 解析继续使用 M3 的后台进程；本步没有调用 DeepSeek。

## 本机运行

在 `docucheck` Conda 环境及项目根目录：

```powershell
python.exe -m backend.app.dev_oidc_realm
docker compose up -d mysql
docker compose --profile identity up -d keycloak
python.exe -m alembic -c backend/alembic.ini upgrade head
```

分别在两个终端启动 API 与前端：

```powershell
.\scripts\start-api-oidc.ps1
```

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

浏览器打开 `http://127.0.0.1:5173/`。本地演示账号的密码保存在项目根目录 `.env` 的 `DOCUCHECK_OIDC_DEMO_PASSWORD`，不要提交或贴出。Keycloak 已有数据卷时 `--import-realm` 不会覆盖现有同名 realm；若是旧导入账号，首次登录可能要求补全姓名和邮箱。上传 XLSX 后可用 `python.exe -m backend.app.parse_background --once` 手动处理一批待解析资料；PDF 还需指定 MinerU 可执行文件，例如 `--mineru-executable 'E:\developTools\miniconda3\envs\docucheck-mineru\Scripts\mineru-kit.exe'`。一次 `--once` 最多领取一份资料，持续处理方式见 M3 文档。已失败快照可用 `python.exe -m backend.app.ingest_snapshot --case-id <Case ID> --snapshot-id <快照 ID> --mineru-executable <MinerU 路径>` 由本地操作员重试。

## 验证范围与边界

- SQLite HTTP 测试覆盖项目/Case 隔离、viewer 只读、会话直链隔离、历史版本不变及跨 Case 历史 ID 拒绝；真实 MySQL 的授权测试也通过。
- 真实浏览器已验证：`alice` 登录后创建项目/Case，上传样例报价 XLSX，Case 从版本 0 到 1；手动运行一次后台解析后页面自动显示“解析完成”。`bob` 登录后项目列表为空，直接访问 alice 的 Case、资料、证据 API 均为 404。之后上传需求 PDF 形成版本 2，切回版本 1 只显示当时的一份报价且有历史标识。
- 上传 PDF 后，首次在 Keycloak/API/Vite 同时运行时执行 MinerU 返回 `parser_failed`。关闭演示服务后，对同一原件直接解析得到 12 个证据块、`complete`；再通过 `ingest_snapshot` 手动重试，版本 2 的 PDF 与报价均为 `complete`。资源竞争是可能原因，尚无系统级诊断证明。当前后台进程不会自动重试此类 `parser_failed`；运维可手动重试，正式部署需要资源配额和失败重试/告警验收。
- 代码审查后补上唯一 owner 保护、前端过期轮询响应拦截，以及稳定版本游标翻页和并发加载保护。真实 MySQL 的 owner 保护/空历史定向测试 **1 passed**。
- 最终回归：后端 **426 passed, 22 skipped**；Ruff、`pip check`、`git diff --check`、Alembic `0010_project_access (head)` 通过；Vue 类型检查、生产构建、Vitest **2 passed**，`npm audit` 为 0 漏洞。全量测试一次在 C 盘临时 SQLite 建表时遇到系统 `disk I/O error`，该用例单测通过；将 pytest 临时目录移至项目 E 盘后全量通过。跳过项依赖 MySQL/Redis/容器或真实服务的显式开关，不能算作通过。
- 本机 MySQL 升级前已备份到忽略提交的 `data/backups/m4-pre-0010-2026-09-30.sql`，迁移到 `0010_project_access`。演示项目/Case/报价留在本机数据库供后续 M4-C 使用。Keycloak、API、前端验证进程在测试后已停止；MySQL 仍由已有 Docker 服务管理。
- 这套配置是本地内网演示基础，不代表公网多租户生产验收。正式部署前仍需公司 OIDC/SSO、HTTPS、账号生命周期与撤权、迁移回滚演练、限流/分页及备份恢复等运维验收。旧无项目归属的 M3 Case 在 OIDC 模式下不会自动归入新项目。
