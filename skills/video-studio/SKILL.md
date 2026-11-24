---
name: video-studio
description: 做中文视频的两条生产线：(A) 素材剪辑 footage——有素材（本地文件/链接/网上下载）就按文案剪成片，自带素材检索、转写定位镜头、字幕水印治理；(B) 零素材——没有素材且找不到时用代码逐帧画。两条都走 edge-tts 实测时长排轴 + 采样级混音 + 自动质检。做视频/宣传片/纪录片/配音配字幕/把报告变成视频/修音画不同步/修画面糊，或提到 ffmpeg、字幕、分镜、混音时使用；开工先跑 intake.py 问完 8 个问题。 | Two pipelines for Chinese videos — footage-cut (source clips -> narration -> timeline) or code-generated frames; ffmpeg, edge-tts, subtitles, QA gates.
---

# 视频工作室（video-studio）

**一句话：视频 = 剧本 + 音频实测时长 + 画面（素材或代码生成）+ ffmpeg 编码 + 自动质检。**
两条生产线共用同一套底盘（TTS 实测时长、时间轴、采样级混音、loudnorm、门禁），区别只在"画面从哪来"。

> 本文件只放**必须常驻上下文**的东西：铁律、五步上手、目录。
> 八阶段规格、引擎选型、画质清单、声音设计、各模块细节都在 `references/` 里，按需读（见文末目录）。
> 插件配置（`mode` / `optionalModules` / `strictGates`）会在加载时追加在本文末尾，没配置就没有那一段。

## 铁律（违反任意一条，成片一定会出问题）

1. **先分诊，再动手**：用户给了素材 / 链接 / 网上下载得到画面 → 走 **footage 线**（`scripts/footage.py`，契约是 `footage.json`）；
   都没有、且题材不该用现成画面（数据/抽象/品牌/动画）→ 走 **零素材线**（`scripts/render.py` + `work/design.json`）。
   两边都占 → 默认 **hybrid 形态 3**（素材打底 + 小面积叠加，`scripts/compose.py`）；**按段拼接必须显式 `--hybrid-form 1`**。
   走错线整片返工；细则见 [references/footage.md](references/footage.md) 与 [references/hybrid-overlay.md](references/hybrid-overlay.md)。
2. **先问诊，再提案（每个新成片任务的第一步）**：`intake.py` 固定 8 题**一次问完**，别一句一轮；
   制作方式、题材、时长、气质、声线、配乐没答完，`propose.py` 会拦。逃生口 `--no-intake` 会留痕。
3. **先提案，再出片（不论用户是否要求）**：`propose.py` 现场采样 N 个候选 → 用户挑一个 → `--confirm` 冻结 `work/design.json` → 才允许渲染。
   `--confirm` 之前 `render.py` 的 `frames`/`all` 会被提案闸拦下（`--require-proposal` 直接失败）。只有"纯修复/纯重渲"可跳过并说明原因。
4. **音频先做，画面后做**：时间轴必须由 TTS **实测时长**反推，**绝不按字数估算**（第 20 句起就会漂）。
5. **每帧是纯函数**：`draw(t, frame) -> Image`。禁 `random`/`time()`；要随机用 `filmkit.hash2/hash3/vnoise`，同一帧号永远画同一张图。
6. **字幕与大字落在安全区**：正文 `y ∈ [58, 974]`，字幕带 `y ∈ [872, 1030]`，永不超过 1030。
7. **原始帧直通编码**：PIL 帧 → `rawvideo` 管道 → `libx264`（默认 CRF18）。**不要**用 `cv2.VideoWriter` 做中间文件（慢 2 倍且二次有损）。
8. **混音用 numpy 采样级叠加**，不要一条 ffmpeg 命令喂上百个输入（Windows 上会非确定性静音）；母带用两遍 `loudnorm` 到 -16 LUFS / 真峰 ≤ -1 dBTP。
9. **声音跟着画面走**：该响的地方在场景代码里登记提示点（`scenes.py` 的 `cues()` 钩子 / `film.json` 的 `sfx.cues`），画面改了提示点自动跟着改。
10. **没看清就加停留，别整片放慢**：`film.json` 的 `hold`（全局）或 `lines[].hold`（单句）在语音结束后加停留；配乐 `bpm` 对齐小节。
11. **偏移全靠指纹绑死**：签核绑内容指纹（剧本 + `scenes.py` + 设计），改任一即失效；分块渲染按绝对起始帧命名并显式查缺口，改内容后 `--resume` 会拒绝复用旧分块。
12. **交付前必须跑质检 + 亲眼看 3 张静帧**（`read_image`）："能出图"不等于"没问题"。`skipped` 不等于通过，报告里要说明为什么没跑。
13. **默认 PIL 引擎**（`engine: pil`）：依赖少、确定性强。要"粒子形变/实时着色器质感"才上浏览器（`--engine web`，需 playwright + Chromium），换引擎要重做画面。

## 五步上手（零拷贝：不拷脚本，不拷模板）

> **这条路径没有任何"拷贝"动作。** 技能脚本留在技能目录里直接执行；工程目录里只需要一个 `film.json`（外加运行产生的 `work/` 与 `out/`）。
> `<SKILL_DIR>` = 本次加载技能时 `skill` 工具回报的 `resourceBase.path`（装成插件后就是本包内 `skills/video-studio/`）；`<REPO>` = 包根（`<SKILL_DIR>` 往上两级，`tools/` 与 `schemas/` 在它下面）。

```bash
# 1) 只写一个 film.json（工程目录里唯一需要你手写的文件）
mkdir -p myfilm && cd myfilm
cat > film.json <<'JSON'
{
  "title": "我的片子",
  "lines": [
    {"id": "l01", "kind": "hook",  "chapter": "01 · 开场",
     "text": "这是用代码从零生成的画面与声音。", "sub": "零素材，全代码生成", "big": "零素材，全代码生成"},
    {"id": "l02", "kind": "body",  "chapter": "02 · 正文", "kicker": "PIPELINE",
     "text": "整条管线只有五步：写剧本、生成旁白、排时间轴、逐帧渲染、混音合成。",
     "sub": "五步成片，每步落盘", "big": "五步成片",
     "items": [{"text": "剧本 film.json"}, {"text": "旁白"}, {"text": "时间轴"}, {"text": "逐帧渲染"}, {"text": "混音"}]},
    {"id": "l03", "kind": "outro", "chapter": "99 · 收尾",
     "text": "先定脚本，再定声音，最后才是画面。", "sub": "顺序比努力重要", "big": "顺序比努力重要"}
  ]
}
JSON
#    lines[].text = 朗读文本（自然口语）；sub = 上屏字幕（短）；kind = hook/body/quote/outro/emphasis/calm
#    画风/配乐/声线/节奏全部由 design 决定，film.json 里**没有** style 字段可写。
python <SKILL_DIR>/scripts/filmcheck.py film.json          # 契约校验（可选，几秒）

# 2) 先自检一次环境（可选；105 条回归护栏，约 10–25 秒，不联网；缺可选模块会标 SKIP）
python <SKILL_DIR>/scripts/selfcheck.py

# 3) 【铁律】先问诊（8 题一次问完），再出方案——同一个 brief 每次都不一样，必须让用户看过
python <SKILL_DIR>/scripts/intake.py --work work --questions --json --topic "AI 大模型的三年"
python <SKILL_DIR>/scripts/intake.py --work work --finalize
python <SKILL_DIR>/scripts/propose.py --brief "AI 大模型的三年，30 秒科普" --script film.json --work work
#    产出 3 个候选：各自一份 design + 静帧联系表（work/proposals/rev1/_compare.png）+ 设计距离，2 秒级、全程离线。看并排图，挑一个。

# 4) 确认候选，冻结设计（work/design.json）——此后渲染只认这份设计
python <SKILL_DIR>/scripts/propose.py --confirm --candidate A --work work
#    不满意就换一个：--revise "再冷一点，少装饰，换女声"（新 rev，旧 rev 留着眼 diff）

# 5) 先看静帧，再出全片；--scenes 不写就用技能内置渲染器
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage stills --stills "l01:3,l02:5,l03:2.5"
#    → work/stills/_sheet.png：用 read_image 亲眼看，别跳过
python <SKILL_DIR>/scripts/render.py --script film.json --work work --out out/film.mp4 --stage all
#    TTS→排版→配乐→音效→混音→逐帧→编码→质检 一条龙；中间产物全在 work/，可断点续跑。
```

**素材线（footage）走法**：契约是 `footage.json`，一条链 `footage.py --stage all`（下载/转写定位/切镜/混音/字幕/门禁），
需要叠加图文时再接 `compose.py --stage all`（形态 3）。完整命令与契约见 [references/footage.md](references/footage.md) 与 [references/hybrid-overlay.md](references/hybrid-overlay.md)。

```python
# 真实签名（六个参数，少一个都会崩）：scenes.draw_frame
def draw_frame(scene, t_local, dur, t_global, script, frame):
    # scene    : 这一段在 timeline 里的字典（id / kind / scene_type / sub / big / items / stats ...）
    # t_local  : 段内时间（秒，0..dur）——入场/持续动效都按它在段内推进
    # dur      : 这一段的总时长（秒）
    # t_global : 全片时间（秒）——跨段的连续动效（镜头缓推、呼吸）用它
    # script   : 整份剧本/计划字典（plan、主题色、words 等；有缓存，别原地改）
    # frame    : 绝对帧号（整数）——需要确定性噪声/相位时用它，别用 time()
    return img   # PIL.Image，必须 1920x1080 RGB（用 K.new_frame() 起手最稳）
```

> `--scenes` 不写时的定位顺序：**① 剧本同目录的 `scenes.py` → ② 技能内置 `assets/scenes.py`**。
> 想改画面才在剧本同目录放自己的 `scenes.py`（实现 `draw_frame` 与可选 `cues()`），会被自动发现。
> 写自定义 `scenes.py` 时先抄 `<SKILL_DIR>/assets/scenes.py` 的一个绘制器（`_draw_hero` 之类），它已绕过纯函数、缓存排版、预烘焙底版这些坑。

### 分阶段跑（每个 stage 只做自己的事，落盘后可单独重跑）

```bash
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage style   # 解析已确认的 design，打印主题/视觉语言/版式轮换/字幕样式
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage tts      # 合成旁白 + 实测时长（带输入校验的缓存），并顺带排一次轴
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage layout   # 只排时间轴 + 选构图，落 timeline.json / sub.srt / storyboard.json
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage cues     # 只出提示点 -> work/cues.json
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage score    # 只出配乐 -> work/music.wav（musicgen 现场编排）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage sfx      # 只出音效轨（sfxkit，按 cues.json）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage audio    # 只混音（旁白+配乐+音效，旁白优先）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage frames   # 只渲染帧（--engine pil|web，--jobs N 分块并行）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage mux      # 只合成（视频流 copy，两遍 loudnorm）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage qa       # 只质检（只读输入，写 work/qa/；另写 out/QC-REPORT.md 与 work/gates.json）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage stills   # 只渲母版静帧 + 联系表
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage accept --gate stills   # 人工签核一道闸（写 work/signoff.json）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage explain  # 只报"哪些阶段过时、为什么、该跑什么"，不跑任何 stage
```

`--stage all` 的完整顺序：`style → tts → layout → cues → score → sfx → audio → frames → mux → qa`。**任何一步出问题都会在该步停下**，不会带着坏输入继续往下跑。

**开渲前先算成本**（`estimate.py` 是仓库工具，按 `<REPO>` 调用；`--json` 可机读）：

```bash
python <REPO>/tools/estimate.py --script film.json --jobs 4
```

要省掉全片 VMAF 就在 `film.json` 写 `"qa": {"vmaf": {"mode": "off"}}`（estimate 与 render 都读这个字段，G11 记 skipped）。

**看交付凭据再签核**：`cat out/QC-REPORT.md`（gate matrix 分 pass / fail / skipped 三档，skipped = 本次没执行，不等于通过），
然后 `--stage accept --gate release`。签核绑内容指纹：改了剧本 / `scenes.py` / 设计，旧签核自动失效。交付前要强制（skipped 也算失败）加 `--strict-gates`。

**长片渲染**（分块并行 + 断点续跑）：`--stage frames --jobs 4`，断点续跑再加 `--resume`。分块落在 `work/segments/`（按绝对起始帧命名，
拼接前显式检测缺口）。**改台词 / 改 scenes.py / 改设计之后 `--resume` 会拒绝复用旧分块**（计划指纹对不上），并提示 `rm -rf work/segments`——旧分块配新时间轴会拼出"画面对不上旁白"的片子。

### 标准八阶段（P0–P7）+ 前置 P0.5（速查）

> 详细规格（每阶段的输入/输出/契约 JSON）见 [references/pipeline.md](references/pipeline.md)。

| 阶段 | 产出 | 关键动作 | 常见错误 |
|---|---|---|---|
| **P0.5 定设计** | `work/design.json` + `work/style.json` | `propose.py` 出方案 → `--confirm` 冻结；`--stage style` 只解析已确认的设计 | 跳过 → 没有 design 就没有外观；不确认就渲染会打提案警告 |
| **P0 定基准** | `CONVENTIONS.md` | 分辨率/帧率/时长/BPM→帧换算一次算死 | 各文件自己写 `150` 这种魔数 → 幕边界对不齐 |
| **P1 写剧本** | `film.json` | 每句拆 `text`（朗读）与 `sub`（字幕）；每段一个 `kind` | 一个字段两头用；**别写 `style`**（外观由 design 决定） |
| **P2 事实核查** | `FACTS.md` | 上屏的每个数字/年份有出处编号 | 跳过 → 画面和旁白数字打架 |
| **P3 TTS** | `work/tts/*.mp3` + `tts.json` | edge-tts 逐句合成，`ffmpeg -f f32le` 实测时长，磁盘缓存 | 按字数估时长 → 字幕漂移 |
| **P4 排时间轴** | `work/timeline.json` + `sub.srt` | `dur = lead + 实测语音 + tail`，句读必须在本段内 | 段太短 → 旁白被下一段截断 |
| **P5 混音** | `work/audio.wav` | numpy 总线：旁白 + 配乐 + 音效按采样点叠加 | ffmpeg 多输入混流 → 非确定性静音 |
| **P6 母版** | 3 张静帧 | 先渲 3 帧看构图/配色/字重，定了再渲全片 | 直接渲全片 → 36000 帧后发现字体丑 |
| **P7 成片** | `out/film.mp4` | rawvideo→libx264 CRF18 + 两遍 loudnorm 到 -16 LUFS | 码率过低/响度 -25 LUFS → "像草稿" |

### 质检门禁（error 必过 / warning 别当看不见）

```bash
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage qa     # 只读质检：分辨率/音轨/时长/响度/真峰/锐度比/黑帧/字幕条数 + 帧级深检
python <SKILL_DIR>/scripts/qa_frames.py out/film.mp4 --cues work/cues.json       # 单独跑帧级深检，出 JSON + 图
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage qa --strict-gates   # 交付前：skipped 也算失败
```

- **三档语义**：`pass` = 真的查了且通过；`fail` = 查了没过（非 0 退出）；`skipped` = 本次没执行（默认 0，`--strict-gates` 下非 0）。**skipped ≠ 通过**。
- **error 档**（出现即 red）：分辨率/帧率不符交付规格、音轨缺失、时长差 ≥0.5 s、真峰 > -1.0 dBTP、锐度比 < 0.85、单帧闪烁 >0、画面空窗、音画同步超差、字幕条数 ≠ 剧本句数、G0 渲前预检 error、G10 词级异常、G11 VMAF<92、G12 字符召回<0.72。
- **warning 档**：采样率非 48 kHz、响度偏离 -16±1、真峰余量不足、锐度比 0.85–0.92、长黑帧/长静音、字幕安全区告警、缺字形、画面静止 >6 s、**门禁没执行**（最重要——它说明"看起来通过"其实没查）。
- **音画同步门禁在 PIL 路线默认可能没在查**：要么给 cue 写 `visual_at`（画面上事件真正发生的绝对秒数），要么设 `QA_REQUIRE_SYNC=1` 把"没查"升级成 error。看到 `sync: []` 先确认是真同步还是根本没查。
- **门禁矩阵、G0/G10/G11/G12/G13 判据、交付凭据 `out/QC-REPORT.md`、阶段新鲜度**：见 [references/delivery-gates.md](references/delivery-gates.md)；
  帧级深检怎么读 `work/qa/deep.json`、闪烁/空窗/同步的判定与反例：见 [references/frame-qa.md](references/frame-qa.md)；方法论 G0–G8 与可复制代码见 [references/quality-gates.md](references/quality-gates.md)。

### 交付话术（不许自我宣称"做好了"）

交付时必须能贴出：`ffprobe` 的时长/分辨率/码率；`ebur128` 的积分响度**与真峰**（两个数）；编码锐度比（≥0.85，并说明参考帧来自场景函数重渲）；
联系表路径 + "已用 read_image 亲眼确认"的**具体观察**；字幕条目数 = 剧本句数；`selfcheck.py` 的 PASS 计数；
帧级深检三个数（单帧闪烁 / 画面空窗 / 音画同步超差）；新门禁 G10/G11/G12 的实测值，`skipped` 项必须逐项说明为什么没跑。

> 不要口头复述数字：`out/QC-REPORT.md` 是机器生成的交付凭据，照抄报告里的"实测值"。完整示例与口径见 [references/quality-gates.md](references/quality-gates.md)。
> 自查口令：如果一条门禁从来没失败过，先确认它是不是根本测不到。

## 目录（何时读哪个 reference）

> 读法：先按上文的铁律与五步上手干活；下表只在"要决定什么 / 卡住了 / 要改这一块"时按需打开。**不要一次全读。**

| 什么时候读 | reference | 里面有什么 |
|---|---|---|
| 有素材要剪（footage 线）/ 分诊拿不准 / 素材太少或下载失败 | [footage.md](references/footage.md) | 分诊规则、`footage.json` 契约、10 个 stage、素材定位（转写/关键词/GBK 坑）、画面治理四类配方、F1–F9 门禁与签核、交付清单 |
| 素材 + 图文混合（hybrid）/ 要不要给素材调色 / 叠加占比 | [hybrid-overlay.md](references/hybrid-overlay.md) | 三种形态怎么选（默认形态 3）、overlay 契约、叠加预算四条判据、compose 链路、matchstyle 适用边界 |
| 出提案 / 换方向 / 手改 design 后重绑 / 候选怎么选 | [proposal.md](references/proposal.md) | 提案优先的由来、`design.json` 六块契约、候选采样与设计距离、`--adopt`、提案闸五档、片间去重台账、时长下限口径 |
| 要定外观（配色/字体/母题/转场/字幕/版式轮换）/ 改 design 字段 | [appearance.md](references/appearance.md) | 35 字段设计 DSL、连续色相配色、母题/转场/字幕/版式轮换写法、三条硬约束、常见误用 |
| 场景类型怎么选 / 改 `scenes.py` / 要不要 Web 引擎画页面 | [scene-grammar.md](references/scene-grammar.md) | 10 类构图与自动选型、`storyboard.json`、设计五组视觉签名、加第 11 类构图的步骤、常见误用 |
| 写自己的渲染器 / 构图规则 / 超采样 / Canvas 方案 | [scene-authoring.md](references/scene-authoring.md) | 场景函数写法、确定性铁律、三层性能分离、构图与排版细节、超采样、字幕两条路线 |
| 八阶段细节 / 契约 JSON / 目录约定 / 并行分工 / 画幅·批量·预览·run-id | [pipeline.md](references/pipeline.md) | 每阶段的输入输出与不变式、`timeline.json`/`storyboard.json` 契约、批量生产、预览片、运行版本化 |
| 声音怎么设计 / 混音链 / 提示点 / 配乐参数 / 音效配方 | [audio.md](references/audio.md) | 三层声音、采样级混音、响度链路、提示点系统、musicgen 编排、sfxkit 配方、混音自检与常见故障 |
| 两部片声音太像 / 片内声音打架 / 改音频代码后验收 | [audio-variation.md](references/audio-variation.md) | 内容指纹怎么进和声/进行/动机/鼓型、`sfx.content_seed`、验收阈值（`audio_similarity.py --strict`） |
| 要设计音效（配方表/听感诊断/物理模型） | [sfx-design.md](references/sfx-design.md) | 物理拆解流程、24 配方与语义别名、API 契约、性能预算、QC 规则 |
| 要上浏览器引擎（Canvas2D/WebGL、逐帧截图） | [web-engine.md](references/web-engine.md) | 页面契约（`window.ready/DURATION/renderFrame/getCues`）、确定性时间轴、分块渲染、崩溃重开、文字质检 |
| 帧级质检报错但看不懂 / 怀疑闪烁或同步 | [frame-qa.md](references/frame-qa.md) | 单帧闪烁判据（含"回到原位"第三道闸）、画面空窗、音画同步、`deep.json` 怎么读、诊断 PNG、已知限制 |
| 要查某道门禁的判据来源 / 阈值 / 关闭方式 | [delivery-gates.md](references/delivery-gates.md) | G0 渲前预检、G11 VMAF/SSIM、G12 ASR 回读的判据与实测数据 |
| 要跑方法论门禁 / 交付清单 / 锐度比怎么算 | [quality-gates.md](references/quality-gates.md) | 方法论 G0–G8 全部门禁、可复制代码、编码质量实测依据、三层质检交付话术 |
| 字幕要按词折行 / cue 打到某个词 / 词级时间戳异常 | [word-timeline.md](references/word-timeline.md) | 词级契约、句时长为何仍是权威、词锚点写法与退化路径、四个真实坑 |
| 改了某个字段，不知道要重跑什么 | [change-impact.md](references/change-impact.md) | 改动影响表（text/sub/hold/design/scenes 各要重跑什么）、`--stage explain` 与 stagewatch |
| 遇到怪问题（抖动/音轨断/字幕丢/越跑越慢） | [pitfalls.md](references/pitfalls.md) | 20 条最佳实践 + 17 条反模式 + 5 个 bug 复盘、§8 故障速查表 |
| 要确认"这台机器能不能跑"/ 换机 / 排期估算 | [machine-facts.md](references/machine-facts.md) | 参考机器实测基准、换机 60 秒自检、各模块耗时、排期公式、三条隐形坑 |
| 想查某个脚本的完整参数与坑位 | [script-index.md](references/script-index.md) | 每个脚本的作用、参数、契约（`§10 脚本资产` 的详细版） |
| 竖屏（9:16）怎么出 / 亚像素推镜为什么不采用 | [portrait-and-motion.md](references/portrait-and-motion.md) | 竖屏现状与版式骨架、动感评估结论与验收判据 |

**脚本清单（速查）**：出片 `scripts/render.py`（14 个 stage）/ 提案 `scripts/propose.py` / 问诊 `scripts/intake.py` /
素材线 `scripts/footage.py` / hybrid 合成 `scripts/compose.py` / 风格对齐 `scripts/matchstyle.py` / 评候选 `scripts/critic.py` /
记不住入口时 `scripts/studio.py <子命令>`（薄门面）。完整表见 [references/script-index.md](references/script-index.md)。

```bash
# 换机先跑这一套（比读文档快；FAIL 就照提示修环境，可选模块缺失是 SKIP 不是 FAIL）
cd <SKILL_DIR>/scripts && python selfcheck.py
python <REPO>/tools/footage_selftest.py     # 素材线全链（F1–F9）
python <REPO>/tools/ci_check.py -v          # 仓库级体检（18 项，不需要 ffmpeg/字体/网络）
```

## 素材线（footage）五步上手

> 有素材（本地文件 / 链接 / 网上下载）就走这条线。契约是 `footage.json`（不是 `film.json`），完整字段与 stage 语义见 [references/footage.md](references/footage.md)。

```bash
# 1) 写 footage.json：标题 + 每句旁白 + 画面来源（path 本地 / url 远程，--stage fetch 负责下载）+ 每句用第几秒
cat > footage.json <<'JSON'
{
  "title": "我的素材片",
  "lines": [
    {"id": "l01", "text": "这是素材剪辑线。", "sub": "素材剪辑", "shot": {"at": "00:00:03.500"}}
  ],
  "sources": [{"id": "s1", "path": "/path/to/clip1.mp4", "kind": "video"}]   # path 填你自己的素材绝对路径（或改 url + --stage fetch）
}
JSON

# 2) 一条链：probe -> tts -> edl -> cut -> mix -> assemble -> qa（素材不在本地时先 --stage fetch）
python <SKILL_DIR>/scripts/footage.py footage.json --work work --out out/footage.mp4 --stage all

# 3) 素材定位（可选，先转写再按关键词找镜头，把候选时间码填回 lines[].shot.at）
python <SKILL_DIR>/scripts/footage.py footage.json --work work --stage transcribe
python <SKILL_DIR>/scripts/footage.py footage.json --work work --stage locate --keywords "关键词1" "关键词2"

# 4) 需要图文叠加时（hybrid 形态 3：素材打底 + 小面积叠加），写 overlay.json 再合成
python <SKILL_DIR>/scripts/compose.py --work work --overlay overlay.json --out out/final.mp4 --stage all
```

- 素材预算（F8，按片长反算独立来源数）：推荐 **3 个来源/分钟**、硬下限 **2 个/分钟**、绝对下限 3 个；低于硬下限 `--stage edl` 直接停。
  `"licensing": {"allow_sparse_sources": true, "why": "..."}` 或 `--allow-sparse-sources` 是显式逃生口（F8 仍记 fail 的旁路说明）。
- **不要给素材叠整帧主题色**：素材段保持原色（只有 crop/scale/遮幅 + 显式 `treatment.color`）。想让两段像一部片，方向反过来——把**图文段**配色锚到素材上（形态 1 才需要，默认形态 3 不需要）。

## 引擎与声音的速查（细节都在 references）

| 场景 | 选型 | 速度（本机实测口径） |
|---|---|---|
| 图文/数据/科普/UI 风（绝大多数） | **PIL**（`engine: pil`，默认） | 40–80 fps @1080p30 |
| 流畅矢量/粒子/3D/纸纹质感 | 无头浏览器 Canvas2D/WebGL（`--engine web`） | 8–12 fps（逐帧截图，慢一个数量级） |
| 纯数据动画 | manim（若已安装） | 视场景而定 |

- **浏览器引擎三条硬约束**（自己写页面也要守）：所有补间在一条**暂停的**主时间轴上、渲染第 f 帧 = seek 到 `f/FPS`；循环/粒子用"按时间直算"的更新器，不许 `setInterval` 累加；页面暴露 `window.ready / DURATION / renderFrame(f) / getCues()`，**不连 CDN、不请求网络**。
- **声音三层**：旁白（`work/tts/`，唯一时长权威）→ 配乐（`musicgen` 现场编排，`work/music.wav`）→ 音效（`sfxkit` 按 `cues.json` 合成，`work/sfx.wav`）。三轨在 `--stage audio` 里 numpy 采样级合成，**旁白优先**（配乐 -12 dB 基线、人声处再压 6 dB），最后两遍 loudnorm。
- **片间差异靠内容指纹**：指纹进和声/进行/动机/鼓型（不是只进 seed），验收用 `python <REPO>/tools/audio_similarity.py --strict`。
- **画质清单**（决定"像不像专业片"）：暗底 + 亮 accent、大字 84–96px、字幕 ≤2 行且 `y ≥ 872`、每 3–6 秒要有视觉变化、只做 3–15% 缓推、整片 ≤4 个色相、母带 -16 LUFS / 真峰 ≤ -1 dBTP。详见 [references/quality-gates.md](references/quality-gates.md) 与 [references/appearance.md](references/appearance.md)。

## 交付生产线：画幅 / 批量 / 预览 / 运行版本化

```bash
# 画幅与帧率：film.json 写 "video": {"width": 1280, "height": 720, "fps": 24}；不写 = 1920x1080@30
python <SKILL_DIR>/scripts/render.py --script film.json --work work --layout portrait --out out/portrait.mp4 --stage all   # 竖屏 9:16（experimental）
python <SKILL_DIR>/scripts/render.py --batch films.json --jobs 4                                                            # 批量（≤100 条，开跑前全量预检）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --preview 15 --out out/preview.mp4                      # 预览片（只渲前 15 秒，仅 PIL）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --run-id hero-v3 --out out/film.mp4 --stage all          # 运行版本化（work/runs/<runId>/）
```

- 允许的输出尺寸：横屏 1920x1080 / 1280x720 / 960x540；竖屏 1080x1920 / 720x1280 / 540x960；fps 24/25/30/50/60。**上采样直接报错**（把 1080p 包装成"更高分辨率"等于假高清）；非 9:16 的竖屏输出也直接报错。
- `--batch`：任一条预检失败就一条都不跑；单条失败不阻断其余；收尾写 `batch-report.json`，部分失败退出码 1。
- `--preview N`：秒数下限是一帧（0/负数/nan/inf 报错）；只支持 PIL 引擎；与单 stage 互斥。

## 常见故障速查（前 8 条；完整 22 条见 references/pitfalls.md）

| 现象 | 根因 | 修法 |
|---|---|---|
| 后半段全程静音 | ffmpeg 多输入混流竞争 | 改 numpy 总线 `bus[i:i+n] += src*gain` |
| 字幕越出画面被裁 | 字幕带贴底 | `SUB_BAND` 上移到 872，`base_y` 夹紧 |
| 文字发虚 | cv2 中间文件二次压缩 | 改 rawvideo 直通 + CRF18，用锐度比验证 |
| 音画不同步 | 按字数估时长 | 改实测时长驱动 |
| 拼出的片子缺一段还不报错 | 分块按"第几块"命名 | 按绝对起始帧命名 + 显式缺口检测 |
| 改了台词但配音没变 | TTS 缓存只判"文件存在" | text/voice/rate/pitch 哈希进 `*.mp3.json`；旧缓存 `--force-tts` 一次 |
| 成片音轨变成 96kHz | mux 没指定 `-ar` | mux 显式 `-ar 48000`（selfcheck 第 12 条守着） |
| 画面里 ✓ × ↵ 显示成方框 | msyhbd 缺这些字形 | 用矢量画，或先跑 `K.check_glyphs` |

## 环境与换机

- **插件运行**：Node.js ≥ 20。**必需**：ffmpeg（含 libx264/aac/loudnorm）+ Python 的 numpy / Pillow / edge-tts。
  **可选**：cv2（锐度比）、scipy（音效滤波加速）、playwright + Chromium/Edge（`--engine web`）、faster-whisper（ASR 回读 G12）。G11 要 ffmpeg 带 libvmaf。
- 依赖清单只有一份：`pip install -r skills/video-studio/requirements.txt`；ffmpeg 路径可用环境变量 `FILMKIT_FFMPEG` 覆盖。
- `SKILL.md` 不再写参考机器的实测数字（那批基准已搬到 [references/machine-facts.md](references/machine-facts.md)）；**换机先跑 §13 的 60 秒自检**：`cd <SKILL_DIR>/scripts && python selfcheck.py`。

## 设计 DSL 与词级时间轴的速查

- **`work/design.json` 是外观的唯一来源**：没有内置风格表，`film.json` 写 `style` 不生效。设计由 `propose.py` 现场采样、用户 `--confirm` 冻结；
  手改后必须 `python <SKILL_DIR>/scripts/propose.py --adopt --work work` 重绑指纹（只改设计指纹，不动候选），否则提案闸会判 stale。
- **六块**：`structure`（段数/时长）、`visual`（配色/字体/版式/动效/装饰/字幕）、`audio`（配乐/声线/音效）、`pacing`（lead/tail/min_dur/hold/item_stagger）、`id/label`、`pitch`（一句中文立意）。
  字段表与三条硬约束（不跨明暗族、必须过 `theme_lint` 对比度、默认单声线）见 [references/appearance.md](references/appearance.md)。
- **无声线/无风格的统一规则**：`design.json` 里 `visual.subtitle` 与 `visual.layout_cycle` 都是可选项，不写=补默认值；版式轮换至少 2 种不同版式，否则 `validate_design` 直接报错。
- **词级时间轴**：`film.json` 顶层 `"words": true`（默认）。`work/words.json` 提供字幕按词折行与 cue 词锚点（`"at": "l03:w2+0.1"`，从 1 数）；
  句时长仍是唯一权威，**不要用词时长之和反推句子时长**。词锚点写错只 WARN 后退回句偏移——改台词后先看 stdout 有没有这条 WARN。契约与四个坑见 [references/word-timeline.md](references/word-timeline.md)。
- **停留窗口**：`hold` 只加在语音结束后（不影响旁白节奏），加长量取 0.5 s（120 BPM 一拍）的整数倍；`bar` 可让段长对齐小节（代价是段长不再精确等于语音+停留）。

## 插件配置（apply(ctx, config)，可选）

插件支持三个配置项，加载时以追加块注入本文末尾（不改 SKILL.md 源文件；不传配置时行为与未配置版本完全一致）：

| 配置 | 取值 | 效果 |
|---|---|---|
| `mode` | `auto`（默认）/ `footage` / `draw` | 固定产线：footage 只走素材线、draw 只走零素材线、auto 按问诊第 2 题落地 |
| `optionalModules` | `sfxkit` / `scorekit` / `webengine` / `qa_frames` | 声明本会话可用的可选模块；没声明的按环境探测（缺失时门禁记 skipped） |
| `strictGates` | `true` / `false`（默认） | true 时交付前用 `--strict-gates`（skipped 也算失败）并加 `--require-proposal` |

> 配置写错（未知 mode / 未知 module / 类型不对 / 未知键）**加载期直接报错**，不会静默忽略。配置面与诊断入口的契约见 README「兼容性」与 `npm run diagnose`。
