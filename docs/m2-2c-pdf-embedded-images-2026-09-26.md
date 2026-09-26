# M2-2c：PDF 嵌入图片覆盖检查（2026-09-26）

本步修正混合 PDF 中，正文可提取、图片表格却被 MinerU 静默漏掉时，文件仍可能被标为 `complete` 的问题。没有调用 DeepSeek、启动 Docker、安装依赖或修改前端。M2-2 的矢量表格、其他 XLSX 结构与任务级限额仍未完成。

## 第一步：复现问题

测试生成一页 PDF：普通正文写着 `Supplier specification`，嵌入图片写着 `Installed RAM 32 GB`。模拟 MinerU 中间结果只包含正文，并声明整份文件已处理。修正前，pypdf 的 `extract_text()` 只得到正文，文字覆盖校验全部通过，正式 `parse_pdf` 入口错误返回 `complete`；失败回归先确认了这个结果。

## 第二步：修正解析覆盖

`verify_pdf_source_coverage` 现在逐页查看原 PDF 的嵌入图片。只要页面含图片，就记录 `page:<零基页码>:embedded_image_unverified`，将文件标为 `partial`；原有可提取文字逐行校验继续执行。正式 `parse_pdf` 和开发模拟包的 `bind_document` 缓存入口共用此检查。解析版本增加 `/embedded-image:1`，新快照中的证据 ID 随版本变化。

这里的 `partial` 表示现有文字证据不足以证明图片内容已覆盖，不表示图片一定包含采购信息。模型不据此自动读取或识别图片。

## 第三步：验证与保存

同一回归验证正式 PDF 入口及缓存绑定入口都从 `complete` 变为 `partial`，并保留页码与原因；原有纯文字开发 PDF 仍为 `complete`。在 `docucheck` Conda 环境，后端离线测试 **118/118 项通过**，全目录 Ruff 与 `pip check` 通过。本步没有重新运行真实模型对话。

## 边界与下一步

pypdf 的图片列表可能包含未实际绘制的图片资源，因此会保守地产生 `partial`。当前没有图片文字 OCR，也不把图片自动转换为可引用证据。矢量线框、绘制成路径的表格、注释外观等非图片区域仍可能漏过此检查；下一小步 M2-2d 针对矢量绘制和表格结构设计覆盖判定。之后还需处理 XLSX 其他不支持结构与任务级限额，才能验收整个 M2-2。
