# 达芬奇免费版（DaVinci Resolve Free）5 分钟微调指南

> **先回答最关心的问题：达芬奇收费吗？**
> - **DaVinci Resolve 免费版**：**永久免费、无水印、可商用、无任何月费/VIP 弹窗**！支持到 **4K UHD 60fps** 导出、完整多轨剪辑、变速、字幕、专业调色、音频混音，以及原生导入 `.otio` 和 `.fcpxml`。
> - **DaVinci Resolve Studio 付费版**：一次性买断（约 $295，无月费），主要多出内置 AI 语音转字幕、Magic Mask 抠像、降噪和 Windows 10-bit H.265 硬件解码。
> - **为什么配合 CastCut 完全不用买付费版？** 因为语音转字幕、挑片、粗剪、精剪、信息卡都已经在本地由 CastCut + Agent 完成，达芬奇免费版只负责最后 10% 的时间线微调、简单调色和导出！

---

## 一、下载与安装

官网免费下载 **DaVinci Resolve（不要选带 Studio 字样的版本）**：
- https://www.blackmagicdesign.com/products/davinciresolve （支持 macOS / Windows / Linux，内置简体中文界面）。

---

## 二、4 步完成最后 10% 微调与出片

1. **新建项目**：打开达芬奇 → 右下角齿轮「项目设置」确认帧率（通常 `30`）与画幅（横屏 `1920×1080` / 竖屏 `3:4` `1080×1440`，对齐 `coolbot` 系列避开上下遮挡）。
2. **导入 CastCut 生成的时间线**：
   - 菜单栏点击：`文件 (File)` → `导入 (Import)` → `时间线 (Timeline)`；
   - 选择 `episodes/<slug>/exports/timeline.otio`（如遇旧版本兼容问题，改选同目录 `timeline.fcpxml`）；
   - 勾选「自动将源素材导入媒体池」，点击确定。你会立刻看到已经剪好的多轨时间线：
     - `V1`：主剪辑轨（已带高光/段落彩色标记 Marker 与变速；手机 `9:16` 或横竖混拍异比例素材已自动预烘焙成高斯模糊背景，打开即无黑边）
     - `V2`：透明 HUD 信息卡（标点、拟饵、中鱼计数，严格避开右侧 `148px` 互动区与底部字幕区；不想要某张直接按 Delete 删掉）
     - `V3`：右下角频道标识 `@路亚知马力`（独立轨道，一键开关/移动）
     - `A1`：原声现场轨
     - `A2`：BGM 音乐轨（已自动烘焙人声/中鱼水花闪避的 `bgm_ducked.wav`，开箱即听，也可直接整轨调音量或替换）
3. **拖入精校字幕**：
   - 将 `exports/subtitles.srt` 拖入左侧媒体池，再拖到时间线最上方即可自动生成字幕轨；可在右侧检查器统一改字体、字号与描边（**完全免费，无 VIP 字体坑**）。
4. **微调 & 导出**：
   - 切到底部「剪辑」页拉动切点微调细节；想调色切到底部「调色」页稍微拉一下对比度/饱和度（户外钓鱼水色更通透）；
   - 切到底部最右侧「交付 (Deliver)」页 → 选 `H.264 Master` 或 `MP4` → `添加到渲染队列` → `全部渲染`。

---

## 三、常见问题避坑

- **Windows 端导入后某些视频显示红色「媒体离线」或只有声音没有画面？**
  - 原因：相机/iPhone 拍摄的 **10-bit H.264/H.265** 素材在 Windows 达芬奇免费版受限（macOS 较宽松）。
  - 解决：CastCut 在 `ingest` 和 `qc` 时会自动报警，运行一条命令即可把 10-bit 素材自动转成达芬奇免费版通用的 8-bit 高清中间片并更新工程引用：
    ```bash
    uv run castcut prepare-davinci episodes/<slug>
    uv run castcut export episodes/<slug> --formats otio,fcpxml,srt
    ```
- **横屏时间线里混入了历史竖屏视频怎么办？**
  - CastCut 渲染的 `exports/fine_preview.mp4` 已自动套用高斯模糊背景（`blur_pillarbox`）。
  - 在达芬奇里导入原片后，右键竖屏片段或使用免费自带特效 `OpenFX → 滤镜 → 消隐填充 (Blanking Fill)` 拖到竖屏片段上，**1 秒实现同款高斯模糊背景**（完全免费）。
