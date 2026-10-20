# 资料关联弹窗实施计划

> **For agentic workers:** 按已获用户确认的范围在当前会话逐项执行；不扩展到清单问答、关联版本续用、全局操作提示或历史资料加载。

**Goal:** 点击“关联采购品目”立即在可视区域打开独立弹窗，不再要求下滑寻找编辑区。

**Architecture:** 将现有 `DocumentItemBindings` 外壳改为原生模态 `dialog`，保留现有读取、编辑、确认、权限与幂等请求逻辑。固定标题、文件名和底部操作区，仅中间表单滚动；浏览器原生模态负责隔离背景与键盘焦点，关闭时恢复入口焦点。

**Tech Stack:** Vue 3、TypeScript、原生 HTML dialog、现有 CSS、Vitest、真实 Chromium。

## 已确认设计和范围

- 用户已于 2026-10-20 确认第 3 个问题的独立弹窗方案并要求完成。
- 标题“关联采购品目”，文件名常驻顶部；宽度最多 760px，高度适应屏幕，小屏留 16px 边距。
- 中间区域独立滚动，底部保留关闭按钮及当前规则允许的确认操作；当前的确认理由、勾选与版本保护不变。
- 原文预览在弹窗中展开；历史记录仍只读。点击背景不关闭，Escape 与关闭按钮都在保存中受阻，避免打断确认。
- 这一步不添加成功 toast、不改错误分类，不修复历史读取或关联版本策略；后端及其他任务未提交修改保留。

## 步骤与验收

- [x] 先补失败回归：数据未返回时已打开模态并显示文件名；确认按钮在滚动区之外；Escape/按钮关闭恢复焦点；保存中不能关闭；卸载不残留模态。
- [x] 在 `frontend/src/components/DocumentItemBindings.vue` 添加 dialog 引用，挂载调用 `showModal()`，通过统一关闭入口处理 `cancel` 与关闭按钮；调整模板为标题、滚动区、底部操作区。
- [x] 在 `frontend/src/style.css` 使用 `.binding-dialog[open]` 控制 flex 外壳，`::backdrop` 显示遮罩，中间区域设置 `min-height: 0; overflow: auto`，沿用现有颜色与控件。
- [x] 执行 `npm test -- src/components/DocumentItemBindings.test.ts src/components/DocumentsPanel.test.ts`，确认先失败后通过；随后 `npm test` 与 `npm run build`。
- [x] 用真实 Chromium 和隔离合成响应检查桌面/小屏布局、背景隔离、Tab/Escape、滚动后按钮可见以及原文预览；不调用模型、不写业务库。
- [x] 自审差异并记录验证边界，只暂存本步文件，独立本地提交。

组件测试使用可控的 `showModal`/`close` 模拟；浏览器的真实布局、焦点约束和顶层显示必须由浏览器验证补足。现有业务服务不可达时，隔离合成响应只能证明前端行为，不冒称真实保存实连。

执行结果：初始 6 项回归因缺少模态和固定操作区失败，补修后相关 36 项通过；真实浏览器发现原生 Tab 循环会经过浏览器界面，增加首尾控件循环及第 7 项先失败回归。最终前端 229 项通过，类型/构建通过，详见[交付记录](../../repair-item-binding-dialog-2026-10-20.md)。
