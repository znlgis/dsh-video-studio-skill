---
name: video-studio
description: 用代码从零生成中文视频（科普片/纪录片/宣传片/动画短片）：写剧本→edge-tts 旁白→实测时长排时间轴→PIL 或 Canvas 逐帧渲染→ffmpeg 编码→numpy 采样级混音→自动质检。当用户要"做一个视频 / 生成宣传片 / 把报告变成视频 / 做动画 / 配音配字幕 / 出成片 / 修视频音画不同步 / 视频画质糊"，或提到 ffmpeg、edge-tts、逐帧渲染、字幕、分镜、配乐、混音时使用。 | Code-generated video production (Chinese narration, deterministic frame rendering, ffmpeg, edge-tts, QA gates). Use when asked to make a video, turn a report into a video, animate a story, fix A/V sync or blurry text in a generated video.
---

# 视频工作室（video-studio）

**一句话：视频 = 剧本 + 音频实测时长 + 确定性逐帧函数 + ffmpeg 编码 + 自动质检。**
画面与声音全部由代码生成，零素材、可复现、可断点续跑、可脚本化验收。

## 0. 铁律（违反任意一条，成片一定会出问题）

1. **音频先做，画面后做**。时间轴必须由 TTS 实测时长反推，**绝不按字数估算**。按字数排的时间轴会在第 20 句开始明显漂移。
2. **每帧是纯函数**：`draw(t, frame) -> Image`。禁 `random`/`time()`，要随机用 `filmkit.hash2/hash3/vnoise`。同一帧号必须永远画出同一张图。
3. **字幕与大字必须落在安全区**：正文 `y ∈ [58, 974]`，字幕带 `y ∈ [872, 1030]`，永不超过 1030。
4. **原始帧直通编码**：PIL 帧 → `rawvideo` 管道 → `libx264`。**不要**用 `cv2.VideoWriter` 做中间文件（慢 2 倍且二次有损）。
5. **混音用 numpy 采样级叠加**，不要用一条 ffmpeg 命令喂上百个输入（Windows 上会非确定性静音）。
6. **交付前必须跑质检脚本 + 亲眼看 3 张静帧**（用 `read_image`）。"能出图"不等于"没问题"。

## 1. 五步上手（最快路径）

> 下文的 `<SKILL_DIR>` 指**本次加载技能时 `skill` 工具回报的 `resourceBase.path`**。装成插件后它就是
> 本插件包内的 `skills/video-studio/`（profile 的 `node_modules/@dsh-gis/dsh-video-studio-skill` 是链接）。
> 找不到时按名字定位：`find ~ -path '*/skills/video-studio/filmkit.py'`。

```bash
# 1) 建工作目录，拷模板
mkdir -p myfilm && cd myfilm
cp <SKILL_DIR>/assets/film.json .            # 剧本模板（改文字即可）
cp <SKILL_DIR>/assets/scenes.py .            # 场景模板（改画面即可）
cp <SKILL_DIR>/scripts/{filmkit.py,render.py} .

# 2) 改 film.json 的 lines[].text / sub / big / items
#    text = 朗读文本（自然口语）；sub = 上屏字幕（短，一行优先）

# 3) 一条命令出片（TTS→排版→混音→渲染→合成→质检）
python render.py --script film.json --scenes scenes.py --out out/film.mp4 --work work

# 4) 亲眼看质检抽出的帧
#    work/qa/sheet.png      联系表（全片调性）
#    work/qa/snap_*.png     全分辨率静帧
```

分阶段跑（改一处不用全跑）：

```bash
python render.py ... --stage tts      # 只合成旁白 + 量时长
python render.py ... --stage layout   # 只排时间轴（不渲染）
python render.py ... --stage frames   # 只渲染帧
python render.py ... --stage audio    # 只混音
python render.py ... --stage mux      # 只合成 + 质检
```

## 2. 标准八阶段（P0–P7）

| 阶段 | 产出 | 关键动作 | 常见错误 |
|---|---|---|---|
| **P0 定基准** | `CONVENTIONS.md` | 分辨率/帧率/时长/BPM→帧换算一次算死，全项目引用同一份 | 各文件自己写 `150` 这种魔数 → 幕边界对不齐 |
| **P1 写剧本** | `film.json` | 每句拆 `text`（朗读）与 `sub`（字幕）；每段一个 `kind` | 一个字段两头用 → 字幕太长或读起来生硬 |
| **P2 事实核查** | `FACTS.md` | 上屏的每个数字/年份有出处编号；建"不能上屏"黑名单 | 跳过这步 → 画面和旁白数字打架，评论区第一条就是你错了 |
| **P3 TTS** | `work/tts/*.mp3` + `tts.json` | edge-tts 逐句合成，`ffmpeg -f f32le` 实测时长，磁盘缓存 | 按字数估时长 → 字幕漂移 |
| **P4 排时间轴** | `work/timeline.json` + `sub.srt` | `dur = lead + 实测语音 + tail`，句读必须在本段内 | 段太短 → 旁白被下一段截断 |
| **P5 混音** | `work/audio.wav` | numpy 总线：旁白 + 配乐 + 音效按采样点叠加 | ffmpeg 多输入混流 → 非确定性静音 |
| **P6 母版** | 3 张静帧 | 先渲 3 帧看构图/配色/字重，定了再渲全片 | 直接渲全片 → 36000 帧后发现字体丑 |
| **P7 成片** | `out/film.mp4` | rawvideo→libx264 CRF18 + 两遍 loudnorm 到 -16 LUFS | 码率过低/响度 -25 LUFS → "像草稿" |

## 3. 渲染引擎怎么选

| 场景 | 选型 | 实测速度 | 适合 |
|---|---|---|---|
| 图文/数据/科普/UI 风 | **PIL（numpy+cv2 辅助）** — `filmkit` 默认 | 40–100 fps @1080p30 | 绝大多数中文科普片、报告转视频 |
| 需要流畅矢量/粒子/3D/纸纹质感 | **无头浏览器 Canvas2D/WebGL + CDP 截图 → image2pipe** | 190–400 ms/帧（核显） | 纪录片级长片、需要复杂视觉语言 |
| 纯数据动画 | **manim**（若已安装） | 视场景而定 | 数学/公式推导 |

**默认走 PIL**：依赖少（numpy+Pillow 就够）、启动快、确定性强、调试成本低。
只有当你需要"一镜到底的粒子形变 / 实时着色器质感"时才上浏览器方案（见 `references/scene-authoring.md`）。

## 4. 画质清单（这几项决定"像不像专业片"）

- **亮度与层次**：暗底 (`#0a1420`) + 顶部亮 accent；不要纯黑纯白，用户屏幕会吃掉渐变。
- **字重与字号**：大字 84–96px、副标题 34–38px、字幕 46px；中文用 `msyhbd.ttc`（微软雅黑粗体）。
- **字幕**：≤ 2 行、单行 ≤ 24 个中文字、带半透明背板、`y ≥ 872`。
- **节奏**：每 3–6 秒要有视觉变化（新卡片 / 位移 / 数字滚动）；静止超过 6 秒观众就走。
- **镜头**：只做 3–15% 缓推，不要大幅拉远拉近。
- **调色**：整片一个主色 + 一个强调色，别超过 4 个色相。
- **音频**：母带 -16 LUFS / 真峰 ≤ -1 dBTP；旁白比间隙响 12 dB 以上；AAC 前留 1.4 dB 余量。

## 5. 质检门禁（交付前必过）

```bash
python render.py ... --stage mux     # 内置 qa_report：分辨率/音轨/时长/响度/锐度比/黑帧
```

| 判据 | 阈值 | 失败怎么办 |
|---|---|---|
| 分辨率/帧率 | 严格等于设定值 | 检查 FrameSink 参数 |
| 音轨存在 | 必须有 | mux 参数漏了 `-i audio.wav` |
| 时长 | 与 `frames/FPS` 差 < 0.5 s | 断点续渲漏帧 |
| 响度 | -16 ± 1 LUFS | 用 `filmkit.loudnorm_two_pass` |
| 真峰 | ≤ -1 dBTP | 前瞻限幅 |
| **编码锐度比** | **≥ 0.85**（原始帧 vs 解码帧 Laplacian 方差） | 降 CRF 或提 preset |
| 长黑帧 | 首尾以外 0 段 | 检查素材/淡入淡出 |
| 字幕安全区 | `qa_text_safe` 无警告 | 缩短字幕或调 SUB_BAND |

**"不能有"和"必须有"两类都要查**：闪烁/重叠/出画/削波是"不能有"；图名框/淡入/字幕/每段至少一个动态元素是"必须有"——后者最容易静默失效。

## 6. 常见故障速查

| 现象 | 根因 | 修法 |
|---|---|---|
| 后半段全程静音 | ffmpeg 多输入混流竞争 | 改 numpy 总线 `bus[i:i+n] += src*gain` |
| 混音爆音/削波 | 先 `astype(float32)` 再判 dtype，归一化分支不执行 | **先判 dtype 再转**，并打印中间峰值 |
| 字幕越出画面被裁 | 字幕带贴底 | `SUB_BAND` 上移到 872，`base_y` 夹紧 |
| 文字发虚 | cv2 中间文件二次压缩 | 改 rawvideo 直通 + CRF18，用锐度比验证 |
| 音画不同步 | 按字数估时长 | 改实测时长驱动 |
| 字幕交叠 | 按字符比例分配字幕时长 | 以 TTS 句边界为窗，窗内按标点切 |
| 全片该有的淡入/图名框消失 | 字段改名没跑冒烟 | 改名后必跑 `snap`；质检加"元素存在性" |
| 渲染越跑越慢 / 内存涨 | 每块开新浏览器没关 | 复用会话 + `finally` 关闭 |
| 拼出的片子缺一段还不报错 | 分块按"第几块"命名 | 按**绝对起始帧**命名 + 显式缺口检测 |
| **请求 524 帧却只出 488 帧** | `-shortest` 按较短一路（音频）截断 | 去掉 `-shortest`，显式 `-t <视频时长>` + 校验音视频时长差 < 0.05 s |
| 产出 13 小时的怪物文件 | `-stream_loop -1` 让 `-shortest` 失效 | 显式 `-t`，别依赖 `-shortest` |
| BGM 混音后掉 6 dB / 听不清 | `amix` 默认 `normalize=1` 会按输入数均分 | `amix=...:normalize=0` 再 `loudnorm=I=-16` |
| 旁白整体偏移 ~0.6 s | `adelay` 铺轨有系统性偏移 | 别用 adelay，改 numpy 按采样点写位 |
| 烧录字幕字号小得看不清 | SRT 交 libass 按 `PlayResY=288` 排版 | `FontSize/MarginV` 要 ×3.75，或直接用 ASS 指定 PlayRes |
| ASS 字幕位置全乱 | Format 行漏了 `MarginL/MarginR/MarginV` 字段 | ASS 头 Format 行字段顺序必须与 Dialogue 完全对应 |
| 渲染出上一部片的内容 | `sys.path` 顺序导致 import 到旧模块 | 用绝对路径 + 每次全新进程；子进程别继承父进程模块缓存 |
| 画面里 ✓ × ↵ 显示成方框 | msyhbd 缺这些字形 | 用矢量画（ImageDraw 线条），不要用符号字符 |

## 7. 参考资料（按需加载）

- `references/pipeline.md` — 八阶段详细规格、契约 JSON 格式、目录约定、并行分工
- `references/scene-authoring.md` — 场景函数写法、构图规则、Canvas/CDP 方案、超采样与后处理
- `references/audio.md` — 采样级混音、程序化配乐/音效、响度链路、循环环境音
- `references/quality-gates.md` — G0–G6 全部门禁、可复制代码、验收清单
- `references/pitfalls.md` — 20 条最佳实践 + 16 条反模式 + 3 个非确定性 bug 复盘

## 8. 脚本资产

| 文件 | 作用 |
|---|---|
| `scripts/filmkit.py` | 生产内核：字体/文本换行/字幕安全区/缓动/确定性随机/音频总线/TTS/时间轴/FrameSink/混音/质检 |
| `scripts/render.py` | 编排器：`--stage` 分阶段，产物落盘可续跑 |
| `assets/scenes.py` | 场景模板（3 种示范：开场/正文卡片/收尾），复制改成自己的 |
| `assets/film.json` | 剧本模板 |

## 9. 验收话术（不要自我宣称"做好了"）

交付时必须能贴出：
1. `ffprobe` 的时长/分辨率/码率
2. `ebur128` 的积分响度与真峰
3. 编码锐度比（≥0.85）
4. 联系表路径 + "已用 read_image 亲眼确认"的具体观察
5. 字幕条目数 = 剧本句数（无静默丢失）

## 10. 作者机器已验证的环境事实（2026-11-06 实测）

> **本节是作者机器（Windows 11 + Ryzen AI 9 HX 370 + OSGeo4W Python 3.12）的实测基准，不是跨机器承诺。**
> 换机后请先用 5 分钟重测：`ffmpeg -version`（是否有 libx264/aac/loudnorm）、
> `python -c "import numpy,PIL,cv2"`、`dir C:\Windows\Fonts\msyhbd.ttc`、`edge-tts --list-voices`。
> 任何一项不符，先改 `filmkit.FONT_CANDIDATES` / `FILMKIT_FFMPEG` 环境变量，别硬跑。

**这些数字是实测的，不是文档抄的。**

| 项 | 实测结论 |
|---|---|
| ffmpeg | **9.0.2 full_build**（gyan.dev），路径 `C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe` |
| 编码器 | libx264 / libx265 / libvpx-vp9 / **aac（native）** / amf 全有；**无 libfdk_aac、无 nvenc**（本机无 N 卡） |
| 滤镜 | drawtext（支持 fontfile）、subtitles/ass、zoompan、xfade、afade、amix、loudnorm 全在 |
| ⚠ 第二套 ffmpeg | `C:\Program Files\EVCapture\ffmpeg.exe` 是 2019 旧版且**没有 xfade**，别混用 |
| Python | OSGeo4W 3.12.16；numpy 2.5 / PIL 13 / cv2 5.0 / scipy 1.18 / edge-tts 7.3.1 / matplotlib / faster-whisper 已装 |
| Python 缺 | **moviepy / pydub / soundfile / librosa / manim 都没装** → 代码里别 import 它们 |
| 字体 | msyh/msyhbd/simhei/simsun/simkai/simfang/Deng + Noto Sans/Serif SC；**PIL 加载 ttc 必须带 `index=0`**（index=2 会抛 OSError） |
| 字形缺口 | msyhbd 缺 ✓ × ↵ → 用矢量线条画，不要用符号字符 |
| TTS | edge-tts 可用，zh-CN 8 个音色；单句 4 s 语音约 4 s 合成 |
| 硬件 | Ryzen AI 9 HX 370 / 12C24T / 59.6 GB / **Radeon 890M 无 CUDA** |
| 编码速度 | x264 `-preset medium -crf 17` = **141 fps（4.7× 实时）**；x265 94 fps；vp9 65 fps |
| 硬件编码 | h264_amf 127 fps —— **不比 x264 快，体积大 2.5 倍，不用** |
| 端到端 | 17.47 s 成片（524 帧 + 3 句旁白）编码 4.99 s = **3.5× 实时** |
| 实测渲染 | PIL 中等复杂度场景 **40–54 fps**（1080p30） |
| 磁盘 | C 剩 307 GB / D 剩 521 GB |

### 排期公式（保守）

```
Pillow 路线：  墙钟秒数 ≈ 帧数 / 40 / 并行度 + 30%  余量
浏览器路线：  墙钟秒数 ≈ 帧数 × 0.3 / 并行度 + 30%  余量
重负载画面（多图/模糊/纹理）：按 12–23 fps 估，而不是 40–54 fps
```

### 三条来自实战的"隐形坑"

1. **`-shortest` 会按较短一路截断**：音频比视频短 1.2 s 时，请求 524 帧只出 488 帧，且不报错。**永远显式 `-t` 并校验两路时长差 < 0.05 s**（`filmkit.mux` 已内置此检查）。
2. **`-stream_loop -1` 让 `-shortest` 失效**：会产生 13 小时的怪物文件。循环输入必须配显式 `-t`。
3. **`amix` 默认 `normalize=1`**：按输入数均分，2 路输入直接掉 6 dB。要么 `normalize=0`，要么走 numpy 总线（推荐）。
