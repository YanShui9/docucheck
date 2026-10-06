# M5-C 复现、离线 CI 与面试交付实施计划

**Goal:** 将已测的 M0–M5-B 项目交付为可安装、可离线回归、可移交数据报告、可解释和演示的面试项目。

**Architecture:** 按[已授权设计](../specs/2026-10-03-m5-c-delivery-design.md)在当前共享仓库顺序实施；保留已有未提交改动及历史证据，不切到缺少这些资产的旧 Git 基线。业务源码与交付工具版本分别记录。

**Tech Stack:** Python 3.12 / pytest / Ruff / GitHub Actions / Vue现有Vitest与Vite / 标准库ZIP与SHA-256。

## 1. 离线门禁与依赖复现

- [x] 为 `scripts.offline_guard` 和 `scripts.verify_offline` 写网络阻断、被捕获异常仍失败、MockTransport可用、外部opt-in清除、缺字体失败的测试；先观察失败，再实现。
- [x] 核对实际主机/worker依赖元数据，更新Windows/dev锁、加入worker完整锁及Docker安装pip check；记录共同版本选择。
- [x] 加入 `.github/workflows/offline-ci.yml`：固定Action SHA、只读权限、无服务/凭据；中文字体存在；后端离线脚本与前端npm ci/test/build两个job。
- [x] 校验：`python -m pytest backend/tests/test_offline_verification.py -q`；随后 `python -m scripts.verify_offline --report data/m5-c/offline-host.json`。

## 2. 可验证的验收资料归档

- [x] 为 `evals.delivery_bundle` 写默认预览、缺文件/变更/凭据拒绝、ZIP哈希/条目/路径/符号链接/重复/限额拒绝、新目录恢复和拒绝覆盖测试；先红后绿。
- [x] 实现prepare/build/verify/restore；白名单固定M5合成原件、标签、AI复核与各版本整轮报告，不携带.env、上传目录、身份导入或私有CA。
- [x] 校验：`python -m pytest backend/tests/test_delivery_bundle.py -q`；`python -m evals.delivery_bundle --build --output data/m5-c/docucheck-m5-development.zip`；随后verify和恢复到新目录并重验资产。

## 3. 启动、恢复和面试材料

- [x] `docs/reproduction.md`：Conda/锁、Node锁、MinerU单独环境、OIDC、Compose、迁移、后台解析、API/前端及健康检查；明确目录、端口、字体、凭据和验收运行开关。
- [x] `docs/backup-and-recovery.md`：停止写入的一致备份、卷/上传原件/数据库边界、隔离恢复与核验；高风险覆盖恢复只给显式操作步骤，不自动覆盖现有数据库。
- [x] `docs/interview-demo.md` 与 `docs/architecture-and-interview.md`：10分钟操作、八字段/来源/修正/历史/导出、Agent工作流和RAG/规则分工、Outbox/fence/版本与失败解释，所有数字绑定报告。

## 4. 干净环境演练与收尾审查

- [x] 新目录只复制源码、锁、必要测试资产；不含.env、原data与node_modules。隔离新环境安装锁并实际执行pip check、离线后端与现有前端test/build。
- [x] 按需告知后使用一次性Docker校验Linux/字体/worker锁；实际运行与没有运行的环境分别记录。
- [x] 对照设计和M5条件复审，检查所有文档链接、归档哈希、测试跳过及网络阻断；最终运行Ruff和git diff --check。
- [x] 更新CLAUDE/README/路线图/M5计划，给出M5-C交付报告和机器可读验证记录。M5正式独立验收门槛保持未完成，不虚构云端CI、人工封存或账单。

## 完成记录与范围

2026-10-03：四个内部实施项均已完成，仍属于用户约定的一个 M5-C 大步骤。新增34项测试；原Conda、Windows隔离、Linux隔离后端各724 passed/22 skipped、网络尝试0；前端18项及构建通过；最终227项归档载荷已验证并恢复、资料完整性错误0。见[交付报告](../../m5-c-delivery-2026-10-03.md)和 `data/m5-c/final-audit.json`。

干净安装演练使用新的Windows/Linux venv；本机主开发环境仍为Conda。没有冷建新Conda/MinerU环境、生产数据库/OIDC恢复或远程GitHub运行；恢复Compose仅配置校验，Linux镜像以本机已有worker为基础。业务源码不变、真实模型新增调用0，正式独立封存和生产发布条件仍未完成。
