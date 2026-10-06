# M4-C：Agent 与复核工作台验收记录

日期：2026-10-01。范围依据 [M4 四个大步骤](m4-large-step-plan-2026-09-30.md)及已确认的[工作台设计](superpowers/specs/2026-09-30-m4-workbench-design.md)。

**状态：M4-C 合成样例范围内完成。** 已在真实 MySQL 8.4、本地 OIDC 和浏览器中验收单家、三家、追问、原件定位、修正及历史快照。M4-D 尚未开始；本记录不代表真实 DeepSeek 输出质量或公网生产验收。

## 本次实现

1. 对话工作台按当前 Case/快照提交标准或深入模式问题，也可发起八字段完整审查；展示 Run 记录、答复、排队/运行/澄清/部分完成/失败/中断/取消、进度事件，以及取消、重试和澄清恢复。页面提示请求及必要资料片段会发到已配置模型服务并可能计费。本轮没有真实 DeepSeek 调用。
2. 新增 `GET /api/v1/cases/{case_id}/runs` 和 `GET /api/v1/agent-runs/{run_id}/checks`。矩阵只读取结构化 CheckResult；正式标识要求 Run 已完成、为当前快照、字段处理完成、复核无需人工确认，并且保存的事实集可经原证据与当前规则重新算出同一结果。审查中补了复算后重新读取 Case 版本、删除状态、Run 及修正复核状态的栅栏，防止并发上传或删除把旧结果误标为正式。旧快照、部分/失败/中断及无法复算的结果不提升为当前正式判定。
3. 新增受 Case、快照、文档 ID 三重约束的原件读取接口。读取前校验相对存储路径、大小与 SHA-256；响应禁止缓存与内容嗅探，并作为附件返回。前端用 PDF.js 在本地渲染指定页和归一化坐标框；XLSX 从已验证原件读取目标单元格及周边 7×5 区域，展示原工作表并高亮单元格，也可下载原件。原文以普通文本显示，不作为 HTML 执行。
4. 接入已有事实集、提交修正、规则复核接口；只修改原文支持的提取数值/单位。修正后产生新快照，界面回到旧 Run 所在历史快照显示旧值、新值和 `needs_review`，可切换回当前版本。列表提供历史 Run/快照追溯；切换快照时关闭旧证据面板，轮询请求用序号避免旧响应覆盖新状态。
5. 发现原上传缺设备型号绑定，导致上传路径无法给出可靠的同型号确定性判定。迁移 `0011_document_model_binding` 在**每份不可变文档版本**保存可选 `model_name`，避免后续更换型号追改旧快照；新工作台上传报价/规格书时要求填写型号。旧 API 允许不填以保持兼容，旧记录仍为 `NULL`，规则会保守给出待确认，而不会凭供应商名称猜型号。

## 真实 MySQL 与浏览器验收

- Docker 恢复后先备份 MySQL，备份存于被忽略的 `data/backups/docucheck-pre-0011-20261001.sql`。Windows 将原主机端口 `3307` 划入保留范围；Compose 和宿主 Python 现在共同读取 `DOCUCHECK_MYSQL_PORT`，本机 `.env` 设为 `13307`，容器仍使用 `3306`。真实 MySQL 的 Alembic `current` 为 `0011_document_model_binding (head)`。镜像已用当前源码重建，MySQL、Redis、Keycloak、worker、dispatcher、cleaner 正常启动。
- 从 Alice 的页面创建单家 Case `5deb2ad553cd44f3b6f4afbdbee3d870`，上传需求、报价、规格书并绑定 `DC-14-A`。三份原件哈希与开发包清单一致，使用已验证的 MinerU 中间 JSON 缓存解析 PDF，XLSX 从原件解析；三份均为 `complete`。Run `b3d52bb020ad49b09d69a99463976ae4` 使用无网络的 `ScriptedProvider` 完成，8/8 格经原文和规则复算为当前正式结果。浏览器点击来源后显示 PDF 第 1 页原件和 XLSX `报价!B6` 的单元格邻域。
- 从页面创建三家 Case `b9b36fb187fa4fbba8d3851cb90c03bd`，逐份上传 7 份资料并绑定各家 `DC-14-A/B/C`，版本到 7，7 份均解析完成。Run `5fc280f4827946c9af9ad888f769ce15` 由脚本 Provider 完成；API 和页面均显示三家各 8 项，共 **24/24** 个当前正式格子。此开发包复用相同字节的报价和规格样例，供应商隔离由上传元数据、证据作用域与规则核验保证；它不是三套独立真实投标文件。
- 从页面在同一三家会话提问“供应商 B 的重量是否达标？请给出来源”。首次标准模式在模型调用前因上下文超预算返回 `partial/context_budget`；修复为仅把本次问题相关供应商、字段的已验证结果句柄放进决策上下文。页面对 Run `505513dce38845e7ab410802acd524ac` 点击重试后，由本地 Provider 用 2 次决策完成，答复引用需求 `1.6 kg` 与 B 规格书 `1.8 kg`。
- 为验收原文支持的修正，给单家 Case 上传合成 XLSX，单元格同时写有“标称 SSD 512 GB；标称 SSD 1 TB”，形成 v4。Run `2f54cea2bad64b2cae4e0f66d05017d1` 对存储保守标为部分处理；页面将提取事实 `512 GB` 修正为原文同样支持的 `1 TB`，生成 v5。复核为 `needs_review`，保留双值冲突；切换回 v4 时旧矩阵全部显示非正式，v5 不继承旧 Run 的正式结论。
- Bob 使用有效 OIDC 令牌直接 GET Alice 三家 Case 文档与 Run 结构化结果，两者均返回 `404/not_found`；Alice 对同一 Case 请求返回 `200`。验收期间暂停自动派发与 worker，未调用 DeepSeek；所有合成 Run 结束、确认无活跃 Run 后才恢复服务，dispatcher 跳过了已完成 Run 的待发消息。

## 代码修复与验证

- 浏览器联调发现完整审查的内存结果使用通用 `fields-v1` 规则，而持久化恢复只接受 `memory-v1`；内存 `FieldFactSet` 恢复也误走普通字段验证器，导致整个矩阵返回 `memory_scope`。现按规则版本和事实集类型恢复，并重新用原文验证内存事实；同一条持久化 Run 修复后可读出 8/8 正式格子。新增回归覆盖真实八字段会话的序列化与恢复。
- 后端全目录回归 `432 passed, 22 skipped`；真实 MySQL 定向 `7 passed`，前端 Vitest `9 passed`、Vite 构建、`npm audit --audit-level=high`（0 漏洞）、Ruff 和 `pip check` 通过。此前 SQLite 的 `0011` 升降级测试继续保留。
- 本次 PDF 验收使用开发包中与原件哈希匹配的缓存解析结果，**没有验证本机实时 MinerU 新文件解析**；模型调用使用确定性本地 Provider，**没有验证 DeepSeek 的稳定性、费用和真实资料效果**。对象存储、病毒扫描、限流、监控、恢复演练及独立资料质量仍在 M5 范围。M4-D 还需导出、完整自动化与交付审查。

参考实现依据：[PDF.js 官方示例](https://mozilla.github.io/pdf.js/examples/index.html)、[OWASP 授权检查](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)、[OWASP 文件上传控制](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)。
