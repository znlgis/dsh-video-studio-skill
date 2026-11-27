# 八阶段管线规格

## P0 定基准（先写文档，再写代码）

一份 `CONVENTIONS.md` 锁死所有换算，全项目只引用它：

```
分辨率 1920x1080 / 帧率 30 / 采样率 48000
BPM 96, 4/4 → 1 小节 = 2.5 s = 75 帧 @30fps
总时长 = 段数 × 段长（由旁白决定，不预设）
幕边界必须落在小节线上：幕 n 起始帧 = round(bar * 2.5 * FPS)
```

**为什么值得花 10 分钟**：画面、配乐、音效、字幕四个子系统全部引用同一份换算；任何一处各写各的 `150`，全片就会错位。

## P1 写剧本 film.json

```json
{
  "title": "片名（顶栏常驻）",
  "voice": "zh-CN-YunyangNeural",
  "rate": "+6%",
  "crf": 18,
  "lead": 0.55, "tail": 0.85, "min_dur": 3.0,
  "music": {"enabled": true, "seg": 4.0, "amp": 0.055,
            "chords": [[220.0, 261.63, 329.63], [174.61, 220.0, 261.63]]},
  "whoosh": true,
  "lines": [
    {"id": "l01", "kind": "hook", "text": "朗读文本（自然口语，可以长）",
     "sub": "上屏字幕（短，一行优先）", "big": "画面大字"},
    {"id": "l02", "kind": "body", "chapter": "02 · 章节名", "kicker": "PIPELINE",
     "text": "...", "sub": "...", "big": "标题", "subtitle": "副标题",
     "items": [{"text": "要点", "ok": true}]},
    {"id": "l03", "kind": "outro", "text": "...", "big": "收尾大字"}
  ]
}
```

**关键设计：`text`（朗读）与 `sub`（字幕）分离**。朗读要口语自然（可以有"其实""也就是说"），字幕要一眼看完（≤24 字）。用一个字段两头凑，必然一边将就。

## P2 事实核查（科普/纪录片必做）

产出 `FACTS.md`，三条要求：
1. 上屏的每个数字/年份都有**编号 + 出处链接**
2. 单独一节"不能上屏"黑名单：写清为什么不行、实际是什么
3. 口径表：同一数字在报告/字幕/画面里表述完全一致

真实收益（一次实际核查的账）：75 条主张 + 25 条黑名单，抓出 8 处硬伤，其中"疫情在拆把手后很快平息"这类因果讲反的错误会被评论区第一条指出。

## P3 TTS（唯一正确的时长来源）

```python
import filmkit as K
lines = K.tts_batch(script["lines"], work, voice="zh-CN-YunyangNeural", rate="+6%")
# 每句得到 {path, dur} —— dur 是 ffmpeg 解码后实测的秒数，不是估算
```

要点：
- **逐句合成并缓存**（按 id 命名），改一句只重跑一句
- 同时用 `ffmpeg -f f32le -ac 1 -ar 48000` 解码测时长，不要信 mp3 头
- edge-tts 每条尾部有 ~0.95 s 静音，片段之间累计能多出几十秒；需要紧凑时用 `fade_out` 或裁尾
- 中文音色：`YunyangNeural`（男声叙述，最稳）、`XiaoxiaoNeural`（女声）、`YunjianNeural`（男声有力）
- 语速 `+0%` 到 `+10%` 之间；超过 `+15%` 会有机械感

## P4 排时间轴

```python
tl = K.build_timeline(lines, lead=0.55, tail=0.85, min_dur=3.0)
# 每段: start / end / dur / voice_start / voice_end
# 不变式: voice_start + dur_voice <= end - tail   （旁白必须读完）
```

字幕窗口规则（防交叠，来自实战）：
```
t0 = voice_start - 0.12
t1 = voice_end + 0.45                     # 没有 5.2 s 硬钳；要钳短显式传 max_hold
t1 = min(t1, next.voice_start - 0.12)   # 绝不压住下一句
```
停留时长参考 `0.5 + 字数/9` 秒。**没有默认上限**——
真要缩短就显式传 `max_hold` / `sub_max_hold`。按字符比例平均分配会产生大量交叠。

## P5 混音（numpy 总线）

```python
bus = K.AudioBus(total_dur)
for s in tl["scenes"]:
    bus.place(K.decode_pcm(s["path"]), s["voice_start"], gain=1.0, fade_in=0.02, fade_out=0.06)
# 配乐：和弦垫铺满
# 音效：每个转场点一个短促 whoosh
K.wav_write("work/audio.wav", bus.mix())
```

**绝不**用一条 ffmpeg 命令喂几十上百个输入：Windows 下输入线程池竞争会产生非确定性静音（同一条命令跑三次，正常/静音/正常）。

## P6 母版（先看再渲）

```bash
# 母版预览：先渲 3 帧看风格（缺 tts/layout 会自动补，联网合成一次旁白）
python render.py ... --stage stills --stills "l01:3,l02:5"
# 定稿之后才渲全片
python render.py ... --stage frames --jobs 4          # 分块并行渲染
python render.py ... --stage frames --jobs 4 --resume   # 只补缺块（按绝对帧号）
python render.py ... --stage qa                          # 只读质检，不重渲不重编
```
看三件事：构图有没有重心、配色是否统一、字重和字号是否舒服。**定了再渲全片**。

## P7 成片

```python
sink = K.FrameSink("work/silent.mp4", crf=18, preset="medium")   # rawvideo 直通
...
# 视频流默认 -c:v copy（不二次编码）；需要改分辨率时 reencode_video=True
K.mux("work/silent.mp4", "work/audio.wav", "out/film.mp4")
```

编码参数：

| 用途 | 参数 |
|---|---|
| 母版/发布 | `-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -g 60` |
| 快速预览 | `-preset veryfast -crf 23` |
| 高动态/粒子多 | `-crf 16 -preset slow` |
| 音频 | `-c:a aac -b:a 192k -ar 48000` |
| Web 播放 | `-movflags +faststart` |

## 目录约定

```
myfilm/
  film.json           剧本（唯一内容真源）
  scenes.py           场景绘制（唯一画面真源）
  filmkit.py          内核（不要改，除非你确实要扩展）
  render.py           编排器
  work/
    tts/              逐句 mp3 + .json（缓存）
    tts.json          实测时长表
    timeline.json     时间轴（唯一时间真源）
    sub.srt
    audio.wav         混音总线
    silent.mp4        无声成片
    qa/               静帧 + 联系表
  out/
    film.mp4
```

## 多 agent 并行分工（长片适用）

三条硬契约，否则必打架：
1. **写入边界硬性**：每人只写自己的文件；改公共模块先问 Lead
2. **接口先写死**：场景接口 / `film.json` 格式 / `timeline.json` 字段先定版
3. **一份施工文档**：把确定性铁律、单帧预算、安全区、镜头语言写成 `SCENE_SPEC.md` 发给每个人

长片按"幕"分派最有效：Lead 写规格+剧本+引擎+装配，每个 agent 负责 2–3 幕 `scenes/aN_*.py`，验收标准统一为"渲 3 帧 + read_image 亲眼看过 + 只写自己文件"。

## 附：SKILL.md 正文迁入（原 1 分阶段跑 / 成本预演 / 交付凭据 / 长片渲染）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

**分阶段跑**（每个 stage 只做自己的事，落盘后可单独重跑）：

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
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage beats    # 只测节拍 -> work/beat_data.json（卡点片才需要；无音乐时写 ok=false 占位）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage qa       # 只质检（只读输入，写 work/qa/；另按 §7.0 写 out/QC-REPORT.md 与 work/gates.json）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage stills   # 只渲母版静帧 + 联系表
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage editlist # 只出剪辑交接清单（editlist.json / EDITLIST.md）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage jianying # 只出剪映草稿计划（缺 pyJianYingDraft 时降级并说明）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --no-subs        # 不烧字幕出「干净画面」plate（给剪映导出用）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage accept --gate stills   # 人工签核一道闸（写 work/signoff.json）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage explain  # 只报"哪些阶段过时、为什么、该跑什么"，不跑任何 stage
```

`--stage all` 的完整顺序：`style → tts → layout → beats → cues → score → sfx → audio → frames → mux → qa → editlist`。

> `beats` 必须排在 `cues` 前（cue 的 `beat:` 锚点要靠网格解析）；`editlist` 排在 `qa` 后
> （它要把 `cues.json` 的音效落点写进表，早于 cues 会得到一份「音效列全是 —」的错表）。
> `review` / `jianying` **刻意不在 all 里**：前者是交付前的独立动作，后者要 `--no-subs`
> 出来的干净画面——塞进 all 只会每次都降级。

> `beats` 排在 `cues` 之前：cue 写 `beat:12` 这种锚点时要靠网格解析。`--stage all` 里
> 它分析的是 `work/music.wav`（还没生成时 cues 会自己补算一次，卡点片一条命令即可跑完）。
**任何一步出问题都会在该步停下**，不会带着坏输入继续往下跑。

**开渲前先算成本**（可选；`estimate.py` 是仓库工具，按 `<REPO>` 调用）：

```bash
python <REPO>/tools/estimate.py --script film.json --jobs 4
```

它给帧数/各阶段耗时/磁盘/分块建议（`--json` 可机读），是**区间上界**不是承诺；
有 `work/timeline.json` 时用真实时长，否则按字数粗估。要省掉全片 VMAF 就在 `film.json` 写
`"qa": {"vmaf": {"mode": "off"}}`（estimate 与 render 都读这个字段，G11 记 skipped）。

**看交付凭据再签核**：

```bash
cat out/QC-REPORT.md     # gate matrix 分 pass / fail / skipped 三档；skipped = 本次没执行，不等于通过
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage accept --gate release
```

签核绑内容指纹：改了剧本 / `scenes.py` / 设计，旧签核自动失效，要重新看、重新签。
交付前想强制（skipped 也算失败）：加 `--strict-gates`。

**长片渲染**（分块并行 + 断点续跑）：

```bash
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage frames --jobs 4
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage frames --jobs 4 --resume
```

> 分块落在 `work/segments/`，按 `seg_<起始帧>_<结束帧>.mp4` 命名；拼接前会**显式检测缺口**，
> 缺块直接报错，不允许"少一段也能出片"。默认 chunk 按 `总帧数 / jobs / 4` 向整秒取整（jobs=4、
> 900 帧时约 8 秒一段），可用 `film.json` 的 `chunk_frames` 覆盖。
>
> **改台词 / 改 scenes.py / 改设计 / 改编码参数之后，`--resume` 会拒绝复用旧分块**（`work/segments/_plan.json`
> 里存着计划指纹），并提示你 `rm -rf work/segments`——这是刻意的：旧分块配新时间轴会拼出
> "画面和旁白对不上"的片子，而且时长门禁查不出来。

## 附：SKILL.md 正文迁入（原 2 标准八阶段（P0-P7）+ 前置 P0.5）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

## 2. 标准八阶段（P0–P7）+ 前置 P0.5

> 表里共 9 行：**P0.5 定设计**是前置条件（没有 design 就没有外观），不是第九阶段。
> 它排在最前面是因为"先定外观再干活"——跳过它，后面每一步都在猜长相。

| 阶段 | 产出 | 关键动作 | 常见错误 |
|---|---|---|---|
| **P0.5 定设计** | `work/design.json` + `work/style.json` | `propose.py` 出方案 → `--confirm` 冻结设计；`--stage style` 只解析已确认的设计并打印主题/视觉语言/版式轮换/字幕 | 跳过 → `render.py` 报错（没有 design 就没有外观）；不确认就渲染会打提案警告 |
| **P0 定基准** | `CONVENTIONS.md` | 分辨率/帧率/时长/BPM→帧换算一次算死，全项目引用同一份 | 各文件自己写 `150` 这种魔数 → 幕边界对不齐 |
| **P1 写剧本** | `film.json` | 每句拆 `text`（朗读）与 `sub`（字幕）；每段一个 `kind`；可选 `lines[].type` 锁定构图 | 一个字段两头用 → 字幕太长或读起来生硬；**别写 `style`**（不再影响画面，外观由 `work/design.json` 决定） |
| **P2 事实核查** | `FACTS.md` | 上屏的每个数字/年份有出处编号；建"不能上屏"黑名单 | 跳过这步 → 画面和旁白数字打架，评论区第一条就是你错了 |
| **P3 TTS** | `work/tts/*.mp3` + `tts.json` | edge-tts 逐句合成，`ffmpeg -f f32le` 实测时长，磁盘缓存 | 按字数估时长 → 字幕漂移 |
| **P4 排时间轴** | `work/timeline.json` + `sub.srt` | `dur = lead + 实测语音 + tail`，句读必须在本段内 | 段太短 → 旁白被下一段截断 |
| **P5 混音** | `work/audio.wav` | numpy 总线：旁白 + 配乐 + 音效按采样点叠加 | ffmpeg 多输入混流 → 非确定性静音 |
| **P6 母版** | 3 张静帧 | 先渲 3 帧看构图/配色/字重，定了再渲全片 | 直接渲全片 → 36000 帧后发现字体丑 |
| **P7 成片** | `out/film.mp4` | rawvideo→libx264 CRF18 + 两遍 loudnorm 到 -16 LUFS | 码率过低/响度 -25 LUFS → "像草稿" |

> 注：`filmkit` / `render.py` 的默认是 `crf=18`；
> `assets/film.json` 只是**测试夹具**（跑通全链与金帧/门禁回归用，不是给你抄的模板），它写的是 17。
> 自己写剧本时不写 `crf` 就是 18；追求更锐利的文字可以写 17（体积约 +30%）。

## 附：SKILL.md 正文迁入（原 6 停留窗口）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

## 6. 停留窗口：让观众看清楚，而不是整片放慢

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

## 附：SKILL.md 正文迁入（原 7.5 交付生产线：画幅 / 批量 / 预览 / 运行版本化）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

## 7.5 交付生产线：画幅 / 批量 / 预览 / 运行版本化

### 7.5.1 画幅与帧率（横屏 16:9 / 竖屏 9:16）

```jsonc
"video": { "width": 1280, "height": 720, "fps": 24 }   // 不写 = 1920x1080@30
```

| 规则 | 实际行为 |
|---|---|
| 内部构图 | 跟随 `layoutkit` 的 profile：**横屏 1920x1080 / 竖屏 1080x1920**；输出更小时在编码阶段用 ffmpeg `scale`（lanczos）下采样（`FrameSink(out_w=, out_h=)`） |
| 允许的输出尺寸 | 横屏 1920x1080 / 1280x720 / 960x540（2560x1440 在"允许列表"里但**会被上采样规则拒掉**）；竖屏 1080x1920 / 720x1280 / 540x960 |
| 允许的 fps | 24 / 25 / 30 / 50 / 60 |
| 竖屏 | **支持（experimental）**：加 `--layout portrait`（render / propose / filmcheck 都有这个参数），画布切到 1080x1920，安全区与字幕带随之重算。10 类构图**全部竖屏原生**（stat/quote/chapter/hero/statement/list/outro 纵向重排，compare 上下、process 竖向轴、chart 竖排条） |
| 竖屏字号 | `layoutkit.TYPE_SCALE = 0.89`（横屏 1.0）：判据是「正文 ≥ `MIN_BODY_PX = 30px`」，不是几何比例——designkit 的 body 基准是 34px，纯几何缩放 0.5625 只有 ~19px；30/34≈0.882，取 0.89 把正文抬到 30px |
| 竖屏逐类停留 | `render._apply_type_hold`：chart +0.45 / compare +0.40 / process +0.40 / list +0.30 / stat +0.25 / chapter +0.15 / outro +0.15，hero/statement/quote 不加；总增量上限片长 6%。段变长会整体后移后续段并重算 total/frames。横屏零变化 |
| 竖屏输出比例 | 非 9:16 的输出**直接报错**；横屏片与竖屏片都只能同比例下采样 |
| 上采样 | **直接报错**。输出大于当前画布 = 把 1080p 包装成"更高分辨率"（假 4K）；要真高清得改内部构图 |
| 非法 fps | **直接报错**（报错里列出可用值） |

链路：`filmkit.video_plan(script)` 在 `layout` 阶段就解析并校验（错了不拖到渲帧），
结果写进 `timeline.json` 的 `video` 块；`G4` 容器门禁改成**按交付规格**比对（不是恒看 1920x1080）；
`encode_quality` 的参考帧会先缩到交付分辨率再比锐度。

> **跨分辨率别比锐度比**：同一片 1080p 交付实测 1.009、720p 交付实测 1.206——数值不可横向比较
> （参考帧也被缩到交付分辨率，笔画占画面的比例变了）。它只用来判"这一版有没有比上一版糊"，别拿它当"720p 更清晰"。
>
> 720p 交付已实测通过全部门禁：`G4 = 1280x720@24`、`G11 = VMAF 97.10 / SSIM 0.9925`（696 帧，7.2 s）。
> `filmkit.qa_report` 为此新增 `expect_w/expect_h/expect_fps`——容器门禁比的是**交付规格**，
> 不是死比 1920x1080；改规格时记得让 `timeline.video` 与之一致（qavmaf 会核对两者是否同源）。

### 7.5.2 批量生产（`--batch`）

```jsonc
// films.json —— 两种写法都接受
["a/film.json", "b/film.json"]
{"films": [{"script": "a/film.json", "scenes": "a/scenes.py",
            "work": "a/work", "out": "out/a.mp4", "label": "A"}]}
```

```bash
python render.py --batch films.json
```

| 行为 | 细节 |
|---|---|
| 上限 | **100 条 / 1 MiB**（清单本身不该是个大 JSON）；超了直接报错让你拆份 |
| 开跑前 | **全量 `filmcheck` 预检**：任一条不通过就**一条都不跑**，把明细一次性报全（`batch-report.json` 里 `phase="preflight"`） |
| 执行 | 逐条起**子进程**渲染（每条都是完整的 `--stage all`），**单条失败不阻断其余** |
| 收尾 | `batch-report.json`（`phase="render"`，含每条 `exit` / `wall_s` / 日志尾）+ 退出码：全成功 `0`，**部分失败 `1`** |
| 预检失败的退出码 | 也是 `1`（`SystemExit` 带文字，Python 的默认行为）；区别看 `batch-report.json` 的 `phase` |
| 逐条透传的开关 | `--jobs`（≠1 时）/`--resume`/`--force-tts`/`--force-music`/`--strict-stale`/`--allow-stale`/`--no-gate`/`--require-proposal`/`--strict-scenes`：这些对每条都成立，原样传给子进程 |
| 只能写进清单的开关 | `--out`/`--work`/`--script`/`--scenes`/`--note`/`--by`/`--verdict`/`--stills` 是**逐条**概念，命令行给了就报错（旧行为是静默忽略——写了以为生效） |
| `scenes` 缺省 | 清单条目没写 `scenes` 时**不强行补**"剧本同目录 scenes.py"：文件不存在就回落技能内置渲染器，与单跑一致（旧行为是子进程直接退出） |
| 撞车检测 | 两条的 `work` 或 `out` 解析到同一路径时**开跑前报错**（否则后一条会覆盖前一条的成片，两条却都报 OK） |

实测（本机，2 条 29 s 示例片）：**67.2 s / 60.1 s**，各 `pass 11 · fail 0 · skipped 1`。
注意每条都要付 G11 的参考帧成本——批量不是"摊销"，是线性叠加。**而且瓶颈已经转到 qa**：
单条全链实测 47.3 s 里 `qa` 占 23.9 s（见 §1 的时间表），批量时这个比例会原样复制。

### 7.5.3 预览片（`--preview N`）

```bash
python render.py ... --preview 15        # 只渲前 15 秒
```

- 产物：`out/preview/<片名>_preview.mp4` + 同名 `.gif`（480 px / 10 fps，两遍调色板生成）。
- 音轨：有 `work/audio.wav` 就**先截到预览时长再混入**。直接拿全长音轨去配短片，`mux` 会判
  "音视频时长差 ≥ 0.05 s" 而失败（开发期实测踩过）；没有音轨就出无声版，不阻断。
- **不落交付账本**：它是给人快速看效果的，不是交付证据；正式结论仍以 `--stage qa` 的报告为准。
- **只支持 PIL 引擎**：`--preview` + `--engine web` 直接报错（预览必须与成片同一条渲染路径，否则预览"好看"不代表成片好看）。
- **与单 stage 互斥**：`--preview` 只和 `--stage all`（或缺省）同用；写 `--stage qa --preview 15` 会报错，而不是静默只出预览。
- **秒数下限是一帧**：0、负数、`nan`/`inf` 一律报错（旧行为是渲 0 帧却退 0，等于假装成功）。

### 7.5.4 运行版本化（`--run-id` + `work/runs/`）

```bash
python render.py ... --run-id hero-v3     # 不给就按时间戳生成 rYYYYMMDD-HHMMSS
```

- `work/gates.json` 增 `runId` 与 `inputs`（**script / scenes / words / filmkit** 四个内容指纹）。
  为什么要逐项指纹：`contentHash` 只说"变了没"，说不出"变了哪个"；批量出片最常见的事故就是
  "这一条用的还是上一版的词级时间戳"。
- 每次收尾（`gates.archive_run`）把**账本快照 + QC-REPORT.md + run.json** 复制到
  `work/runs/<runId>/`。只归档小文件；视频/音频/分块体积大，就地保留。
- `out/QC-REPORT.md` 头部多两行「运行编号」「输入指纹」，交付时一眼能对上是哪一版跑的。
