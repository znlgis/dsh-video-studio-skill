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
7. **（v0.3）声音跟着画面走**：该响一下的地方在**场景代码里登记提示点**（`K.CueBus` / `scenes.py` 的 `cues()` 钩子），
   不要事后手工对时间。画面改了（换停留窗口、调节奏），提示点自动跟着改。
8. **（v0.3）没看清就加停留，别整片放慢**：`film.json` 的 `hold`（全局）或 `lines[].hold`（单句）
   在语音结束后加停留；配乐用 `bpm` 对齐小节。

## 1. 五步上手（最快路径）

> 下文的 `<SKILL_DIR>` 指**本次加载技能时 `skill` 工具回报的 `resourceBase.path`**。装成插件后它就是
> 本插件包内的 `skills/video-studio/`（profile 的 `node_modules/@dsh-gis/dsh-video-studio-skill` 是链接）。
> 找不到时按名字定位：`find ~ -path '*/skills/video-studio/scripts/filmkit.py'`。
>
> `<REPO>` 指**包根**（仓库根，或装好后 profile 里的 `node_modules/@dsh-gis/dsh-video-studio-skill/`），
> 即 `<SKILL_DIR>` 往上两级。**`tools/` 与 `schemas/` 在 `<REPO>` 下，不在 `<SKILL_DIR>` 下**：
> 本节的 `cp <SKILL_DIR>/scripts/...` 把技能拷成 `myfilm/` 之后，那个工作目录里**没有** `tools/`，
> 所以 `tools/estimate.py` 这类仓库工具要按 `<REPO>/tools/...` 调用（或从包/仓库里取一份过来）。

```bash
# 1) 建工作目录，拷模板
mkdir -p myfilm && cd myfilm
cp <SKILL_DIR>/assets/film.json .            # 剧本模板（改文字即可）
cp <SKILL_DIR>/assets/scenes.py .            # 场景模板（改画面即可）
#    v0.6.0 起 gates.py（门禁账本）与 stagewatch.py（阶段新鲜度）是**必需**的：
#    render.py 直接 import 它们，少一个就是 ModuleNotFoundError。
cp <SKILL_DIR>/scripts/{filmkit.py,stylekit.py,render.py,selfcheck.py,gates.py,stagewatch.py} .
# 可选（v0.3）：要程序化音效/配乐就一并拷；要走浏览器渲染再拷 webengine.py + assets/web/
cp <SKILL_DIR>/scripts/{sfxkit.py,scorekit.py,voicekit.py,qa_frames.py} .
cp -r <SKILL_DIR>/assets/web ./web

# 2) 先跑一次自检：43 条回归护栏，20 秒内出结论（不联网；可选模块缺失会标 SKIP）
python selfcheck.py

# 3) 【v0.5 新步骤】先确认风格，再改文字
#    film.json 的 "style": "auto" 会按内容自动选一套（画风+配乐+旁白一起变）；
#    想锁定就写死风格 id。这条命令打印"为什么选这套"，并落盘 work/style.json。
python render.py --script film.json --stage style

# 3b) 改 film.json 的 lines[].text / sub / big / items
#    text = 朗读文本（自然口语）；sub = 上屏字幕（短，一行优先）

# 4) 母版预览（P6）：先只渲几张关键帧看构图/配色，别一上来渲全片
#    全新目录下的第一条命令也能跑：缺 tts.json / timeline.json 会自动补跑
#    （tts 要联网合成一次旁白），并打印 [stills] 说明自己在补哪一步。
python render.py --script film.json --scenes scenes.py --work work --stage stills \
       --stills "l01:3,l02:5,l03:2.5"

# 4.5) 【v0.6】先算成本再开渲：帧数 / 各阶段耗时 / 磁盘 / 分块建议
#      项目原则是"成本敏感"，而在此之前开渲前拿不到任何数字。
#      （--json 可机读；有 work/timeline.json 时用真实时长，否则按字数粗估并标注）
# 注意：estimate.py 是**仓库/包里的工具**（<REPO>/tools/），不在 skills/video-studio/ 里，
#      quickstart 拷出来的 myfilm/ 也不会有它；想用就按包根路径调用，或从包/仓库里复制过来。
python <REPO>/tools/estimate.py --script film.json --jobs 4

# 5) 一条命令出片（TTS→排版→混音→渲染→合成→质检）
python render.py --script film.json --scenes scenes.py --out out/film.mp4 --work work

# 6) 亲眼看质检抽出的帧
#    work/qa/sheet.png      联系表（全片调性）
#    work/qa/snap_*.png     全分辨率静帧

# 7) 【v0.6 新步骤】看机器可读的交付结论，然后签核
#    out/QC-REPORT.md 是**交付凭据**：gate matrix 分 pass / fail / skipped 三档，
#    skipped 表示"本次没执行"，不等于通过。以前这些只活在 stdout 里，会话一断就没了。
cat out/QC-REPORT.md
python render.py --script film.json --work work --stage accept --gate stills    # 看过静帧了
python render.py --script film.json --work work --stage accept --gate release   # 成片验收过了
# 签核绑内容指纹：改了剧本 / scenes.py / 引擎，旧签核自动失效，要重新看、重新签。
# 交付前想强制：加 --strict-gates（skipped 也算失败）。
```

分阶段跑（**每个 stage 只做自己的事**，不会再顺手重渲全片）：

```bash
python render.py ... --stage style    # 只解析风格 -> work/style.json（画风/配乐/旁白/节奏一次定死）
python render.py ... --stage tts      # 只合成旁白 + 实测时长（带输入校验的缓存）
python render.py ... --stage layout   # 只排时间轴 -> timeline.json / sub.srt / sub.ass（含 hold 停留窗口）
python render.py ... --stage cues     # 只出提示点 -> work/cues.json（声音跟着画面走）
python render.py ... --stage score    # 只出配乐 -> work/music.wav（scorekit）
python render.py ... --stage sfx      # 只出音效轨 -> work/sfx.wav（sfxkit，按 cues.json）
python render.py ... --stage audio    # 只混音 -> audio.wav（旁白+配乐+音效，音效响时配乐让位）
python render.py ... --stage frames   # 只渲染帧 -> silent.mp4（--engine pil|web，--jobs N 分块并行）
python render.py ... --stage mux      # 只合成（视频流 copy，两遍 loudnorm）
python render.py ... --stage qa       # 只质检（只读输入，只写 work/qa/）+ 帧级深检
python render.py ... --stage stills   # 只渲母版静帧 + 联系表
python render.py ... --stage accept --gate stills   # 【v0.6】人工签核一道闸（写 work/signoff.json）
python render.py ... --stage explain  # 【v0.6】只报"哪些阶段过时了、为什么、该跑什么"，不跑任何 stage
```

`--stage all` 的完整顺序：`style → tts → layout → cues → score → sfx → audio → frames → mux → qa`。
**任何一步出问题都会在该步停下**，不会带着坏输入继续往下跑。

长片渲染（分块并行 + 断点续跑）：

```bash
python render.py ... --stage frames --jobs 4        # 4 进程按绝对帧号分块
python render.py ... --stage frames --jobs 4 --resume   # 中断后只补缺块
```

> 分块落在 `work/segments/`，按 `seg_<起始帧>_<结束帧>.mp4` 命名；拼接前会**显式检测缺口**，
> 缺块直接报错，不允许"少一段也能出片"。默认 chunk 按 `总帧数 / jobs / 4` 向整秒取整（jobs=4、
> 900 帧时约 8 秒一段），可用 `film.json` 的 `chunk_frames` 覆盖。
>
> **改台词 / 改 scenes.py / 改编码参数之后，`--resume` 会拒绝复用旧分块**（`work/segments/_plan.json`
> 里存着计划指纹），并提示你 `rm -rf work/segments`——这是刻意的：旧分块配新时间轴会拼出
> "画面和旁白对不上"的片子，而且时长门禁查不出来。

> **v0.6.0 修了一个已发布版本里的真 bug**：`--jobs >1` 时子进程从 `_script_snapshot.json` 读剧本，
> JSON 把主题色的 `(r,g,b)` 元组变成 `list`，而 `scenes._bg()` 拿它当缓存键 → `TypeError: unhashable type: "list"`。
> 也就是说 **"长片并行渲染"这条 SKILL 自己教的路径此前是崩的**。现在子进程会按剧本重解风格（拿回元组），
> `scenes.py` 的 `_bg` 键也做了冻结兜底；`selfcheck` 第 32 条专门盯这个。

## 2. 标准八阶段（P0–P7）

| 阶段 | 产出 | 关键动作 | 常见错误 |
|---|---|---|---|
| **P0.5 定风格** | `work/style.json` | `--stage style`：按内容选风格包，打印"为什么选这套" | 跳过 → 画风/配乐/旁白全片一个样（v0.4 的通病） |
| **P0 定基准** | `CONVENTIONS.md` | 分辨率/帧率/时长/BPM→帧换算一次算死，全项目引用同一份 | 各文件自己写 `150` 这种魔数 → 幕边界对不齐 |
| **P1 写剧本** | `film.json` | 每句拆 `text`（朗读）与 `sub`（字幕）；每段一个 `kind`；定 `style`（auto 或风格 id） | 一个字段两头用 → 字幕太长或读起来生硬；不写 style → 每部片长一个样 |
| **P2 事实核查** | `FACTS.md` | 上屏的每个数字/年份有出处编号；建"不能上屏"黑名单 | 跳过这步 → 画面和旁白数字打架，评论区第一条就是你错了 |
| **P3 TTS** | `work/tts/*.mp3` + `tts.json` | edge-tts 逐句合成，`ffmpeg -f f32le` 实测时长，磁盘缓存 | 按字数估时长 → 字幕漂移 |
| **P4 排时间轴** | `work/timeline.json` + `sub.srt` | `dur = lead + 实测语音 + tail`，句读必须在本段内 | 段太短 → 旁白被下一段截断 |
| **P5 混音** | `work/audio.wav` | numpy 总线：旁白 + 配乐 + 音效按采样点叠加 | ffmpeg 多输入混流 → 非确定性静音 |
| **P6 母版** | 3 张静帧 | 先渲 3 帧看构图/配色/字重，定了再渲全片 | 直接渲全片 → 36000 帧后发现字体丑 |
| **P7 成片** | `out/film.mp4` | rawvideo→libx264 CRF18 + 两遍 loudnorm 到 -16 LUFS | 码率过低/响度 -25 LUFS → "像草稿" |

> 注：`filmkit` / `render.py` 的默认是 `crf=18`，但**自带模板 `assets/film.json` 写的是 17**。
> 自己写剧本时不写 `crf` 就是 18；追求更锐利的文字可以写 17（体积约 +30%）。

## 3. 渲染引擎怎么选

| 场景 | 选型 | 实测速度 | 适合 |
|---|---|---|---|
| 图文/数据/科普/UI 风 | **PIL（numpy+cv2 辅助）** — `filmkit` 默认 | 40–80 fps @1080p30（模板片 75） | 绝大多数中文科普片、报告转视频 |
| 需要流畅矢量/粒子/3D/纸纹质感 | **无头浏览器 Canvas2D/WebGL + CDP 截图 → image2pipe**（`webengine.py`） | 80–130 ms/帧（核显，PNG 截图） | 纪录片级长片、需要复杂视觉语言 |

> 上面两行是**本机空载**实测口径（换机、重负载画面都会变；详见 §12）。别把区间当保证。
| 纯数据动画 | **manim**（若已安装） | 视场景而定 | 数学/公式推导 |

> **v0.5 注意**：风格包的主题化目前只覆盖 **PIL 引擎**（`engine: pil`，默认）。
> `--engine web` 的页面（`assets/web/`）仍是 v0.4 的深蓝橙配色，不跟随 `style`；
> 用 web 引擎时画风不会随内容变化，但配乐与旁白**照样**跟随风格包（它们不依赖渲染引擎）。

**默认走 PIL**：依赖少（numpy+Pillow 就够）、启动快、确定性强、调试成本低。
需要"一镜到底的粒子形变 / 实时着色器质感"才上浏览器方案——**v0.3 起它是真的可用了**，不再是"文档里说有"：

```bash
cp -r <SKILL_DIR>/assets/web ./web                     # 页面模板（原生 Canvas2D，零三方库）
pip install playwright && python -m playwright install chromium   # 只在走 web 引擎时需要
python render.py ... --engine web --stage cues        # 从页面导出提示点
python render.py ... --engine web --stage frames --jobs 3 --resume
python webengine.py textqa ./web/index.html            # 文字重叠/出画/一闪而过/从没露面
```

两条路线的取舍（**先想清楚再选，换引擎要重做画面**）：

| | PIL（`engine: pil`） | 浏览器（`engine: web`） |
|---|---|---|
| 依赖 | numpy + Pillow（+cv2 做锐度比） | + playwright + Chromium/Edge |
| 速度 | 40–80 帧/秒 | 8–12 帧/秒（逐帧截图+编码，慢一个数量级） |
| 画面语言 | 位图合成、渐变、几何、文字 | Canvas2D/WebGL：粒子、形变、着色器、矢量动画 |
| 确定性 | 天然（纯函数画图） | 靠"把时间轴精确 seek 到 f/FPS"保证（`window.renderFrame`） |
| 调试 | 改一帧跑一帧 | 浏览器里 `index.html?dev=1&t=秒` 直接看，出了 bug 好查 |
| 适合 | 图文/数据/科普/宣传片 | 纪录片级长片、需要一镜到底的视觉语言 |

**浏览器引擎的三条硬约束**（`webengine.py` 已实现，自己写页面时也要守）：
1. 所有补间放在一条**暂停的**主时间轴上，渲染第 f 帧 = 把它 seek 到 `f / FPS` 秒；**不许依赖播放**。
2. 循环/粒子/波浪这类程序化内容用"按时间直算"的更新器（`update(fn)`），不许用 `setInterval` 累加。
3. 页面必须暴露 `window.ready / DURATION / renderFrame(f) / getCues()`；**不连 CDN、不请求网络**。

要**跨浏览器会话**的字节级可复现（崩溃重开、多次渲染做 diff、质检要比对两张图）时开确定性模式：
`--deterministic` 或环境变量 `WEBENGINE_DETERMINISTIC=1`（关 GPU 光栅，实测 8/8 会话一致，
代价约 +22%/帧）。默认不开：同一会话内本来就一致，跨会话只有 ≤4 像素、±1 级的抗锯齿差异。

## 4. 画质清单（这几项决定"像不像专业片"）

- **亮度与层次**：暗底 (`#0a1420`) + 顶部亮 accent；不要纯黑纯白，用户屏幕会吃掉渐变。
- **字重与字号**：大字 84–96px、副标题 34–38px、字幕 46px；中文用 `msyhbd.ttc`（微软雅黑粗体）。
- **字幕**：≤ 2 行、单行约 24 个中文字、带半透明背板、`y ≥ 872`。
  实际折行看的是**像素宽度**（`fit_sub_lines` 按 `max_w=1560 px` 自动折），24 字是"大致上限"并非硬校验
  ——`SUB_MAX_CHARS=24` 这个常量目前**没有被任何代码引用**，别指望它拦你。
- **节奏**：每 3–6 秒要有视觉变化（新卡片 / 位移 / 数字滚动）；静止超过 6 秒观众就走。
- **镜头**：只做 3–15% 缓推，不要大幅拉远拉近。
- **调色**：整片一个主色 + 一个强调色，别超过 4 个色相。
- **音频**：母带 -16 LUFS / 真峰 ≤ -1 dBTP；旁白比间隙响 12 dB 以上；AAC 前留 1.4 dB 余量。

## 5. 声音：配乐 + 音效 + 提示点（v0.3.0）

> 这一节的思路来自一部真实成片（`ai-history-film`）的复盘：**声音不好不是"混得不够响"，而是"声音不认识画面"**。
> 解决顺序永远是：先让声音知道画面上发生了什么（提示点），再谈音色，最后才是母带。

### 5.1 三层声音

| 层 | 谁做 | 落盘 | 说明 |
|---|---|---|---|
| 旁白 | `filmkit.tts`（edge-tts） | `work/tts/*.mp3` | **唯一的时长权威**：时间轴由它反推 |
| 配乐 | `scorekit.py`（程序化） | `work/music.wav` | 和弦表 + 律动层 + 分段能量；段落边界对齐小节线 |
| 音效 | `sfxkit.py`（物理建模） | `work/sfx.wav` | 按 `cues.json` 里登记的提示点逐条合成 |

三条总线在 `--stage audio` 里合成：**音效响起时配乐自动让位**（`duck_db`，v0.7 默认 4 dB，
cinema/epic 5.5 dB；旧版 3 dB 在段落过门时配乐几乎与音效等高），
最后交给 ffmpeg 两遍 loudnorm 到 -16 LUFS / 真峰 ≤ -1 dBTP。**不要**自己在 numpy 里做最终响度标准化——
你有 `loudnorm`，它比手写归一化准得多，而且真峰才是门禁。

> **v0.7 片内分离**：配乐在段落交界本来就有过门（scorekit 的 swell/riser/sub_boom），
> sfx 轨不再在同一个边界重复放 whoosh/riser——`--stage sfx` 会对落在
> [边界 - 2.2 s, 边界] 内的转场类 cue 自动 -3 dB（`sfx.duck_transitions=false` 可关）。
> 提示音的音高现在跟随配乐调性（`in_key(freq, key)`），不再硬编码 D 小调五声。

### 5.2 提示点：让声音跟着画面走

**这是 v0.3 最重要的一个概念。** 画面在该响一下的地方登记一个提示点，音频阶段按提示点合成音效：

```python
# 方式一：写在 film.json 里（好改，适合少量关键音效）
"sfx": {"cues": [
  {"at": "l03+0.05", "type": "impact", "size": 1.1},
  {"at": "abs:12.5", "type": "whoosh", "dur": 0.9}
]}

# 方式二：写在 scenes.py 的 cues() 钩子里（推荐——画面改了声音自动跟着改）
def cues(tl, script):
    out = []
    for s in tl["scenes"]:
        for i, _it in enumerate(s.get("items") or []):
            out.append({"at": "%s+%.2f" % (s["id"], 0.48 + 0.32 * i), "type": "ui_blip"})
    return out

# 方式三：web 引擎里由页面导出（提示点跟着动画代码走）
S.cue(t, 'impact', {size: 1.1});   // 页面里的时刻是"本幕局部秒"
python render.py ... --engine web --stage cues   # 自动导出 work/cues.json
```

`at` 有三种写法：`"abs:12.5"`（绝对秒）、`12.5`（数字同义）、`"l03+0.35"` / `"l03-0.2"`（段 id ± 偏移）。

**为什么值得多写这一层**：改一句台词、加一个停留窗口、把 BPM 从 120 改成 100，
所有音效仍然贴着画面，不需要重新对时间。纯手工"在 12.5 秒放一声"的方案，改一次节奏就全废。

### 5.3 配乐（scorekit）

> **v0.5 起先看 §5.5。** 配乐的"身份"（调式/调性/BPM/进行/音色/律动）由风格包决定，
> 你通常只需要在 film.json 写 `"style": "auto"` 或一个风格 id，不用手调下面这些参数。

```json
"bpm": "auto",
"music": {"enabled": true, "mode": "score", "style": "auto", "mood": "auto",
          "key": "auto", "seed": 7, "seed_from_content": true,
          "gain_db": -1.5, "duck_db": 3.0,
          "sections": [{"at": "l01", "name": "intro", "energy": 0.3},
                       {"at": "l03", "name": "drive", "energy": 0.8}]}
```

- **一张和弦表贯穿全片**，段落之间只换"色彩与能量"，不换调——这是"听起来像一首曲子"而不是"一堆配乐"的关键。
- `energy` 控制律动层数（pad / bass / kick / hat / arp）与亮度；安静段比律动段低 4–8 dB。
- 段落边界**对齐小节线**（按 `bpm` 算），画面重音才落在拍点上。
- 重击前用 `Score.suck()` 让配乐"吸一口气"（压低 3–8 dB、0.2 s 内恢复），落差立刻出来——
  **不要**用静音断层，那会让声音像断了。
- `"mode": "bed"` 是 v0.2 的和弦床兜底；`"mode": "off"` 不要配乐。

### 5.4 音效（sfxkit）

`sfxkit` 是**物理建模**而不是"画波形"：撞击 = 材质（衰减率）× 形状（模态比）× 敲击位置 × 接触时间；
风 = 噪声 + 时变滤波（参数由慢变控制曲线驱动）；雨/火 = 泊松事件 + 幂律幅度 + 多核散布。

```bash
python sfxkit.py --list                      # 全部配方与参数
python sfxkit.py --render impact --seed 3 --set size=1.2 --out /tmp/i.wav
python -c "import sfxkit; print(sfxkit.render('whoosh', dur=1.2).shape)"
```

常用配方（**cue 的 `type` 就是这些名字**）：

| 用途 | 配方 |
|---|---|
| 界面/提示 | `ui_blip` `ui_click` `relay_click` `roulette_tick` `answer_chime` |
| 文字 | `typing` `typing_at`（逐字对齐，`times` 是相对提示点的秒数组） `text_stream` |
| 转场 | `whoosh` `riser` `impact` `sub_drop` `reverse_swell` `glitch` `tape_rewind` |
| 环境 | `wind` `rain` `campfire` |
| 撞击 | `knock_wood` `metal_hit` `glass_clink` `go_stone` |

**音效设计的三条经验**（都是踩过坑才写下的）：

1. **"叮叮咚咚"是头号污染源**：有音高的小提示音多了就变成八音盒。全片只留几个**关键**的叮
   （开场任务完成、认出猫、片尾），其余换成不带音高的"咔嗒"。
2. **"嗖"要从物理出发**：多普勒 + 1/r 音量钟形 + 越远越闷 + 地面反射的梳状相位。
   合成器式的扫频"咻"听起来像电子音，物理飞过模型才像"有东西飞过去"。
   它的定位是**环境声**：基准电平要比同刻配乐低 10 dB 以上。
3. **音量一律在成片混音里量**，不要按配方内部音量推断——混音时每个音效会先按峰值归一，配方里的"响"是没用的。

### 5.5 风格包：画风 + 配乐 + 旁白一起变（v0.5 新增）

**这是 v0.5 最重要的一节。** v0.4 及以前的通病是"内容换了、气质没换"：
画风锁死在 scenes.py 的深蓝橙、配乐锁死在 Dm/120、旁白锁死在同一个 voice。
用户的原话是「背景音乐风格都差不多、旁白声音都差不多、整体画风也差不多」。

v0.5 引入 **风格包**：一个 `style` id **同时**决定画风、配乐、旁白声线和节奏。

```json
{"style": "auto"}
```

- `"auto"` = 按标题/章节/正文关键词自动选；零命中时按内容指纹稳定落桶
  （同一份稿子永远同一个风格，不同稿子大概率不同）。
- 写死 id 就锁定：`tech-deep` / `magazine` / `paper` / `vivid` / `mono` / `cinema` / `lo-fi` / `epic`。
- **不写 `style` 字段 = `legacy`**：画风/配乐/旁白全部等于 v0.4，配乐逐样本一致（老项目无痛）。

查表与环境：

```bash
python stylekit.py --list          # 8 套风格（含画风/配乐/旁白/节奏与对比度）
python stylekit.py --show cinema   # 单套风格的完整字段
python render.py ... --stage style # 用某份 film.json 解析风格并打印"为什么选这套"
```

| 风格 | 画风 | 配乐 | 旁白 |
|---|---|---|---|
| tech-deep | 深蓝渐变 + 橙 | 小调 100–124 / 琶音 + 铃声 | 男声叙述 + 青年开场 |
| magazine | 米白 + 朱红，衬线 | 大调 88–100 / 电钢 | 女声叙述 + 权威引用 |
| paper | 牛皮纸 + 靛青，楷体 | 大调 84–92 / 马林巴 | 软糯女声 + 男声引用 |
| vivid | 深紫 + 柠檬黄，大字 | 大调 120–132 / 短促和弦 | 活泼女声开场 |
| mono | 纯白 + 黑，超宽字距 | 极简 60–68 / 钢片琴 | 沉稳男声 + 女声引用 |
| cinema | 近黑 + 青橙，扫描线 | 小调 76–84 / 弦乐铺底 | 纪录片男声 |
| lo-fi | 暖灰 + 薄荷 | 大调 72–84 / 电钢 + 轻鼓 | 温柔女声 |
| epic | 近黑 + 金，衬线 | 小调 88–96 / 铜管 + 人声铺 | 有力男声 |

**片内也要有呼吸**（不是整片一个死样子）：

- 版式按章节轮换（同一章内不变）：left-stack / center-stack / split-right；
- 镜头缓推、入场位移、条目落位步长都由主题决定（`item_stagger` 与 `cues()` 共用同一个值，音效不会错位）；
- 旁白按 kind 换语气档（hook/body/quote/outro/emphasis/calm），引号句会自动换声线；
- 配乐逐小节有能量弧线、配器渐入渐出、主奏轮换。

**三条硬约束**（违反会让片子"花"或者被门禁拦）：

1. 同一部片**不许跨明暗族**换主题（深蓝段直接切暖白段会精确触发闪烁门禁，观众也看着像换片）。
2. 主题必须过 `stylekit.theme_lint` 的对比度体检（ink≥7 / muted≥4.5 / dim≥3 / accent≥4.5）。
3. 旁白全片最多 2–3 把声音（`voice.max_distinct`），相邻句不换人——否则像广播剧而不是旁白。

字段、指纹规则、加第 9 套风格的步骤见 **references/style-packs.md**。

### 5.6 混音链（照着做，别自创）

```
旁白(单声道, 居中) ─────────────────────────────┐
配乐(立体声, -12dB 基线) ─> 音效duck(-4dB) ─> 旁白duck(-6dB) ─┤─> 求和 ─> 峰值保护(0.89) ─> loudnorm 两遍 ─> -16 LUFS / TP<=-1
音效(立体声) ───────────────────> 旁白duck(-3dB) ─────────────┘
```

- **旁白优先是硬行为（v0.7.1）**，不是文档口号：`mix_film` 里旁白固定 `voice_gain=1.0` 居中、不被任何侧链影响；
  配乐基线 `music.gain_db=-12`（旧版 -1.5，导致背景与台词几乎等高）；有人声的地方再按旁白包络压
  配乐 6 dB、音效 3 dB（`music.voice_duck_db` / `music.voice_duck_sfx_db`，间隙自动恢复）。
  实测人声段"旁白/背景"响度差 **12.9 dB**（真实配乐素材下；合成信号口径 15 dB），
  selfcheck 第 35/36 条与 `audio_similarity.py --strict` 的 D 组守着这条。
- **SNR 兜底（v0.7.1 最后一道闸）**：混音后按台词活跃区实测"人声/背景"，不足 12 dB 就整体再压背景
  （`music.guard_snr_db`，最多 `guard_cap_db=30`；补压 >18 dB 会提示检查素材电平）。
  这是唯一一条"混音后自己改电平"的门禁，保证换任何配乐素材都不会把旁白盖住。
- **BGM 与音效是可选层**：它们的作用是铺情绪与打重音，不该抢字。想更安静就调低 `music.gain_db`
  或设 `music.enabled=false`；"让观众听清旁白"永远优先于"背景好听"。
- 音效响时配乐自动让位：v0.7 起风格表默认 4.0 dB（mono 3.0 / lo-fi·magazine 3.5 /
  cinema·epic 5.5），`film.json` 的 `music.duck_db` 显式值永远优先。旧版 `mix_film` 默认 3.0 dB，
  在段落过门时配乐几乎与音效等高——这是"配乐和音效听着像同一套"的混音侧成因，已由 `duck_default` 抬高。
  让得太多会"音乐一抖一抖"，所以按风格分级而不是一律压狠。
- **本项目不做母带压缩**：链路是 求和 → duck → 峰值保护 0.89 → `ffmpeg loudnorm` 两遍。
  代码里**没有压缩器**（想加请自行在 `mix_film` 之后接），所以也不存在"压太狠"的问题；
  要让重击前后有能量差，靠的是配乐分段 energy 与 `suck()`，不是压缩。
- 成片音轨必须 48 kHz（`mux` 已显式 `-ar 48000`，selfcheck 第 12 条守着）。

### 5.7 声音的片间差异与片内分离（v0.7 新增）

> v0.5 解决的是"配乐风格都差不多"，但**同一风格下每部片的配乐仍像同一首**——
> 内容指纹只进了 seed（改音色细节），和弦进行/动机/鼓型全固定。
> 实测：同风格、同 bpm、同调、只换内容指纹，两段配乐相关性 **0.966**。

v0.7 把指纹推进到**结构层**（详见 **references/audio-variation.md**）：

| 层 | v0.6 | v0.7 |
|---|---|---|
| 配乐和声 | 风格固定进行 | 指纹决定**每两小节一次的稳定和弦替换**（不影响乐句末与片尾规则） |
| 配乐进行 | 风格固定一套进行 | 指纹在风格自带的 calm/body/drive 三档里选一档（同风格素材，只换功能顺序） |
| 配乐动机 | 风格固定动机 | 指纹对级数做 **±1 稳定偏移**（节奏不变，问句/答句独立派生；跨主音带八度修正） |
| 配乐鼓型 | 固定落点 | 指纹加 ghost kick（不与现有落点重复），clap 位置 ±0.5 拍 |
| 出片接线 | 指纹只进 seed | `render.py` 的 `st_score` 把 `fingerprint.full` 传进 `scorekit.render()`；legacy 不传 |
| 音效 seed | `seed_base + i`（只跟 cue 顺序） | `content_seed(seed_base, style, content_fp, type, t)`（跟内容走） |
| 提示音音高 | 硬编码 D 小调五声 | `in_key(freq, key)` 跟随配乐调性 |
| 段落边界 | 配乐过门 + sfx 转场各响各的 | 边界前 2.2 s 内的 whoosh/riser **自动 -3 dB**（不与过门撞车） |
| duck | 3 dB 一刀切 | 按风格 3.0–5.5 dB，配乐让得更彻底 |
| 旁白优先（v0.7.1） | 配乐 -1.5 dB、无语音侧链 | 配乐 -12 dB 基线 + 人声处再压 6 dB（音效 3 dB），人声段 SNR 实测 15 dB |

**向后兼容**：不写 `style` 字段（legacy）或 `sfx.content_seed: false` 时，音效走旧的
`seed_base + i` 路径，老片逐样本不变（selfcheck 第 33 条守着这条）。

验收工具（改音频相关代码后必须跑）：

```bash
python <REPO>/tools/audio_similarity.py --strict   # A/B/C 三组阈值，失败 exit 1（在包根执行）
```

## 6. 停留窗口：让观众看清楚，而不是整片放慢（v0.3.0）

> 来自同一部成片的复盘："部分画面没看清就被切走了"。**解决办法不是把整片放慢，而是按内容加停留。**

```jsonc
"hold": 0.4,                 // 每句语音结束后统一多停 0.4 s
"lines": [{"id": "l03", "hold": 1.2, ...}]   // 这一句单独停 1.2 s
```

- `hold` 只加在**语音结束后**，不影响旁白节奏；句子读得还是那么快，只是停在画面上的时间变长。
- 加长量取 `0.5 s`（120 BPM 下的**四分音符**＝一拍）的整数倍，画面重音仍落在拍点上。
- 配乐 `sections` 用 `at: "l03"` 而不是绝对秒——停留窗口一改，段落边界自动跟着走。
- `bar` 字段可以让每段时长自动对齐小节（`"bar": 2.0` = 120 BPM 一小节），**代价是段落时长不再精确等于
  语音+停留**，只在对齐更重要时才用。

**判断哪里需要停留**（按顺序看这三条）：
1. 画面上出现了**要读的字**（数字滚完、卡片落定、字幕出齐）→ 至少留 `0.5 s + 字数/9`。
2. 出现了**要理解的结构**（网络层次、坐标轴、流程图）→ 留到能扫一眼看完。
3. 两个镜头之间**没有内容连续性**（黑一下、空一拍）→ 让下一幕提前开始，而不是加长黑场。

## 7. 质检门禁（error 必过 / warning 别当看不见）

```bash
python render.py ... --stage qa     # 只读质检：分辨率/音轨/时长/响度/真峰/锐度比/黑帧/字幕条数 + 帧级深检
python selfcheck.py                 # 技能自身回归（43 条曾真的坏过的点）
python qa_frames.py out/film.mp4 --cues work/cues.json   # 单独跑帧级深检，出 JSON + 图
# v0.6：qa / all 收尾会自动写 out/QC-REPORT.md（机器可读交付结论）
#       并落 work/gates.json（三档 gate matrix）与 work/cost.jsonl（实测耗时台账）
```

### 7.0 交付凭据：`out/QC-REPORT.md` 与人工签核（v0.6.0）

> 这一节解决的是"**质检结论只活在控制台里**"——会话一断，"我查过了"就无从复现。

**三档状态**（这是最关键的语义，不许混）：

| 状态 | 含义 | 退出码 |
|---|---|---|
| `pass` | 本次**真的查了**且通过 | 0 |
| `fail` | 查了没过 | 非 0（除非 `--no-gate` 绕过，且会留痕） |
| `skipped` | 本次**没有执行**（没装 cv2 / 没写 visual_at / 没签核） | 默认 0，`--strict-gates` 下非 0 |

**"skipped ≠ 通过"** 是这套机制的全部意义：以前"没查"和"查了没事"在输出上都叫"没报错"。

```bash
cat out/QC-REPORT.md                          # gate matrix + 实测值 + 签核 + 耗时，交付时一起给
python render.py ... --stage qa --strict-gates   # 交付前：skipped 也算失败
python render.py ... --stage accept --gate stills --note "看过联系表"   # 人工签核
```

**四道人工签核闸**（`style` 风格与声线 / `stills` 母版静帧 / `facts` 上屏数字 / `release` 成片验收）：

- 签核前会检查"证据产物存在"（如 `stills` 要求 `work/qa/sheet.png`），**没有证据不许签**——
  否则签核就是橡皮图章。
- 签核绑**内容指纹**（剧本 + `scenes.py` + 引擎）。改了其中任何一个，旧签核**自动失效**，
  报告里会标成 `skipped` 并写明"内容已变"。这不是麻烦，是防止"我签过了"变成空头支票。
- 逃生阀是 `--no-gate`（整体跳过签核检查），但会写进 QC 报告的"本次绕过的门禁"一节，
  **留痕不等于通过**。

**耗时台账** `work/cost.jsonl`：每个 stage 一行实测墙钟（`--cost` 开启，`--stage all` 里自动记）。
它是 SKILL §12 那张"排期公式"的对照——公式从来没被验证过，现在有数据了。

### 7.0b 阶段新鲜度：别让旧产物混进新片（v0.6.0）

> 与 §1 的分块指纹同源：那里只修了 `frames` 一个点，其余 stage 此前是裸奔的
> （改了 `style` 后 `--stage audio` 仍读旧 `music.wav`；改了 `hold` 后 `--stage frames` 仍用旧 `cues.json`，**都不报错**）。

```bash
python render.py ... --stage explain          # 哪些阶段过时了、为什么、最小重跑命令
python render.py ... --stage qa --strict-stale   # 有过时就失败（CI / 交付前）
# 机读：问 stagewatch（render.py 的 explain 只打人读文本，不接受 --json）
python scripts/stagewatch.py --work work --json
```

机制：每个 stage 结束往 `work/manifest.json` 记一行 `{stage, at, inputs:{名:指纹}, outputs:{名:指纹}}`；
下次该 stage 开始前重算上游输入与自己的产物指纹。**默认只告警**（`--strict-stale` 升级为失败，
`--allow-stale` 显式放行并留痕）。改一句要重跑什么，见 [references/change-impact.md](references/change-impact.md)。

**质检分三层**，一层比一层更接近"观众真的看到了什么"：

| 层 | 查什么 | 工具 | 抓得住什么 |
|---|---|---|---|
| 容器层 | 分辨率/帧率/音轨/时长/码率 | `ffprobe`（`filmkit.probe`） | 参数写错、音轨丢了 |
| 信号层 | 响度/真峰/锐度比/黑帧/静音/字幕条数 | `filmkit.qa_report` | 像草稿、文字发虚、静默丢字幕 |
| **时间层（v0.3）** | **单帧闪烁 / 画面空窗 / 音画同步 / 响度曲线** | `qa_frames.py` | **"一帧白闪""两秒没东西看""重击没声音"** |
| 页面层（web 引擎） | 文字重叠/出画/一闪而过/从没露面 | `webengine.py textqa` | 页面里被别的元素压住的字 |

时间层需要单独说，因为它是**唯一靠"看完全片"才能发现**的一类问题：单看任何一帧都正常。

**先分清两档**（这张表以前混在一起，标题写"必过"，实际 8 行里 5 行只是 warning）：

- **error 档**：出现即 `qa` 返回非 0、CI 红。这些是"片子坏了"。
- **warning 档**：只提示，不阻断交付。这些是"能更好"或"门禁没跑"。

#### error 档（必须过）

| 判据 | 阈值 | 失败怎么办 |
|---|---|---|
| 分辨率/帧率 | 严格等于设定值 | 检查 FrameSink 参数；`strict=True` 会直接报错而不是静默缩放 |
| 音轨缺失 | 必须有音轨 | mux 参数漏了 `-i audio.wav` |
| 时长 | 与 `frames/FPS` 差 < 0.5 s | 断点续渲漏帧；分块模式检查缺口检测 |
| **真峰** | **> -1.0 dBTP**（-1.0 ~ -1.4 之间只 warn） | 混流前目标设 -1.5（AAC 会把真峰抬高 ~0.9 dB） |
| **编码锐度比** | **< 0.85**（0.85–0.92 只 warn；没装 cv2 则门禁跳过 + warn） | 降 CRF 或提 preset |
| **单帧闪烁（v0.3）** | **0 条** | 位移/推力没归零、粒子贴镜头过曝、转速突变；见 §8 故障速查 |
| **画面空窗（v0.3）** | 首尾以外 0 段（≥0.8 s 近黑） | 转场之间没有内容连续性：让下一幕提前开始 |
| **音画同步（v0.3）** | 判到超差（`dt > 60 ms`）即 error；**但默认可能根本没在查**（见下方两小节） | 要给 cue 写 `visual_at`，或设 `QA_REQUIRE_SYNC=1` 把"没查"也变成 error |
| **字幕条数** | **= 剧本句数** | 少一条就是静默丢字幕，查 `sub_window` 与 `write_srt` |
| **时长（时间层复核）** | 与预期差 > 0.5 s | `qa_frames` 会独立再查一次时长 |

#### warning 档（不阻断，但别当看不见）

| 判据 | 触发条件 | 怎么办 |
|---|---|---|
| 音轨采样率 | ≠ 48 kHz（**音轨缺失才是 error**） | mux 的 `-ar 48000` 被漏了 |
| 响度略偏 | -17.2 ~ -14.5 之外但仍在 -19 ~ -12 内 | 目标 -16 ± 1 是"舒适区"；重跑 loudnorm |
| 真峰余量不足 | -1.4 ~ -1.0 dBTP | AAC 编码后可能更高，留 1.4 dB 余量 |
| 锐度比偏低 | 0.85 ~ 0.92 | 可接受但可更好 |
| 长黑帧 | 首尾以外出现 ≥0.4 s 黑帧 | 检查素材/淡入淡出 |
| 长静音 | 出现 ≥1.2 s 静音 | 确认是否有意留白 |
| 字幕安全区 | `qa_text_safe` 有警告（>2 行、越界、触底） | 缩短字幕或调 `SUB_BAND` |
| 缺字形 | `check_glyphs` 有警告 | 改文字或用矢量线条画（msyhbd 缺 ✓ × ↵ 等） |
| **画面长时间静止（v0.4）** | 连续 6 s 内最活跃 8×8 块的 0.5 s 漂移 ≤ 1.5 级灰度 | 加视觉变化（§4「每 3–6 秒要有变化」）；确实要静止就接受它 |
| 门禁没执行 | 没装 cv2 / 测不到响度 / 找不到 sub.srt / 没 events 也没锚点 | **这一条最重要：它说明"看起来通过的项，其实没在查"** |

**三个 v0.2 才修对的坑（v0.1 的"门禁"其实是摆设）：**

1. **锐度比的参考帧不能是成片自己抽的帧。** 那样 `sharp_ref` 与 `sharp_dec` 几乎相同，
   ratio 恒 ≈ 1.0。必须用**场景函数重新渲染**同一时刻的原图（`st_qa` 已这么做）。
2. **真峰必须显式开 `ebur128=peak=true`。** 默认参数下解析不到 `Peak:`，
   "真峰 ≤ -1 dBTP" 这条永远不触发。
3. **码率不是质量指标。** 暗底大字内容 0.5–1 Mbps 是正常的；把它当"下限"会天天误报。

**"不能有"和"必须有"两类都要查**：闪烁/重叠/出画/削波是"不能有"；图名框/淡入/字幕/每段至少一个动态元素是"必须有"——后者最容易静默失效。

**时间层门禁怎么读**（`work/qa/deep.json`）：

| 字段 | 意义 | 判据 |
|---|---|---|
| `flicker[]` | 只亮一帧的格子：`|F[t]-(F[t-1]+F[t+1])/2|` 分块超阈值 **且** `|F[t+1]-F[t-1]|` 很小（"回到原位"——加速运动不满足这条） | 0 条；有的话 `bbox` 告诉你在画面哪个位置 |
| `black_windows[]` | ≥0.8 s 近黑（片头片尾不算） | 0 段；有就补内容连续性 |
| `sync[]` | cue 与视觉事件的时间差 | 全部 `|dt| ≤ 0.06 s` |
| `luma[]` / `loudness[]` | 逐帧亮度 / 短时响度曲线 | 看形状：有没有"该有起伏的地方一条直线" |

**别把"重击"当闪烁**：设计好的闪白/震屏会出现在 `events` 里（web 引擎由页面导出，pil 引擎写进 `cues.json`），
`qa_frames` 会先排除它们再报 `flicker`。没登记却又闪的，才是 bug。

**也别把"运动最快的一帧"当闪烁**：二阶差分对加速运动天生过敏（逐字入场、缓动推近、
交界黑场的起坡帧）。所以判据还有第三道闸——**单帧事件必须"回到原位"**：
`|F[t+1] - F[t-1]|` 要远小于中间帧的偏离量（`FLICKER_RETURN_RATIO = 1.6`）。
高分（≥10）但被判成运动的候选**会进 warning**（"高分异常被判为运动，建议人工复核"）——
**门禁可以保守，但不能静默**。

实测反例（本技能自带的 web 模板片）：报了 3 处闪烁，逐一逐帧查下来**全部是**
"最后一个字入场"和"交界黑场起坡"这两类加速运动；加上这道闸后归零，
而 selftest 里注入的单帧白闪仍然能被检出——**没被削弱的门禁才叫门禁**。
调阈值之前先确认这条闸在不在：没有它，你会把正常运动当 bug，改到死。

### 音画同步的命名约定（门禁会不会静默失效就看这条）

`sync_check` **先按 `events.kind == cue 的配方名` 配对**。所以画面里"该响一下"的事件，
kind 要写成**同一个配方名**：

```js
S.cue(0.62, 'impact', {size: 1.15});   // 音频：配方名 impact
S.event(0.62, 'impact', {});           // 画面：同名的 visual event
S.event(0.62, 'ring',   {});           // 纯装饰事件：用通用名，不参与配对
```

**不这么写会怎样**：页面只导出 `flash / shake / ring` 而 cue 是 `impact / whoosh` 时，
同 kind 永远配不上、跨 kind 又只做确认，`sync` 会恒为空 —— **看起来"零超差"，其实一条都没在查**。

两道保护：
1. `qa_frames` 发现"cues 与 events 没有任何同名项"时**主动给 warning**（"音画同步检查可能没生效"）；
2. 自查：故意把某个 cue 的时刻改错 0.3 s 重跑，**必须**能报出超差。不报就是没配上。

### PIL 路线的同步门禁怎么真的生效（v0.4 新增）

`--engine pil` 没有页面，也就没有 `events`，"同 kind 配对"永远配不上——**这条门禁默认是没在查的**。
v0.4 给了两条真正能用的路：

1. **给 cue 写画面锚点**（推荐）：在 `film.json` 的 `sfx.cues` 或 `scenes.py` 的 `cues()` 里，
   给每条 cue 加一个可选字段 `"visual_at"`（或 `"visual_t"`），值是**画面上那个事件真正发生的绝对秒数**：

   ```json
   { "at": "l03+0.05", "type": "impact", "visual_at": 15.77 }
   ```

   `qa_frames` 会直接算 `|cue 时刻 - visual_at|` 并报超差，不依赖 events / 命名约定。
   锚点必须写画面上的真实时刻——随便填一个等于自己骗自己。

2. **把"没查"变成失败**：设环境变量 `QA_REQUIRE_SYNC=1`（或 `qa_frames.py --require-sync`），
   "音画同步门禁没执行"就从 warning 升级成 error。CI / 交付前建议开着。

两条都没做时，`deep.json` 里 `sync` 恒为 `[]`，同时会有一条 warning 明说"同步对照跳过"。
**看到 `sync: []` 先确认是"真的同步"还是"根本没查"**——`warnings` 里有没有那句话就是判据。

## 8. 常见故障速查

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
| 画面里 ✓ × ↵ 显示成方框 | msyhbd 缺这些字形 | 用矢量画（ImageDraw 线条），不要用符号字符；或先跑 `K.check_glyphs` |
| **改了台词但配音没变** | TTS 缓存只判断"文件存在" | v0.2 已把 text/voice/rate/pitch 哈希写进 `*.mp3.json`；旧缓存需 `--force-tts` 一次 |
| **长句字幕读到一半消失** | `sub_window` 把 t1 钳在 `voice_start+5.2` | v0.2 默认覆盖整句；要钳短显式传 `max_hold` |
| **--stage qa 也重渲全片** | stage 分发写成 `in ("all","frames","mux","qa")` | v0.2 每个 stage 独立；qa 只读成片 |
| **concatenation 打不开分块** | concat.txt 里写相对路径，ffmpeg 按自己 cwd 解析 | 写**绝对路径**（v0.2 已修） |
| 成片比 silent.mp4 慢一倍 | mux 对已编码视频又编一遍 | `-c:v copy`（v0.2 默认）；只在要改分辨率时 `reencode_video=True` |
| 每帧渲染慢 2–3 倍 | 每帧重新 `wrap_cn`（中文逐字测宽） | 用 `K.wrap_cn_cached` / `K.text_w_cached` |
| 混音偶发爆表 | 归一化分支没执行 / 叠加峰值 >1 | `AudioBus.mix()` 会打印叠加峰值并软限幅，峰值 >0.97 报警 |
| **成片音轨变成 96kHz** | mux 没指定 `-ar`，loudnorm 内部升到 192kHz 后 AAC 取了 96k | mux 显式 `-ar 48000`（v0.2 已内置，selfcheck 第 12 条守着） |

## 9. 参考资料（按需加载）

- `references/pipeline.md` — 八阶段详细规格、契约 JSON 格式、目录约定、并行分工
- `references/scene-authoring.md` — 场景函数写法、构图规则、Canvas/CDP 方案、超采样与后处理
- `references/audio.md` — 采样级混音、程序化配乐/音效、响度链路、循环环境音、提示点系统
- `references/audio-variation.md` — **（v0.7）片间差异**：内容指纹怎么进和声/进行/动机/鼓型，legacy 兼容与验收阈值
- `references/sfx-design.md` — **（v0.3）音效设计**：物理拆解流程、配方表、听感诊断表、QC 规则
- `references/web-engine.md` — **（v0.3）浏览器引擎**：页面契约、确定性时间轴、分块渲染、崩溃重开
- `references/frame-qa.md` — **（v0.3）帧级质检**：单帧闪烁判据、画面空窗、音画同步、报告怎么读
- `references/quality-gates.md` — G0–G6 全部门禁、可复制代码、验收清单
- `references/change-impact.md` — **（v0.6）改动影响表**：改了 text/sub/hold/style/scenes.py 各要重跑什么
- `references/style-packs.md` — **（v0.5）风格包**：主题/配乐/声线字段、三级指纹规则、加第 9 套风格的步骤
- `references/pitfalls.md` — 20 条最佳实践 + 16 条反模式 + 3 个非确定性 bug 复盘

## 10. 脚本资产

| 文件 | 作用 |
|---|---|
| `scripts/filmkit.py` | 生产内核：字体/字形自查/缓存排版/字幕安全区/缓动/确定性随机/音频总线/**CueBus 提示点**/**立体声混音 mix_film**/TTS/时间轴/**hold 停留窗口**/FrameSink/合成/质检 |
| `scripts/render.py` | 编排器：**13 个 stage**（含 `accept` / `explain`）、两种引擎（`--engine pil\|web`）、分块并行（`--jobs`）、断点续跑（`--resume`）、母版预览（`stills`） |
| `scripts/gates.py` | **（v0.6）门禁账本**：pass / fail / skipped 三档、`out/QC-REPORT.md` 渲染、人工签核（绑内容指纹）、原子写 |
| `scripts/stagewatch.py` | **（v0.6）阶段新鲜度**：每 stage 记录输入/输出指纹到 `work/manifest.json`；上游一改，下游报过时并给出最小重跑命令 |
| `scripts/filmcheck.py` | **（v0.6）film.json 契约校验**：未知键提示"是不是想写 X"（以前静默忽略）、枚举与范围、跨字段（cue 引用的 id 必须存在） |
| `scripts/selfcheck.py` | 回归自检：**43 条**"曾真的坏过"的断言，不联网 20 秒内出结论（可选模块缺失标 SKIP） |
| `scripts/sfxkit.py` | **（v0.3）物理建模音效库**：模态合成/纹理/空间/动力学/母带 + 24 个配方 + `CUE_ALIASES` |
| `scripts/scorekit.py` | **（v0.3）程序化配乐**：和弦表/分段能量/律动层/吸气/自动电平，输出 `music.wav` |
| `scripts/webengine.py` | **（v0.3）浏览器逐帧渲染**：Edge/Chromium + CDP 截图、分块并行、崩溃重开、cue 导出、文字质检 |
| `scripts/qa_frames.py` | **（v0.3）帧级质检**：单帧闪烁/画面空窗/音画同步/亮度与响度曲线（JSON + PNG） |
| `assets/scenes.py` | 场景模板（开场/正文卡片/收尾 + **`cues()` 提示点钩子示范**），含缓存排版与预烘焙背景的正确写法 |
| `assets/film.json` | 剧本模板（含 `hold`/`bpm`/`music`/`sfx.cues`/`engine` 等新字段） |
| `<REPO>/schemas/film.schema.json` | **（v0.6）film.json 的契约**（JSON Schema draft-07 子集）。校验器见 `scripts/filmcheck.py`，模板由 `tools/ci_check.py` 第 12 项守着 |
| `<REPO>/tools/estimate.py` | **（v0.6）开渲前预演**：帧数 / 各阶段耗时 / 磁盘峰值 / 分块建议，`--json` 可机读。基准常量集中在文件顶部，换机请重测后回填 |
| `assets/web/` | **（v0.3）浏览器页面模板**：原生 Canvas2D 确定性时间轴、场景示范、文字质检脚本 |
| `requirements.txt` | **（v0.4）Python 依赖清单**：必需（numpy/Pillow/edge-tts）/ 强烈建议（cv2）/ 可选（scipy、playwright）三档，带注释说明"缺了会怎样"。`pip install -r requirements.txt` 即可 |

### 关键内置能力（v0.2）

| API | 用途 |
|---|---|
| `K.tts(...)` | 逐句合成；缓存 key = text+voice+rate+pitch 哈希；网络重试 4 次；可选尾静音裁剪 |
| `K.loudnorm_two_pass(...)` | 两遍响度标准化，返回可给 `-af` 的滤镜串 |
| `K.loudness(path)` | 返回 `{I, LRA, TP}`（真峰已开 `ebur128=peak=true`） |
| `K.AudioBus.music_bed(...)` | 连续配乐床（和弦交叉淡化，无接缝爆点） |
| `K.AudioBus.mix()` | 归一化 + 软限幅，打印叠加峰值，>0.97 报警 |
| `K.wrap_cn_cached / text_w_cached` | 场景每帧排版必用（实测单帧 12ms → 4.6ms） |
| `K.glyph_missing_chars / check_glyphs` | 缺字形自查（✓ × ↵ 会画成方块） |
| `K.write_ass(...)` | 直接输出 ASS，头里声明 PlayResX/Y=1920/1080 |
| `K.encode_quality(film, ref_img=...)` | 锐度比；参考帧必须来自场景函数，不能是成片抽帧 |

## 11. 验收话术（不要自我宣称"做好了"）

交付时必须能贴出：
1. `ffprobe` 的时长/分辨率/码率
2. `ebur128` 的积分响度与**真峰**（两个数都要，只有响度不算）
3. 编码锐度比（≥0.85）**并说明参考帧来自场景函数重新渲染**
4. 联系表路径 + "已用 read_image 亲眼确认"的**具体观察**（不是"看起来不错"）
5. 字幕条目数 = 剧本句数（无静默丢失）
6. `python selfcheck.py` 的 PASS 计数
7. **（v0.3）帧级深检的三个数**：单帧闪烁 N 条 / 画面空窗 N 段 / 音画同步超差 N 条
   （有 web 引擎时再加页面层的 文字重叠 / 出画 / 一闪而过 / 从没露面 四项）

> **v0.6 起不要口头复述这些数字**：`out/QC-REPORT.md` 是机器生成的交付凭据，
> 照抄报告里的"实测值"即可；报告里的 `skipped` 项必须逐项说明为什么没执行。
>
> 完整示例（本技能自带模板片的实测，可以照这个格式抄；**数字每次跑会差一点**——
> TTS 实测时长、AAC 编码都会让真峰/响度/锐度在小范围内浮动，别 chasing 小数点）：
> ```
> 容器：1920x1080 / 30 fps / h264+aac / 48 kHz / 时长 30.10 s / 1.00 Mbps
> 信号：-16.0 LUFS / 真峰 -1.2 dBTP / 锐度比 1.01 / 黑帧 0 / 字幕 4 条 = 剧本 4 句
> 时间：单帧闪烁 0 / 画面空窗 0 / 音画同步超差 0（本条为 web 引擎或写了 visual_at 时才有意义）
> 自检：selfcheck PASS 43 / FAIL 0 / SKIP 0；tools/ci_check.py PASS 12 / FAIL 0
> 音频：work/score.json 的 variation 非 0（指纹参与了结构变奏）、audio_similarity --strict 全 PASS
> 目检：work/qa/sheet.png，四帧分别为开场星射线、五步成片卡片、画质三件事卡片、收尾居中大字；
>       字幕黑底白字不压正文，大字边距均匀，无空洞帧。
> ```

> 自查口令：如果一条门禁从来没失败过，先确认它是不是根本测不到。

## 12. 作者机器已验证的环境事实（2026-11-07 实测）

> **本节是作者机器（Windows 11 + Ryzen AI 9 HX 370 + OSGeo4W Python 3.12）的实测基准，不是跨机器承诺。**
> 换机后请先用 5 分钟重测：`ffmpeg -version`（是否有 libx264/aac/loudnorm）、
> `python -c "import numpy,PIL,cv2"`、`dir C:\Windows\Fonts\msyhbd.ttc`、`edge-tts --list-voices`。
> 任何一项不符，先改 `filmkit.FONT_CANDIDATES` / `FILMKIT_FFMPEG` 环境变量，别硬跑。

**这些数字是实测的，不是文档抄的。**

| 项 | 实测结论 |
|---|---|
| ffmpeg | **9.0.2 full_build**（gyan.dev），路径 `C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe` |
| 编码器 | 实测可用：libx264 / libx265 / libvpx-vp9 / **aac（native）** / h264_amf；**无 libfdk_aac**。ffmpeg 构建里**带 nvenc 编码器**（h264/hevc/av1_nvenc），但本机无 NVIDIA GPU，跑不起来——别看到 `ffmpeg -encoders` 里有就以为能用 |
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
| 实测渲染 | PIL 中等复杂度场景 **40–54 fps**（1080p30）；自带模板片 **68–82 fps**（多次实测区间） |
| 磁盘 | 当时约 C 300 GB / D 520 GB（会持续变动，别当基准） |

### v0.3.0 各模块实测（同机，2026-11-07）

| 模块 | 实测数据 |
|---|---|
| `sfxkit` | 24 配方单次渲染 ≤ 1.5 s（**selftest 断言的门槛**；空载最慢 wind ≈0.8 s，并发负载下 ~1.3 s）；**389 s 长环境声** wind 5.7–7.8 / rain 7.3–7.9 / campfire 6.3–6.7 s（预算 8 s，随负载浮动）；真峰与 ffmpeg 差 **0.05 dB**；响度与 ebur128 差 **0.01 LU** |
| `scorekit` | 389 s 全片 **4–8 s**（v0.3 首测 6.7–8.2 s，v0.4 复测 3.9–5.8 s，随负载）；60 s 实测 2–4 s；输出 -18.5 LUFS / -1.44 dBTP（母带交给 loudnorm）。**selftest 会打印当下实测**，以那个为准 |
| `webengine` | **80–130 ms/帧**（1080p30，Edge 无头，PNG 截图）；12 s / 360 帧 **28–30 s**（2 workers，含启动）；**同一页面会话内**同帧两次截图 byte 完全一致（跨会话默认 GPU 光栅有 ≤4 像素、±1 级抗锯齿差异，要跨会话字节级一致请开 `--deterministic`，代价约 +22%/帧） |
| `qa_frames` | 30 s 片全量深检 **约 1 s**（160×90 流式解码 903 帧）；闪烁/空窗/静止/同步 |
| 端到端 | 30.10 s 模板片：TTS 4 句 → cues 由 scenes.py 按 kind 生成（v0.7，模板片 17 条）→ 配乐 3.3 s → 音效逐条落位 → 混音 → PIL 75 fps 渲染 12 s → mux → 深检 PASS |
| Python 依赖 | **playwright 已装**（chromium + msedge 均可用）；scipy 1.18 已装（sfxkit 可选，缺了走 FFT 兜底）；`soundfile` **没装**（本技能不依赖它） |

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

## 13. 升级须知（从旧版迁移要动的地方）

### 13.0 v0.4 → 当前版本（v0.5 / v0.6 / v0.7 / v0.7.1 / v0.7.2 要做什么）

**一句话**：v0.4 的老项目**不写新字段就能继续跑**（不写 `style` = legacy 行为，配乐逐样本不变），
但**工作目录要多拷文件**，且有两处默认值变了要重新听。按版本逐条过：

| 从 → 到 | 你要做的动作 | 依据 |
|---|---|---|
| v0.4 → **v0.5** | 新工作目录多拷 `scripts/stylekit.py`（**必需**，render 直接 import）、`scripts/voicekit.py`（可选，缺了单声线降级）；`--stage` 多出 `style`，`--stage all` 顺序变为 `style → tts → …`；改用 `style: "auto"` 才会自动选风格，不写仍是 legacy | CHANGELOG 0.5.0「升级须知（v0.4 -> v0.5）」 |
| v0.5 → **v0.6** | **必需**多拷 `scripts/gates.py`、`scripts/stagewatch.py`（少一个 render.py 直接 `ModuleNotFoundError`）；首次跑会多出 `work/manifest.json` / `work/gates.json` / `out/QC-REPORT.md`，属新增文件 | CHANGELOG 0.6.0「升级须知（v0.5 → v0.6）」 |
| v0.6 → **v0.7** | 想吃到「配乐/音效随内容变化」就保持 `style: "auto"`，**不要**写死 `music.seed` 或关掉 `seed_from_content`；音效想更丰富就把提示点写进 `scenes.py` 的 `cues()` | CHANGELOG 0.7.0「升级须知（v0.6 → v0.7）」 |
| v0.7 → **v0.7.1** | 配乐基线默认值从 `music.gain_db=-1.5` 变成 **-12**（模板也已改），有人声处再压配乐 6 dB / 音效 3 dB；**之前手调过 `gain_db`/`duck_db` 的片子要重新听一遍**（现在背景整体更低）。显式写 0 可关掉对应侧链 | CHANGELOG 0.7.1「修了什么」+ `assets/film.json` 的 `music` 默认值 |
| v0.7.1 → **v0.7.2** | `QC-REPORT.md` 现在**只落在 `--out` 的目录**（以前 `accept` 分支按 `work` 的兄弟目录算，`--out deliver/film.mp4` 时签核会更新到另一份没人交付的文件）。如果你用 `--out` 指到了非 `out/` 的目录，交付时请看新位置；旧的 `out/QC-REPORT.md` 不会被再写 | `render._report_path()` 的 docstring + `selfcheck` 第 40 条（本轮新增） |

> 查当前版本：`python <REPO>/tools/ci_check.py -v` 里的 version 断言、或 `cat package.json` 的 `version`。
> 升级后建议按 §14 重跑一次自检（43 条），再按 §7 重新签核（改了代码/默认值，旧签核会自动失效，这是设计）。

### 13.1 v0.3.0 → v0.4.0

**多数项目不用改任何东西**（默认行为向后兼容），但下面几处会**改变你看到的输出或报错**：

| 变更 | 影响 | 你要做什么 |
|---|---|---|
| `--resume` 增加计划指纹 | 改过台词 / scenes.py / crf·preset·chunk 之后再用 `--resume`，会**报错拒绝拼接**（旧行为是静默复用错画面） | 按提示 `rm -rf work/segments` 重渲；没改过东西时 `--resume` 照旧只补缺块 |
| `music.mode=off` 真的不混配乐 | 以前 `work/music.wav` 存在就会被混进去；现在 off 时既不读也不删 | 想用外部配乐：显式 `"mode": "file"`，或不写 `mode`/`bpm` 让兼容链生效 |
| `--stage qa` 报错更严 | 缺 `scenes.py` 时不再崩，改为丢参考帧 + 两条 WARN；锐度比参考帧按时刻配对 | 无（行为更合理） |
| 新增"画面长时间静止"WARN | 连续 6 s 无视觉变化会告警（默认不 fail） | 设计上确实要静止 >6 s 就接受它；误报可调 `FLAT_DRIFT_EPS` |
| 音画同步可真的生效 | cue 支持 `visual_at`；`QA_REQUIRE_SYNC=1` 把"没查"升级为 error | 想用门禁就加锚点 + 环境变量；不想要就保持默认（仅 warning） |
| `film.json` 校验变严 | 缺 `text` / `lines` 空 / id 重复 / bpm 非法 会直接报错 | 按提示修剧本 |
| 可选 peer 改成按需 import | 缺 `@deepseek-ai/dsh-skill-filesystem` 时插件不再整体加载失败，只是不挂载技能并打日志 | 想用技能就按日志里的命令装依赖 |

### 13.2 v0.2 → v0.3.0

**兼容性优先：老项目不做任何改动也能跑**（`engine` 默认 `pil`、`music.mode` 缺省时按老的和弦床走、`whoosh` 行为保留）。

| 变更 | v0.2 | v0.3.0 | 你要做什么 |
|---|---|---|---|
| stage 列表 | 7 个 | **10 个**（+cues/score/sfx） | `--stage all` 顺序变了；单独跑多阶段时补 `--stage cues sfx` |
| 渲染引擎 | 只有 PIL | `--engine pil\|web` | 想用浏览器就拷 `assets/web/` 并装 playwright |
| 配乐 | 只有和弦床 | 有 `bpm` 时用 scorekit | 想保留旧声音：`"music": {"mode": "bed"}` 或不写 `bpm` |
| 音效 | 每段一声 whoosh | 按 `cues.json` 合成 | 想保留旧行为：不写 `sfx.cues`、不写 `scenes.cues()`，且不跑 `--stage cues` |
| 混音 | 旁白+配乐两路 | 旁白+配乐+音效三路（带 duck） | 无（自动） |
| 时间轴 | lead/tail/min_dur | + `hold` / `bar` | 想加停留就写 `hold` |
| 质检 | 成片级 | + 帧级深检（有 `qa_frames.py` 时） | 无（自动；没这个文件就跳过） |
| qa 退出码 | 恒 0 | **有 FAIL 就返回 1** | CI 里会开始真的失败——这是故意的 |

**新增文件**：`sfxkit.py` / `scorekit.py` / `webengine.py` / `qa_frames.py` / `assets/web/`（都是可选，缺失时对应能力自动降级并打印 WARN）。

### 13.3 v0.1 → v0.2（历史）

| 变更 | v0.1 | v0.2 | 迁移动作 |
|---|---|---|---|
| 阶段语义 | `--stage mux/qa/frames` 都会重渲全片 | 每个 stage 只做自己的事 | 脚本里连续跑多阶段时去掉重复的 `--stage all` |
| TTS 缓存 | 只判断 mp3 存在 | text/voice/rate/pitch 哈希必须匹配 | 首次升级加 `--force-tts` 跑一次 |
| 字幕窗口 | 硬钳在 `voice_start+5.2` | 默认覆盖整句（`end-0.06` 封顶） | 确实要短字幕就传 `sub_max_hold` |
| 视频合成 | 对 silent.mp4 再编一遍 libx264 | `-c:v copy`（不重编码） | 要改分辨率时显式 `"reencode_video": true` |
| 帧尺寸 | 不符时静默 resize | `strict=True` 直接报错 | 场景函数必须返回 1920×1080 |
| 码率门禁 | `min_bitrate=3Mbps` 算错误 | 默认不当门禁，只做参考 | 需要就显式传 `min_bitrate` |
| 真峰门禁 | 解析不到 Peak → 永不触发 | `ebur128=peak=true`，真测 | 无（自动生效） |
| 锐度比 | 参考帧用成片抽帧 → 恒 ≈1.0 | 参考帧由场景函数重新渲染 | 无（`st_qa` 已改） |
| TTS 音频 | 保留 edge-tts 尾部 ~0.95s 静音 | 默认裁掉首尾静音 | 想保留：`"trim_silence": false` |

## 14. 换机 60 秒自检（比读文档快）

```bash
cd <SKILL_DIR>/scripts
python selfcheck.py                  # 43 条断言；FAIL 就照提示修环境
python sfxkit.py --selftest          # 音效库（可选模块，装了才跑）
python scorekit.py --selftest        # 配乐
python webengine.py --selftest       # 浏览器引擎（24 项，会真起无头浏览器，约 15 秒）
python qa_frames.py --selftest       # 帧级质检（自己造测试视频）
```

selfcheck 自己造测试信号、现场调 ffmpeg 编码，**不联网、不依赖任何成片**。
每条 FAIL 都对应一个具体环境缺口（缺字体 / ffmpeg 无 libx264 / 无 numpy / 缺 sfxkit），
报错信息直接写该改哪里。可选模块缺失时 selfcheck 标 `SKIP` 而不是 FAIL——
**别把"没装可选件"和"坏了"混为一谈**。

> **仓库级体检**（v0.4 起）是另一层，别和 selfcheck 搞混：
> `python <REPO>/tools/ci_check.py -v`（在包根执行）只查"这个仓库作为可分发包是否完整"（语法/JSON/依赖白名单/
> 打包卫生/文档路径引用），**不需要 ffmpeg、字体或网络**，任何机器都能跑；
> selfcheck 查的是"技能还能不能真的出片"。两层都在 CI 里跑。
>
> v0.6.0 起 ci_check 多了第 12 项「film.json 契约」：直接调 `scripts/filmcheck.py` 校验模板，
>并把 `schemas/film.schema.json` 的存在与合法性一起断言——**校验规则只允许有一份**，
>不在这里重写一遍。

### v0.2 在本机实测（2026-11-07，同一台机器）

| 项 | 数据 |
|---|---|
| 场景渲染（缓存排版 + 预烘焙背景） | **单帧 12.3ms → 4.6ms**；885 帧整片约 8s（103 fps） |
| 分块并行（--jobs 3，4 段） | 885 帧 **7s** 完成分段，拼接后时长误差 0 |
| mux（视频流 copy） | 两遍 loudnorm 后 −16.5 LUFS / 真峰 −1.5 dBTP，不二次编码 |
| 11 分钟真实片 | 帧渲染 69–80 fps |
| selfcheck | 14/14 PASS（含真峰、ASS PlayRes、尾静音裁剪、mux 48kHz） |
