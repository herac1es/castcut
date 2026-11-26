# CastCut 项目规范（路亚钓鱼 Vlog 实拍剪辑）

本项目专注**实拍路亚钓鱼 Vlog 的「挑片 → 粗剪 → 精剪 → 零 VIP 非编工程交接」**，目标是让 AI Agent（同时支持 **Antigravity** 与 **Codex**）完成约 90% 的重复剪辑工作，最后 10% 由人工在**达芬奇免费版（DaVinci Resolve Free）**中微调出片。

与纯代码生成动画教学项目 `coolbot` 不同，本项目处理的是**本地真实拍摄素材**（运动相机头戴/胸挂、手机/微单横竖屏混拍）。

---

## 一、核心工程原则

1. **严禁直接修改 `raw/` 原素材**：`raw/` 视为只读素材库；所有代理片、转码片、剪辑单、导出文件一律写入 `work/` 与 `exports/`。
2. **禁止裸拼长串 FFmpeg 命令**：剪辑、混画幅、变速、混音、导工程统一通过 `uv run castcut ...` 或 `castcut-mcp` 原子工具执行，确保音画同步、跨平台一致。
3. **先定结构再精剪（两阶段确认）**：
   - 挑片完成后，先产出 `work/rough_cut.yaml` + `exports/rough_preview.mp4` 核对叙事与节奏；
   - 确认节奏后再升级 `work/fine_cut.yaml`、生成字幕/HUD 卡片/BGM 闪避与 NLE 工程。
4. **零 VIP 交接原则**：
   - 默认主导出为 `exports/timeline.otio` + `exports/timeline.fcpxml` + `exports/subtitles.srt` + `exports/overlays/*.png`，直接导入**达芬奇免费版**（免费、无水印、无 VIP 弹窗）。
   - 如需导出剪映草稿（`--formats jianying`），只允许使用基础免费轨（视频、音频、基础纯文本字幕），严禁写入 VIP 滤镜/花字/贴纸。
5. **双画幅与安全区规范（对齐 `coolbot` 系列）**：
   - 横屏 `16:9`：`1920×1080`（4K：`3840×2160`）；竖屏 **`3:4`**：`1080×1440`（4K：`1620×2160`，严守达芬奇免费版 2160p 高度上限），抖音/小红书竖刷居中展示天然避开上下遮挡且主页 3:4 作品列表零裁切。
   - `aspect: auto` 按原素材总时长占比自动在 `16:9` 与 `3:4` 间选择；异比例素材混拍（含手机 `9:16` 进 `3:4`、横屏进 `3:4`、竖屏进 `16:9`）默认自动采用 `blur_pillarbox`（高斯模糊背景填充），避免生硬黑边，也可按镜头改成 `crop_fill` 或 `fit`。
   - 安全区对齐 `coolbot`：下半区（`y >= height/2`）右侧预留 `148px` 避开互动图标，字幕区 `left: 48, right: 148, bottom: 48`，右下角常驻低干扰频道标识 `@路亚知马力`（可通过 `channel_handle: ""` 关闭）。

---

## 二、路亚 Vlog 剪辑准则

1. **高光优先与去水化**：
   - 开头 3–5 秒放全场最具张力的咬口/炸水/弯弓/翻车瞬间做 Hook（不剧透完整抄鱼结局）；
   - 大段空抛、走路、低头换线果断剔除（`waste`）或压缩为 1.6x–2.0x 过渡镜头（`casting_action`）。
2. **保留现场钓感声效与豆包后期口播**：
   - 刺鱼水花、泄力出线、口播讲标点时必须保留原声并压低 BGM（自动闪避 `bgm_duck_db`），切忌把音乐开太大盖住水声与人声。
   - 现场风噪大或需要补录旁白解说时，在 `fine_cut.yaml` 的镜头 `voiceover_text` 或 `voiceovers` 列表写口播词，运行 `uv run castcut tts <episode_dir>` 调用**火山引擎豆包语音（与 `coolbot` 共用 `.env` 凭据与默认音色 `zh_male_m191_uranus_bigtts`）**；同一集保持统一主讲音色，合成结果自动走内容哈希缓存（`work/tts_cache/`），并在达芬奇里单独落 **A2 轨（`A2 Voiceover TTS`）** 与字级精准字幕。
3. **术语准确与生态合规**：
   - 字幕必须经过 `knowledge/lure_glossary.yaml` 路亚词库纠偏（米诺/波爬/德州钓组/无铅/倒钓/标点/刺鱼/溜鱼/炸线等），避免台钓串味词（如打窝/炸窝）。
   - 封闭管理场/练杆坑可强调科学摘钩与放流；涉及自然开放水体时，严禁鼓励或美化向无黑鲈分布水域放生外来物种加州鲈。

---

## 三、双 Agent 协作约定（Antigravity & Codex）

- 详细操作 SOP 见 [.agents/skills/castcut-vlog-editor/SKILL.md](./.agents/skills/castcut-vlog-editor/SKILL.md)（Codex 同路径镜像在 `.codex/skills/castcut-vlog-editor/SKILL.md`）。
- **Antigravity**：优先直接用 `read_file` 原生观看 `work/proxies/*.mp4` 挑片，最后原生回看 `exports/fine_preview.mp4` 与 `exports/qc_contact.jpg` 做端到端视听验收；素材文件很多时可并行派发 Subagents 分批看片打标。
- **Codex**：优先结合 `work/contacts/*_sheet.jpg`（多帧时间码拼图）+ `work/clips.json` 字幕与音频峰值做挑片，直接编辑 `work/rough_cut.yaml` / `work/fine_cut.yaml`，并用 `exports/qc_contact.jpg` 做视觉复核。

---

## 四、验收要求

- 修改 `src/castcut/` 代码后必须运行：`uv run ruff check src tests && uv run pytest`。
- 涉及渲染或导出改动后，须确认合成素材 E2E 测试通过并核验 `qc_report.json` 为 `passed: true`。
