# 更新日志

版本号规则：`主.次.修订`；技能正文（SKILL.md / references / scripts / assets）有实质改动才升次级。

## 0.3.0

> 这一版来自一次"把两个项目放在一起看"的复盘：本项目擅长**旁白驱动的图文科普片**（TTS 实测时间轴、
> 字幕安全区、质检门禁），而 `ai-history-film` 擅长**画面与声音本身**（浏览器逐帧渲染、程序化配乐、
> 物理建模音效、帧级质检）。v0.3.0 把后者**已经用一部 6 分半成片验证过**的能力，按本项目的约束重写进来。

### 新增能力（四个新模块 + 提示点系统）

- **`scripts/sfxkit.py` —— 物理建模程序化音效库**（v0.2 只有 `tone/pad_chord/noise_burst/whoosh` 四个函数）。
  24 个配方：界面/文字/转场/环境/撞击五类；模态合成（材质 × 形状 × 敲击位置 × 接触时间）、
  泊松/Cox 纹理事件、镜像声源混响、BS.1770 响度、块级压缩限幅。
  **scipy 可选**：没装 scipy 时所有 IIR 走"短冲激响应 + FFT 卷积"，长片不会卡死。
- **`scripts/scorekit.py` —— 程序化配乐**。一张贯穿全片的和弦表 + 分段能量（pad/bass/kick/hat/arp）+
  段落边界对齐小节线 + 重击前"吸气"（`suck`）。同 seed 逐采样一致。
- **`scripts/webengine.py` + `assets/web/` —— 浏览器逐帧渲染引擎**。
  v0.2 的 SKILL.md §3 推荐"浏览器 Canvas/WebGL 路线"但**没有任何代码资产**（文档承诺、代码没做到），
  v0.3 补上：Edge/Chromium + CDP 截图 → image2pipe、分块并行、断点续跑、缺口检测、崩溃重开、cue 导出、页面文字质检。
  页面用**原生 Canvas2D + 自写补间**，不 vendor GSAP / three.js（许可干净、体积为零）。
- **`scripts/qa_frames.py` —— 帧级/时间轴级深检**（18 项自测）。单帧闪烁
  （二阶差分 + 孤立性 + **"回到原位"双闸**，见下文集成期修正）、画面空窗（≥0.8 s 近黑）、
  音画同步（cue vs 视觉事件 dt）、亮度/响度曲线，出 JSON + PNG（PIL 手绘，不引 matplotlib）。
  这些是**唯一靠"看完全片"才能发现**的问题，单看任何一帧都正常。
- **提示点系统（cue）**：画面在"该响一下"的地方登记提示点（`film.json` 的 `sfx.cues`、
  `scenes.py` 的 `cues()` 钩子，或 web 页面的 `S.cue()`），音频阶段按 `work/cues.json` 合成音效轨。
  **改台词/加停留窗口/换 BPM 之后，音效不用重新对时间。**
- **三总线混音**：`filmkit.mix_film` 把旁白/配乐/音效分开处理，音效响起时配乐自动让位（`duck`），
  最后才交给 ffmpeg loudnorm。v0.2 是"配乐床 + 旁白"两路简单叠加。

### filmkit 的变化（向后兼容）

| 变化 | v0.2 | v0.3 |
|---|---|---|
| `AudioBus` | 只有单声道 | `channels=2` 立体声 + `pan_pos`；默认仍单声道 |
| `wav_write / decode_pcm` | 只支持单声道 | 自动识别单/双声道，新增 `wav_read` |
| `build_timeline` | 只有 lead/tail/min_dur | 新增 `hold`/`hold_map`/`bar`（停留窗口 + 小节对齐） |
| 混音 | `AudioBus.mix()` 一条总线 | `mix_film(voice, music, sfx)` 三总线 + `duck()` |
| 提示点 | 无 | `CueBus` / `cue_time` / `CUE_ALIASES` / `CUE_BASE_DB` |

**旧调用全部还能用**（selfcheck 第 13 条守着 v0.1 签名兼容）。

### render.py 的变化

新增 5 个 stage，`--stage all` 顺序变为：

```
tts -> layout -> cues -> score -> sfx -> audio -> frames -> mux -> qa
```

新增 `--engine pil|web`（默认 `pil`，老项目行为不变）、`--force-music`。
`qa` 阶段会顺手跑帧级深检（没有 `qa_frames.py` 就跳过，不阻断）。

### selfcheck：14 条 → 23 条

新增 9 条，全部对应"v0.3 新代码可能出的错"（另外每个新模块还自带 `--selftest`：
sfxkit 16 项、scorekit 17 项、webengine 18 项、qa_frames 18 项，全部不联网、合计约 40 秒）：
立体声总线形状与声像、`cue_time` 三种写法、`CueBus` 落盘/读回/别名展开/越界丢弃、
`duck` 真的压低了配乐、`mix_film` 时长与峰值、以及四个新模块的存在性/确定性/契约检查
（模块缺失标 SKIP，不影响只用 PIL 路线的人）。

### 文档

- SKILL.md：§5 新增"声音：配乐 + 音效 + 提示点"、§6 新增"停留窗口"、
  §3 引擎表补上浏览器路线的真实用法与取舍、§7 质检门禁分四层（容器/信号/**时间**/页面）并给出 `deep.json` 判读表。
- 新增 references：`sfx-design.md`（音效设计流程 + 诊断表）、`web-engine.md`（页面契约 + 渲染器实现要点）、
  `frame-qa.md`（闪烁/空窗/同步判据）。
- `references/audio.md` 新增 §10 提示点系统、§11 程序化配乐、§12 程序化音效。
- `assets/film.json` 增加 `engine/bpm/hold/music.mode/sfx.cues/web` 字段；
  `assets/scenes.py` 增加 `cues()` 提示点钩子示范。

### 集成期发现并修掉的问题（都用实测定位，不是猜的）

| 现象 | 根因 | 修法 |
|---|---|---|
| `--stage cues` 在"剧本改短、cue 还指向老场景 id"时整个炸掉 | `CueBus.extend` 直接抛 `ValueError` | 逐条跳过并汇总 WARN；**全部落位失败才报错**（"少了两个提示点"和"一个都没落上"不是一回事） |
| 浏览器引擎的片子被帧级质检报 3 处"单帧闪烁" | 二阶差分分不清"单帧闪烁"和"加速运动最快的一帧"（3.7/7.7 s 是交界黑场起坡，0.733 s 是最后一个字入场） | `qa_frames` 加"回到原位"闸门：`|F[t+1]-F[t-1]|` 必须 ≤ 1.6×中间帧偏离量；两个演示片 flicker 归零，注入白闪的用例仍 PASS |
| 音画同步报 -0.40 s / -0.32 s 超差 | 跨 kind 的"最近事件"被当成"预期事件"（闪白去配 data_chirp） | 异 kind 只在 `|dt| ≤ tol` 时作佐证，不再据此判超差 |
| `scorekit` 写的 24-bit WAV 读不进来 | `filmkit.wav_read` 只支持 16-bit | 支持 16/24/32-bit PCM（含 24-bit 符号扩展） |
| 场景分块改了画面却不重渲 | 分块只按"文件存在"跳过 | 这是**已知语义**：改画面必须 `rm -rf work/chunks`（已在 SKILL 与困境速查里写明） |

### 帧级质检的两条已知盲区（**故意的取舍，不是遗漏**）

写进这里是因为它们都会让"门禁看起来很干净"，而这正是最危险的失败模式。

1. **"回到原位"闸会放过快速运动段里的真实单帧闪**：如果前后两帧因**运动**产生的差异
   大于 1.6×闪烁幅度，真实闪烁会被判成运动。缓解：分数 ≥10 却被拒的候选**不进 error、但进 warning**
   （`高分异常被判为运动（建议人工复核）`），命令行摘要也会打"高分异常待复核 N"。
   **原则：门禁可以保守，但不能静默。**
2. **音画同步检查依赖命名约定**：`events.kind` 与 cue 配方名不同名时，同 kind 配不上、
   跨 kind 只做确认，`sync` 会恒为空（看起来"零超差"，其实一条都没在查）。
   约定与自查方法写在 `references/frame-qa.md` §2.5：**故意把 cue 改错 0.3 s，必须能报出来**。

### 许可

新增 `THIRD_PARTY_NOTICES.md`：sfxkit / scorekit / webengine / qa_frames 中**算法思想**改编自
`ai-history-film`（MIT）；本项目未复制其文件、未引入其三方依赖（three.js / GSAP 均未使用）。

## 0.2.0

> 这一版全部来自一次 **11 分钟真实成片** 的复盘：用 0.1.0 做出了《817 → 16 → 1》，
> 过程中发现 10 处"文档承诺了、代码没做到"的地方，逐条修掉并补了回归护栏。

### 修掉的实质缺陷（每条都曾真的造成误导）

- **stage 假独立**：`--stage mux` / `--stage qa` 都会重渲全片（qa 甚至会把成片重编一遍）。现在 7 个 stage
  真正各做各的，qa 是纯只读。
- **TTS 缓存不校验文本**：只判断 mp3 存在，改一句台词会静默复用旧音频。现在缓存 key 是
  text+voice+rate+pitch 的 sha1，变了就重合成；另加网络重试 4 次与尾静音裁剪。
- **字幕窗口硬钳 5.2s**：10 秒长句读到一半字幕就消失。现在默认覆盖整句（`end-0.06` 封顶），要钳短显式传
  `sub_max_hold`。
- **真峰门禁是摆设**：`ebur128` 没开 `peak=true`，解析不到 Peak，"真峰 ≤ -1 dBTP" 永不触发。
- **锐度比恒 ≈ 1.0**：参考帧用成片自己抽的帧，等于自己跟自己比。现在参考帧由场景函数重新渲染。
- **mux 二次编码**：对已 CRF18 编好的 silent.mp4 又编一遍。现在 `-c:v copy`，只在 `reencode_video=True` 时重编。
- **码率当门禁**：暗底大字 0.5–1 Mbps 是正常值，却被报成错误。现在只做参考，真门禁看锐度比。
- **FrameSink 静默 resize**：场景写错分辨率会被悄悄缩放。现在 `strict=True` 直接报错。
- **缺字形无人发现**：`✓ × ↵` 在 msyhbd 里不存在，会被画成方块。新增 `glyph_missing_chars` /
  `check_glyphs`，layout 阶段自动扫剧本。
- **分块拼接路径错**：concat.txt 里写相对路径，ffmpeg 按自身 cwd 解析会拼成 `work2/work2/...`。现在写绝对路径。
- **音轨变成 96 kHz**：mux 没指定 `-ar`，loudnorm 内部升到 192 kHz 后 AAC 取了 96 kHz。用 11 分钟成片重合成复现，现在显式 `-ar 48000`。

### 新增能力

- `scripts/selfcheck.py`：14 条回归断言（真峰、ASS PlayRes、尾静音裁剪、缺口检测、缓存 key…），
  不联网 10 秒出结论，换机自检用。
- 分块并行渲染 `--jobs N`（按**绝对帧号**命名、显式缺口检测、`--resume` 断点续跑）。
- 母版预览 `--stage stills --stills "l01:3,l02:5"`：只渲关键帧 + 联系表，P6 不再靠手写脚本。
- `K.write_ass`：直出 ASS，头里声明 `PlayResX/Y=1920/1080`，避免 libass 按 288 排版。
- `K.AudioBus.music_bed`：连续配乐床（和弦交叉淡化），替代逐段硬拼。
- `K.wrap_cn_cached` / `text_w_cached`：场景每帧排版走缓存，**实测单帧 12.3ms → 4.6ms**。
- 预烘焙背景（渐变+暗角 + 柔边圆点精灵池）：再省约 5ms/帧。
- `qa_report` 新增字幕条数校验、音轨采样率校验；`notes`/`expected_subtitles` 参数。

### 模板与文档

- `assets/scenes.py` 重写：示范缓存排版、预烘焙背景、精灵池、HUD 的正确定时方式。
- `assets/film.json` 增加 `trim_silence` / `tts_workers` / `sub_max_hold` / `chunk_frames` / `reencode_video`。
- SKILL.md：§1 上手加自检/母版/并行；§5 门禁表重写（真峰/字幕条数/缺字形）；§6 故障速查补 7 条实测坑；
  新增 §11 升级须知、§12 换机 60 秒自检。
- references：quality-gates 的锐度比示例改用 `ref_img`；pipeline 更新 stage/mux；audio 补 TTS 缓存与裁剪。

### 兼容性

`filmkit` 公共 API 保持向后兼容（`tts`/`build_timeline`/`sub_window`/`mux`/`qa_report` 旧参数仍可用），
但默认行为有变，迁移项见 SKILL.md §11。