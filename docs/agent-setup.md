# Antigravity 与 Codex 接入指南

`castcut` 原生同时支持 **Google Antigravity** 与 **OpenAI Codex**，两者共享同一套确定性 CLI、MCP Server 与 `SKILL.md`。

---

## 一、Skills 自动发现

本仓库已经同时在两处放置了完全一致的技能定义：
- `.agents/skills/castcut-vlog-editor/SKILL.md`（通用 / Antigravity）
- `.codex/skills/castcut-vlog-editor/SKILL.md`（Codex 项目级自动发现）

只要在 `castcut` 仓库目录下启动 **Antigravity** 或 **Codex**，直接说：
> 「帮我把 `episodes/2026-spring-bass/raw` 里的路亚素材做挑片、粗剪、精剪，导出达芬奇免费版工程」

Agent 就会自动按 SOP 执行全流程。

---

## 二、可选接入本地 MCP Server（`castcut-mcp`）

先安装 MCP 依赖：
```bash
uv sync --extra mcp
```

### 1. Codex CLI 配置
在项目目录运行或写入 `~/.codex/config.toml`：

```toml
[mcp_servers.castcut]
command = "uv"
args = ["run", "--directory", "/你的本机路径/castcut", "castcut-mcp"]
```

### 2. Antigravity 配置
在 Antigravity 的 MCP 配置（如 `mcp_config.json`）中加入：

```json
{
  "mcpServers": {
    "castcut": {
      "command": "uv",
      "args": ["run", "--directory", "/你的本机路径/castcut", "castcut-mcp"]
    }
  }
}
```

---

## 三、双 Agent 各自的最佳用法

- **用 Antigravity 剪实拍素材（最推荐）**：
  - `ingest` 后让它直接用 `read_file` 看 `work/proxies/*.mp4`（原生听水花/泄力声、看弯弓刺鱼动作），挑片准确率最高；
  - 精剪渲染完后，让它直接看 `exports/fine_preview.mp4` + `exports/qc_contact.jpg` 做端到端视听验收。
- **用 Codex 跑批量与工程迭代**：
  - `ingest` 后让 Codex 结合 `work/contacts/*_sheet.jpg`（时间码 16 宫格拼图）与 `work/clips.json`（字级转写+音频峰值）快速挑片、改写 `rough_cut.yaml` / `fine_cut.yaml`、跑批量导出与脚本扩展；
  - 用 `codex exec --image episodes/<slug>/exports/qc_contact.jpg "检查成片拼图是否有黑屏、遮挡或裁切问题"` 做快速视觉验收。
