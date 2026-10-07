# 当前源码安全证据

本目录固定工具版本、官方规则版本及逐项处置。执行入口是 `python -m scripts.security_evidence`。它不是 Git hook，也不是生产安全认证。

## 工具与规则

- Semgrep CE 1.179.0：装在独立 venv，不改变 `docucheck` Conda 依赖。
- Gitleaks 8.30.1：Windows zip 的 SHA256 见 `tools.json`。
- 官方 `security-audit` 225 条规则：原始 YAML 哈希和规范化 JSON 哈希均已记录。`rule-versions.json` 固定每条规则的版本 URL、原 ID 和规范化内容哈希；可重新下载重建，不依赖滚动规则集的当前内容。
- 官方规则受 [Semgrep Rules License](https://semgrep.dev/legal/rules-license/) 限制分发，规则正文留在被忽略的 `data/security-tools/rules/`。本仓库只交付版本、地址和哈希。上游不可用或哈希变化时重建失败，不能替换成最新规则后宣称复现成功。
- `project-rules.yaml` 是本项目自有规则，检测特权容器配置；也为 YAML、Vue 等文件提供明确的通用文本检查。通用文本检查不等于完整语义解析。
- `gitleaks.toml` 基于 [v8.30.1 官方配置](https://github.com/gitleaks/gitleaks/blob/v8.30.1/config/gitleaks.toml)，保留222条原检测规则和其余模式级 allowlist，删除全局 paths，以及包含 paths 的整个规则级 allowlist（避免只删 paths 后把 AND 条件放宽）。因此默认被排除的 lock 文件也扫描。MIT 许可见 `GITLEAKS-LICENSE`。运行时还禁用源码 `gitleaks:allow` 注释并固定空 ignore 文件。

安装示例（项目根目录；工具安装/下载需要网络）：

```powershell
python -m venv data/security-tools/semgrep-1.179.0
& data/security-tools/semgrep-1.179.0/Scripts/python.exe -m pip install semgrep==1.179.0
# 本次 Windows / Python 3.12 的完整工具依赖快照：semgrep-windows-py312.txt。
# 重建同平台环境可改用 pip install -r ops/security/semgrep-windows-py312.txt。
# 从 tools.json 中的官方 release 下载 Gitleaks，核对 zip SHA256 后解压。
& data/security-tools/semgrep-1.179.0/Scripts/python.exe -m scripts.fetch_security_rules --output data/security-tools/rules/security-audit.json
```

当前机器已完成安装，并实际重建全部 225 条规则、核对整个规则包哈希。工具依赖快照纳入本目录，当前运行使用的工具版本/可执行文件哈希和原始报告哈希进入 receipt。

## 执行

先用 `git add <本步文件>` 将新文件纳入清单。扫描读取 **Git 管理文件的当前工作区字节**，包括已跟踪文件的未暂存修改；不要求这些修改已经提交。未跟踪文件须先暂存，才属于本次明确的扫描范围。

```powershell
python -X utf8 -m scripts.security_evidence `
  --semgrep data/security-tools/semgrep-1.179.0/Scripts/semgrep.exe `
  --gitleaks data/security-tools/gitleaks-8.30.1/gitleaks.exe `
  --rules data/security-tools/rules/security-audit.json `
  --work data/security-evidence/run-unique-id `
  --temp E:/sgtemp `
  --receipt docs/security/evidence/run-unique-id.json
```

`--work`、`--receipt` 必须是新路径。Windows 当前环境的较长临时目录会触发原生引擎 socketpair 错误，使用短本地 `--temp` 路径；不通过关闭错误处理绕过它。

## 门禁和边界

1. 为每个 Git 文件记录路径、字节数和 SHA256。文本复制到新快照；二进制/非 UTF8 原件逐文件注明排除原因。`.env`、采购上传件、依赖目录和旧工作副本不在 Git 清单内。不能据此宣称这些被忽略内容没有泄漏。
   为避免报告递归绑定自身，只单独排除本次两个已知生成物 `r13-final-2026-10-07.json` 和 `r13-delivery-2026-10-07.md`；不会整目录排除 `docs/security/evidence/`，目录里新增的源码仍必须扫描。
2. Python、TS/JS（含 mjs/cjs）、Vue、YAML/JSON、shell/PowerShell 与 `Dockerfile*` 必须在 Semgrep `paths.scanned` 中。解析错误、规则跳过、缺少覆盖都失败；无代码空文件明确不要求扫描。
   Windows 原生入口会展开独立的 `*.yaml` 参数，所以使用 `--include=*.yaml` 的单参数形式，防止只扫描根目录匹配文件。`Dockerfile.worker` 额外建立同字节/hash的 `Dockerfile` 别名，以应用 Docker 语义规则；别名、原路径和hash明确记录，发现映射回原文件，最终也核对别名字节未变化。
3. Gitleaks trace 必须覆盖每个非空文本输入。读取/引擎告警、覆盖缺失、报告缺失或结构错误都失败；正常的“发现泄漏候选”计数告警保留为发现，不算引擎故障。
4. 原始发现先保留，再按“工具、规则、路径、行、源码哈希”指纹处置。源码或位置变化使豁免失效；孤立的旧豁免也失败。不得用整文件排除或拼接假 key 作为修复依据。
5. 原始 JSON/日志留在忽略目录；提交的 receipt 不复制 `Secret`、`Match`、源码片段或扫描器消息。脱敏证据保留每个发现的位置、规则、源码哈希、原因和复核人。原始文件哈希用于核对本机扫描记录。
6. 最终核对原工作区和快照字节/hash、Git 文件清单；扫描期间变化会失败。新机器若 Git 换行策略不同，也可能使具体源码 hash 和既有处置失配，须复核新发现，不能忽略指纹变化。

来源目录已去掉两处公开附件 URL 的 `accessCode` 参数。文件 hash 和公开公告 `landing_url` 保留；若下载需要新参数，请从公告页重新获取公开入口，不能保证去掉参数的原件地址可直接下载。这不代表 Git 历史已清理，也没有轮换项目密钥。

`complete` 表示工具和声明范围完整；`passed` 还要求所有发现有具体处置。即使 `passed=true`，仍不等同于“无漏洞”。Git 历史、依赖 CVE、容器漏洞、运行时攻击、跨文件业务推理和 MySQL/OIDC/真实模型端到端验收分别进行。扫描使用本地规则、关闭 Semgrep metrics 和版本联网检查，不调用模型。参考 [官方 metrics 说明](https://docs.semgrep.dev/metrics)。
