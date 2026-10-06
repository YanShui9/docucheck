# 聊天式采购助手前端实施计划

> 按已定稿设计执行，使用 executing-plans / test-driven-development；独立契约及完成审查按 subagent-driven-development 做规格和质量两层复核。沿用当前工作区，不提交、重置或覆盖既有修改。

**Goal:** 用户通过中文聊天、上传和按需面板完成动态采购核验。

**Architecture:** 保留 App 的项目权限、资料版本与异步栅栏；新的独立 ChatWorkspace 使用持久 conversation/messages、Run/assistant-result，Composer 负责输入法和发送，工作面板保留既有上传/原件/历史能力。动态结果与确认版本在第二步接入，不改写旧报告。

**Tech Stack:** Vue 3 / TypeScript / CSS / Vite / Vitest / 现有 FastAPI API 与 OIDC；不安装新的界面依赖。

用户已于2026-10-05明确选择“按推荐方案开始改前端”，见 [定稿设计](../specs/2026-10-05-chat-ui-redesign.md)。按三个大步骤执行与验收，不拆为几十次确认。第一步完成后报告其范围和下一步。

## 一、整体骨架与真实聊天

文件职责：

- `frontend/src/components/ChatComposer.vue`：受控文本、多行高度、组合输入保护、上传入口、发送/停止。
- `frontend/src/components/ChatComposer.test.ts`：Enter、Shift+Enter、IME、禁用/停止、输入保留。
- `frontend/src/components/ChatWorkspace.vue`：真实会话选择、消息/Run读取与轮询、发送/停止/重试、来源引用；同任务草稿和迟到响应栅栏。
- `frontend/src/components/ChatWorkspace.test.ts`：revision0聊天、历史恢复、不能串任务/会话、错误/重试幂等及轮询覆盖。
- `frontend/src/api.ts`：新增 conversation/message/assistant 与 Run读取类型，保留 legacy类型。
- `frontend/src/App.vue`、`CaseSidebar.vue`：浅色聊天主页、任务导航、折叠、首次操作建立任务、资料/历史结果按需打开。
- `frontend/src/style.css`：保留旧面板所需类，追加明确chat命名空间及设计变量；不影响原文查看。
- `frontend/src/App.test.ts`：保留原上传/创建/修正迟到响应测试，适配按需面板；新增空任务和跨项目创建保护。

- [x] 先写 Composer 和 ChatWorkspace 的失败测试，运行 `npm run test -- src/components/ChatComposer.test.ts src/components/ChatWorkspace.test.ts`，确认未实现失败。
- [x] 实现 GET conversations/messages、GET runs 与每个 Run 当前状态，POST messages 必须 `operation=assistant` + `expected_revision` + 稳定提交幂等key；聊天无需snapshot非空。
- [x] 页面基于真实消息和对应 Run 生成答复；恢复进行中的 Run，禁止并发轮询覆盖新状态。本轮逐页读取到 next_offset=null；最多20页，超过上限明确报错，第三步补渐进分页入口，不把截断说为完整历史。
- [x] 发送前保留文本与绑定；失败重发同一次未确认请求保持幂等key，成功后清空。取消/重试仅作用于绑定任务Run；失效/结果未知不自动重复调用模型。
- [x] 调整导航、首页、底部输入及资料抽屉；英文Case替换为采购任务，版本说明中文化；旧八字段结果入口清晰标为历史兼容。
- [x] 回归 `npm run test`、`npm run build`，必要 API 契约离线验证；启动本机前端并用浏览器检查实际页面、登录、空资料聊天、窄屏及无横向溢出。

## 二、动态指标与采购工作面板

- [ ] 新建 ChecklistPanel / ReviewPanel 与相应测试；以已确认且启用的指标生成清单/矩阵行，按品目分组，保留旧八字段类型分支。
- [ ] 接入 proposal/changes、草稿PUT/confirmPOST、稳定ID/资料和清单revision，候选无自动保存确认；保存成功不等于确认成功。
- [ ] 供应商中文名称及任务摘要缺失时最小补充现有读取响应，测试权限/版本，不用临时映射冒充持久名称。
- [ ] 上传来源用途/产品标识泛化，事实修正与审计、报告/原件/Excel导出联动；旧版本只读。
- [ ] 跑前端全量、类型/构建、必要后端定向以及真实合成动态采购闭环。

## 三、交互完善与统一验收

- [ ] 有限记忆偏好设置、期限/删除，中文错误/无命中/降级/未完成区别；不展示未实现的自动长期记忆、语音或额外工具按钮。
- [ ] 浏览器检查大屏/窄屏、面板焦点/返回、键盘、历史滚动、取消/澄清/失败、撤权与升版。
- [ ] 全面审查异步栅栏、注入/HTML、版本与来源、接口契约；最终测试/构建、必要真实服务验证，记录源码、成绩、费用未知和边界。
- [ ] 更新 CLAUDE/README/roadmap 与前端交接，保留每轮失败及旧阶段记录。

## 本轮记录

第一步已完成：前端36项测试通过、vue-tsc/Vite构建通过；独立契约和质量复核已执行并修复恢复/停止竞态。真实OIDC首次revision0苹果采购聊天completed，刷新恢复与390px小屏检查通过。详见 [第一步交付](../../chat-ui-step-one-2026-10-05.md)。第二/第三步仍未完成，不把聊天界面交付视为动态报告与记忆设置全部交付。
