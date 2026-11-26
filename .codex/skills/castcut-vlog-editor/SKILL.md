---
name: castcut-vlog-editor
description: 路亚钓鱼实拍 Vlog 的全流程 Agent 剪辑技能（挑片、粗剪、精剪、字幕与信息卡、零 VIP 工程导出与成片验收）。同时适配 Antigravity 与 Codex；当用户要剪辑钓鱼实拍素材、挑中鱼高光、混剪横竖屏素材或导出达芬奇/剪映工程时使用。
---

# CastCut 路亚 Vlog 剪辑 SOP（Antigravity & Codex 通用）

你的任务是协助用户把 `episodes/<slug>/raw/` 中的真实路亚钓鱼素材，完成约 **90%** 的挑片、粗剪、精剪与交接工程产出，剩余 **10%** 交由用户在 **达芬奇免费版（DaVinci Resolve Free）** 中微调出片。

**绝对红线**：
- 严禁修改、删除或重命名 `raw/` 下的原始拍摄文件。
- 严禁手写长串裸 FFmpeg 命令拼接成片；统一调用 `uv run castcut ...` CLI 或 `castcut-mcp` 工具。
- 涉及自然开放水体时，严禁鼓励向无黑鲈分布水域放生外来物种加州鲈；管理场内强调科学摘钩与放流。

---

## Stage 0：环境与单集准备

1. 首次运行先执行 `uv run castcut doctor` 确认 FFmpeg、字体与 ASR 状态。
2. 如未初始化单集目录，运行：
   ```bash
   uv run castcut init episodes/<slug> --title "<标题>" --aspect auto --mixed blur_pillarbox --template fishing-vlog
   ```
   - `aspect=auto` 会根据 `raw/` 素材时长自动判定横屏（16:9）或竖屏（9:16）。
   - 如用户明确指定竖屏短视频（1–3 分钟），改用 `--aspect portrait --template short-highlight`。

---

## Stage 1：素材预处理与多模态挑片（Triage）

1. 执行预处理（扫描原片、音频峰值、场景切点、转写、720p 代理片、时间码拼图）：
   ```bash
   uv run castcut ingest episodes/<slug>
   uv run castcut clips episodes/<slug>
   ```
2. **双 Agent 差异化看片方式**（最关键一步，把启发式初筛升级为真正懂钓鱼的挑片）：
   - **Antigravity 模式**：
     - 直接使用 `read_file` 原生读取 `work/proxies/*.mp4`（同时看画面 + 听水花/泄力/口播）。
     - 若原素材超过 6 个长视频，可并行派发多个 Subagents 分批查看代理片并汇总镜头评分。
   - **Codex 模式**：
     - 读取 `work/contacts/*_sheet.jpg`（通过 `--image` 或视觉读取查看带 `MM:SS` 时间码的 16 宫格拼图）结合 `work/clips.json` 中的字幕文本与 `peak_db` 音频峰值判断镜头内容。
3. **按路亚 Vlog 分类体系逐段打标**（使用 `castcut label` 或直接编辑 `work/clips.json`）：
   - `hook_highlight`：全场最炸的 3–5 秒开场钩子（炸水、弯弓、泄力狂出、搞笑翻车）
   - `fight_catch`：中鱼、刺鱼、溜鱼、抄鱼、展示、摘钩放流（必留，尽量保留完整过程与原声）
   - `talking_strategy`：讲标点、水情、风向、换饵思路的口播（留核心，砍废话）
   - `gear_rig`：拟饵、钓组、竿轮线特写
   - `casting_action`：有代表性的抛竿/控饵动作（粗剪中可加速 1.6x–2.0x 过渡）
   - `broll_scenery`：水面、标点环境、路途空镜（2–4 秒点缀节奏）
   - `blooper`：挂底、炸线/炒粉、放枪、跑鱼（极具真实感与互动评论价值，高优保留）
   - `waste`：低头拍地面、重复空抛、无效走路、误触黑屏（标记 `--drop` 剔除）

---

## Stage 2：粗剪（Rough Cut — 先审结构与节奏）

1. 生成粗剪单与快速代理预览片：
   ```bash
   uv run castcut plan episodes/<slug> --stage rough
   uv run castcut render episodes/<slug> --stage rough
   ```
2. 检查并精修 `work/rough_cut.yaml`：
   - 核对叙事弧线：`hook（3–5s 倒叙预告） → intro_spot（交代标点与思路） → gear_setup（饵/钓组） → search_adjust（搜鱼/加速抛竿/遇阻） → blooper_moment → climax_catches（中鱼连发） → outro（复盘收尾）`。
   - 确认混画幅镜头（如横屏成片中插入历史竖屏素材）的 `framing` 策略：`blur_pillarbox`（默认模糊背景）或 `crop_fill`（主体居中可满屏裁切）。
   - 向用户汇报粗剪时长、结构分段与代理预览片 `exports/rough_preview.mp4`，确认节奏后再进入精剪。

---

## Stage 3：精剪、HUD 卡片与零 VIP 工程导出（Fine Cut）

1. 升级生成精剪单：
   ```bash
   uv run castcut plan episodes/<slug> --stage fine
   ```
2. 审核并润色 `work/fine_cut.yaml`：
   - **字幕校对**：检查 `subtitles` 是否存在路亚术语误识别（必要时补充到 `knowledge/lure_glossary.yaml`），单条字幕控制在 16–20 字以内。
   - **HUD 透明信息卡（`overlays`）**：确认章节名、拟饵克重/线号（`lure_info`）、上鱼计数（`catch_counter` 如 `中鱼 #1`）准确不遮脸。
   - **BGM 闪避**：若配置了 `bgm_path`，确认人声与刺鱼水花段均覆盖在 `ducking_intervals` 内。
3. 渲染精剪预览片并导出零 VIP NLE 工程：
   ```bash
   uv run castcut render episodes/<slug> --stage fine
   uv run castcut export episodes/<slug> --formats otio,fcpxml,srt
   ```
   - 若 `media_index.json` 提示存在 10-bit 素材且用户使用 Windows 达芬奇免费版，提醒运行 `uv run castcut prepare-davinci episodes/<slug>` 后重新 `export`。

---

## Stage 4：成片验收（QC Gate）

1. 运行自动质检：
   ```bash
   uv run castcut qc episodes/<slug>
   ```
2. **必须执行视觉复核**（不可只看命令返回 0）：
   - **Antigravity**：读取 `exports/qc_contact.jpg` 并直接查看 `exports/fine_preview.mp4` 确认画面比例、混画幅背景、HUD 卡片位置及音画同步。
   - **Codex**：检查 `exports/qc_report.json` 并用视觉能力读取 `exports/qc_contact.jpg` 确认无黑屏、无遮挡、无拉伸变形。
3. 交付给用户时，明确列出：
   - 审片预览：`exports/fine_preview.mp4`
   - 达芬奇免费版导入文件：`exports/timeline.otio`（或 `exports/timeline.fcpxml`）+ `exports/subtitles.srt`
   - 已自动剔除的废片占比、剩余建议人工微调的 10% 要点（如调色风格、局部精切点、是否替换 BGM）。
