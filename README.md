# CastCut — 路亚钓鱼 Vlog Agent 剪辑流水线

**CastCut** 是一套面向 **实拍路亚钓鱼 Vlog** 的本地优先 Agent 剪辑工程，同时原生适配 **Antigravity** 与 **Codex**（共享 CLI、本地 MCP Server 与 `SKILL.md`）。

核心目标：把几小时原始拍摄素材（头戴/胸挂运动相机 + 手机/微单横竖混拍）的 **挑片 → 粗剪 → 精剪 → 字幕/信息卡/BGM 闪避 → 工程导出** 自动化完成约 **90%**，最后 **10%** 在完全免费、无水印、无 VIP 弹窗的 **达芬奇免费版（DaVinci Resolve Free）** 中轻松微调出片。

---

## 核心特性

- **路亚专属挑片引擎**：结合本地 Whisper 语音转写（内置路亚词库纠偏：米诺/波爬/德州/无铅/标点/刺鱼/溜鱼/炸线等）、音频能量峰（抓水花、泄力出线、惊呼）与场景切点，自动把长素材拆成可打分、可打标的候选镜头。
- **双画幅自适应混剪（对齐 `coolbot`）**：`aspect: auto` 按素材占比自动选 **横屏 `16:9` (`1920×1080`)** 或 **竖屏 `3:4` (`1080×1440`)**——竖屏采用 3:4 天然避开抖音/小红书上下遮挡且主页列表零裁切，并遵循相同安全区（右下互动栏 `148px` 避让、字幕安全带与 `@路亚知马力` 角标）；手机 `9:16` 或横竖混拍素材默认自动套用**高斯模糊背景填充（`blur_pillarbox`）**，告别大黑边（也支持 `crop_fill` / `fit`）。
- **双 Agent 一等公民**：
  - 生成低码率 720p 代理片（`work/proxies/`），供 **Antigravity** 原生看视频、听现场声挑片与成片审片；
  - 生成带时间码水印的缩略图拼图（`work/contacts/`），供 **Codex** 视觉挑片与高效脚本迭代。
- **豆包语音口播旁白（与 `coolbot` 配置同源）**：支持给风噪素材/空镜一键补配**火山引擎豆包 TTS**（Seed-TTS 2.0/1.0/ICL，默认音色 `zh_male_m191_uranus_bigtts` 云舟 2.0，直接复用 `coolbot` 的 `.env` 变量名）；内置路亚多音字纠偏（`快调/调性` vs `微调`）、字级精准字幕对齐、内容哈希防重复计费缓存，以及达芬奇独立 **A2 口播音轨**。
- **零 VIP 专业工程导出**：
  - 主导出：`timeline.otio` + `timeline.fcpxml` + `subtitles.srt` + `overlays/*.png`（透明 V2 信息卡），直接导入 **DaVinci Resolve 免费版**（零会员费、无水印）；
  - 内置 **10-bit 预警与一键转码**（`castcut prepare-davinci`），避免 Windows 达芬奇免费版出现素材离线；
  - 备选导出：零 VIP 纯基础轨剪映草稿（`--formats jianying`）。

---

## 快速安装（macOS / Windows / Linux）

前置要求：Python 3.11+ 与 [`uv`](https://docs.astral.sh/uv/)（推荐同时安装系统新版 FFmpeg，未安装时也会自动回退使用随包附带的 `imageio-ffmpeg`）。

```bash
git clone <your-repo-url> castcut
cd castcut

# 基础安装（含 CLI、FFmpeg 渲染、OTIO/FCPXML/SRT 导出）
uv sync

# 推荐完整安装（额外启用本地 faster-whisper 语音转写 + MCP Server）
uv sync --extra asr --extra mcp

# 环境自检
uv run castcut doctor
```

> macOS 推荐额外执行 `brew install ffmpeg`；Windows 推荐 `winget install Gyan.FFmpeg`（以便同时拥有最新版 `ffmpeg` 与 `ffprobe`）。

---

## 5 步完成一集路亚 Vlog

```bash
# 1. 初始化单集（aspect=auto 会按素材自动判断横屏或竖屏）
uv run castcut init episodes/2026-spring-bass --title "早春管理场寻鲈" --location "近郊管理场"

# 2. 把原素材放进 episodes/2026-spring-bass/raw/（支持横竖混放、可附带 .srt 字幕）

# 3. Stage 1 挑片预处理（探测元数据、音频峰值、转写、代理片、时间码拼图）
uv run castcut ingest episodes/2026-spring-bass
uv run castcut clips episodes/2026-spring-bass

# （可选：让 Antigravity / Codex 看代理片或拼图后，用 castcut label 微调镜头分类和留弃）

# 4. Stage 2 & 3 粗剪 -> 精剪 ->（可选）豆包口播配音 -> 渲染预览 -> 导出达芬奇工程
uv run castcut plan episodes/2026-spring-bass --stage rough
uv run castcut render episodes/2026-spring-bass --stage rough   # 秒级看粗剪节奏

uv run castcut plan episodes/2026-spring-bass --stage fine
# 若在 fine_cut.yaml 写了后期口播 voiceover_text / voiceovers，先复制 .env.example 为 .env 并填入豆包 Key：
uv run castcut tts episodes/2026-spring-bass                    # 调用豆包 TTS 生成口播 MP3 + 字级字幕
uv run castcut render episodes/2026-spring-bass --stage fine    # 完整预览（含混画幅/HUD/口播/BGM闪避）
uv run castcut export episodes/2026-spring-bass --formats otio,fcpxml,srt

# 5. Stage 4 自动质检（输出 qc_report.json 与验收拼图 qc_contact.jpg）
uv run castcut qc episodes/2026-spring-bass
```

或者直接一条命令跑完全流程原型：

```bash
uv run castcut run episodes/2026-spring-bass
```

---

## 目录说明

- `knowledge/lure_glossary.yaml`：路亚 ASR 纠偏词库与高光信号词（可按个人口头禅扩充）
- `docs/davinci-free-guide.md`：**达芬奇免费版 5 分钟零基础微调上手指南**
- `docs/agent-setup.md`：**Antigravity 与 Codex 接入指南（Skills + MCP）**
- `.agents/skills/castcut-vlog-editor/` & `.codex/skills/castcut-vlog-editor/`：双端通用剪辑 Skill
