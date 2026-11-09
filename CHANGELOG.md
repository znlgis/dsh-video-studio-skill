# 更新日志

版本号规则：`主.次.修订`；技能正文（SKILL.md / references / scripts / assets）有实质改动才升次级。

## 0.5.0 — 风格包：让每部片的画风 / 配乐 / 旁白都不一样

> 这一版解决的是**同质化**，不是加功能。用户的原话：
> 「背景音乐风格都差不多、旁白声音都差不多、整体画风也差不多，希望随内容变化，生动活泼不死板。」
>
> 根因不是"参数不够多"，而是**身份参数全是模块级常量**：画风锁死在 scenes.py 的 THEME、
> 配乐锁死在 scorekit 的 Dm/120、旁白锁死在 render.py 的单个 voice，而 SKILL.md 教的流程
> 是"拷模板 -> 只改 lines[].text" —— 内容换了，气质不可能换。

### 新增：风格包（style packs）

| 层 | 之前 | 现在 |
|---|---|---|
| 画风 | 深蓝底 + 橙，3 个写死的绘制函数 | 8 套主题（8 组配色/材质/字体族/版式/HUD）+ 3 种版式骨架 |
| 配乐 | Dm / 120BPM / 3 套进行 / 固定动机 | 8 套配乐风格（调池/BPM 梯度/进行/动机/配器/鼓型/空间）+ 大调与七和弦 |
| 旁白 | 全片一个 voice/rate/pitch | 9 个 persona × 6 档 tone，按 kind 与文本规则自动切换 |
| 节奏 | 全局 lead/tail/min_dur | 每个风格一套 pacing，逐句可覆盖 |

- **一个 style id 同时决定三者**（不允许各配各的，否则会"极简黑白画面 + 史诗管弦"）。
  内置 `tech-deep / magazine / paper / vivid / mono / cinema / lo-fi / epic`。
- `"style": "auto"` 按内容自动选：关键词命中即锁定，零命中按指纹稳定落桶。
- **三级内容指纹**（full/tempo/style）：改一个错别字不会换整首曲子，换一部片三个都变。
- 片内也有变化：版式按章节轮换、镜头缓推、入场位移、配器渐入渐出、主奏轮换、语气档切换。

### 新增文件

- `scripts/stylekit.py`（909 行，纯标准库）：8 套主题 + 8 套风格包 + 内容指纹 + 对比度门禁 + CLI。
- `scripts/voicekit.py`（872 行）：9 个 persona + 6 档 tone + 逐句 voice_plan + 实测响度校准。
- `references/style-packs.md`：字段表、指纹规则、加第 9 套风格、常见误用。

### 修掉的两个静默 bug

| 现象 | 根因 | 修法 |
|---|---|---|
| 把 `trim_silence` 改成 false，旁白仍是被裁过的 | 缓存 key `sha1(text|voice|rate|pitch)` 不含 trim，旧 meta 的 trimmed=True 仍命中 | 升 v2 key（含 volume/trim/trim_db）；v1 key 保留给老缓存 |
| `voice_gain` 一调就偏（1.2 变成 1.44） | 在 `_voice_track` 和 `mix_film` 里**各乘一次** | 全局增益只在 mix_film 乘一次；逐句 gain 只在 voice bus 乘一次 |

### 门禁与回归

- **新增对比度门禁**：`stylekit.theme_lint()` 在 `--stage style` 就拦
  （ink≥7 / muted≥4.5 / dim≥3 / accent≥4.5 / bg-bg2 亮度差≥0.002）。
  8 套主题全部实测通过；v0.4 全仓 `grep 对比度` 是 0 处命中。
- **selfcheck 24 → 29 条**：新增风格层一致性、legacy 精确兼容、指纹确定性、声线计划、
  字体族与主题字幕 5 条。
- **legacy 逐字节兼容**（实测，不是承诺）：`scorekit.render(style=None)` 的 PCM sha256
  与 v0.4 完全一致（两个用例：12s 无锚点 + 10s 带锚点）；`assets/scenes.py` 的 hook/body/outro
  三帧 sha256 与 v0.4 一致；没问题就是没问题，不是"应该一样"。
- **性能**：styled 配乐比 legacy 慢 2–3 倍（24s 约 5.2s、60s 约 15–25s），仍在原预算内。

### 升级须知（v0.4 -> v0.5）

1. **老项目不用改**：film.json 里没有 `style` 字段就自动走 legacy，配乐逐样本一致。
2. 想用新特性：加 `"style": "auto"`（或一个风格 id）；模板 film.json 已经是 auto。
3. 新工作目录要多拷两个文件：`scripts/stylekit.py`（必需）、`scripts/voicekit.py`（可选）。
4. `--stage` 多了一个 `style`，且 `all` 的顺序变成 `style -> tts -> ...`（style 必须先跑，它决定声线）。
5. 改过 `trim_silence`/`voice_gain` 的项目，第一次跑会重新合成 TTS（key 变了），属预期。

## 0.4.0

> 这一版不是加功能，而是**修"门禁看着干净、其实没在查"**。上一版（0.3.0）把四条质检腿都补齐了，
> 但复盘时发现其中三处在**默认配置下根本不会失败**——这比没有门禁更危险。
> 同时把仓库从"一个人本机能跑"提升到"任何人都能验"：仓库级体检、CI、依赖清单、打包卫生。

### 修掉的静默失效（每一条都先复现、再修、再写回归断言）

| 现象 | 根因 | 修法 |
|---|---|---|
| 改完台词 `--resume` 拼出的片子**画面和旁白对不上**，且时长门禁照样通过 | 分块只按"文件存在"skip，不校验时间轴/场景/编码参数是否变过 | `work/segments/_plan.json` 存计划指纹（timeline+scenes.py sha1+剧本 sha1+crf/preset/chunk）；指纹不符时**拒绝拼接**并提示删目录。实测旧代码在 t=10 s 处有 27305 个像素差异却 EXIT=0 |
| 把 `music.mode` 改成 `off`，**旧配乐还在响** | `st_audio` 无条件读 `work/music.wav`；`st_score` 甚至打印"沿用已有 music.wav" | 新增 `K.music_plan()` 统一判定 8 种来源（score/file/bed/off/none/missing-file/missing-bpm/unknown-mode），score 与 audio 共用；off 时既不读也不删 |
| PIL 路线 `sync: []` 看着"零音画差"，其实**一条都没在查** | 同步门禁依赖页面导出的 `events`，PIL 路线没有 | cue 支持 `visual_at` 锚点自校验 + `QA_REQUIRE_SYNC=1` 把"没查"升级为 error + `sync_info.judged` 暴露查了几条 |
| 自带模板片报 4 段"亮度长时间无变化"，画面其实一直在动 | 判据用全帧平均亮度，对局部动画天生不敏感 | 换成"最活跃 8×8 块在 0.5 s 内的漂移"，阈值 1.5 级灰度；模板片 4 段 → 0 段，合成"真冻结 6 s"仍能报出 |
| Windows 管道里 `render.py \| tee` 的中文全是乱码 | stdout 在非 tty 时是 GBK，且 render/selfcheck 没有像其它模块那样切 UTF-8 | `_setup_stdio()`：仅非 tty 时 reconfigure 到 UTF-8，真控制台保持系统编码 |
| 跑一次 `--stage qa` 就在仓库里留一个 250 KB 的 `_dec_tmp.png` | 锐度比的解码帧写到了 cwd | 写进系统临时目录 |
| 锐度比偶尔把"不同时刻的参考帧"拿来比 | 部分参考帧渲染失败时仍用 `refs[0]` | 参考帧按时刻配对，并补 `_total` 与 frames 阶段一致 |

### 新增的"会失败"（不是新玩具）

- **浏览器确定性开关**：`--deterministic` / `WEBENGINE_DETERMINISTIC=1`。实测默认参数下
  **同一会话内**字节一致、**跨会话**有 ≤4 像素 ±1 级抗锯齿差异；开关打开后 8/8 会话字节一致，
  代价约 +22%/帧。文档相应从"同帧两次截图 byte 完全一致"改成带条件的说法。
- **`open_page` 契约检查**：缺 `window.ready` / `renderFrame` 时不再是裸 Playwright 超时，
  而是带上页面异常、控制台错误与契约快照的可操作报错。
- **`qa_text` 契约修正**：`frames` 字段改为真实采样帧数（原来放的是累计文本实例数），
  另加 `texts`；CLI `textqa` 对"重叠/出画/一闪而过/从没露面"四类都 `exit 1`（原来只看前两类）。
- **sfxkit 参数健壮性**：枚举值写错从"一个硬崩、一个静默"统一成明确报错（`_enum()`：
  `wind(env)` / `ui_blip(kind)` / `text_stream(voice)` / `whoosh(direction)` / `riser(end)` /
  `knock_wood(door)` / `metal_hit(obj)` 全部收口）；`sub_drop(start_hz=0)`、负/非整数 seed、
  `relay_click(gap<0)`、`typing_at(times` 含负数/NaN`)`、未知 `room` 都从裸
  `ZeroDivisionError`/`KeyError`/静默哑音改成可读 `ValueError`。
- **无 scipy 的性能门禁分档**（`PERF_BUDGET_SCIPY=1.5` / `PERF_BUDGET_FALLBACK=3.5`）：
  两条实现路径**不共用同一个数字**，自检输出里明写"当前无 scipy，按 fallback 预算判定；
  装 scipy 可加速并回到 1.5 s"——修掉"没装可选件 = 自检失败"这个假红灯，但没有偷偷放宽。
- **`film.json` 前置校验**：坏 JSON / `lines` 为空 / id 重复 / bpm 非法 / music·sfx·web 类型错，
  全部给一句能照着改的错，而不是 Python traceback；顶层加 `except` 兜底（`RENDER_DEBUG=1` 看栈）。

### 仓库工程化

- **`tools/ci_check.py`（9 项，不需要 ffmpeg/字体/网络）**：py 语法、`node --check`、JSON、
  SKILL.md frontmatter、三方依赖白名单、`npm pack` 打包卫生、文档路径引用、Python 3.9 语法、单文件体积。
- **`.github/workflows/ci.yml`**：`repo` job 在 ubuntu+windows 跑 ci_check；`skill` job 在 ubuntu
  装 ffmpeg + `fonts-noto-cjk` 后跑 `selfcheck.py` 与可选模块 selftest。
- **`skills/video-studio/requirements.txt`**：依赖分"必需 / 强烈建议 / 可选"三档并写明缺了会怎样。
- **打包卫生**：`files` 增加 `!skills/**/__pycache__` 与 `!skills/**/*.pyc`；补 `publishConfig.access`、
  `engines.node`、`homepage`、`bugs`。实测修前包内混入 `__pycache__`（922 KB），修后 431 KB / 35 文件。
- **插件入口不再用静态 `import` 引可选 peer**：改成 `apply` 内按需 `import`，缺依赖时只降级
  （不挂载 bundled 技能根）并打一条能照着做的日志，不让整行插件加载失败。

### selfcheck：23 条 → 24 条

新增第 24 条「配乐来源判定」：11 条 `music_plan` 断言 + 真跑一次 `st_audio` 验峰值
（`mode=off` 混音峰值 0.000 / `mode=file` 0.673）。模块自测同步扩到：
sfxkit 16、scorekit 17、qa_frames 22、webengine 24。

### 文档

- SKILL.md：`--stage qa`"零写入"改为"只读输入、只写 `work/qa/`"；分块目录 `work/segments/` 与
  默认 chunk 计算方式写清；新增"PIL 路线的同步门禁怎么真的生效"与"画面长时间静止"判据；
  硬约束补确定性模式；实测表修正"同帧 byte 完全一致"的适用条件。
- references/frame-qa.md 新增 §2.4b（静止判据实测分布与调阈值方法）、§2.5 补 v0.4 两条补救；
  web-engine.md 修掉指向不存在的 `assets/web/fonts/`；pitfalls.md 的分块命名改为真实格式。
- THIRD_PARTY_NOTICES：edge-tts 许可 GPL-3.0 → **LGPLv3**（实测元数据分类器）；Python 版本声明
  补"语法已验证、运行期未实测"的边界。

### 文档"真话化"（对账 100+ 条承诺 vs 实现）

这一轮专门做了一次**文档逐条对账**（读代码 + 跑命令，不是读文档推断），修掉三类问题：

**A. 抄了就失败（最高优先级）**

| 位置 | 问题 | 修法 |
|---|---|---|
| quickstart 第 4 步 `--stage stills` | 干净目录必失败（`没有 work/timeline.json`，实测 EXIT=1） | 代码侧让 `stills` **自动补 tts/layout**（会打印在补哪一步）；文档同步说明 |
| `audio.md` `trim_tail(...)` | 本仓库没有这个函数（实际 `trim_silence`） | 换成正确签名 |
| `SKILL.md` 的 `find ~ -path '*/skills/video-studio/filmkit.py'` | 少 `/scripts/`，永远匹配不到 | 补全路径 |
| `pipeline.md` 字幕公式 `min(..., voice_start + 5.2)` | 5.2 s 硬钳 v0.2 就移除了 | 更新公式，删"上限 5.2 s" |
| `pipeline.md` 注释写"只渲 3 帧"却给 `--stage frames --jobs 4` | 照抄会白渲全片 | 改成 `--stage stills` |

**B. 门禁口径不符（影响"能不能交付"的判断）**

- SKILL §7 那张表 8 行里 **5 行实际只出 warning**（48 kHz、长黑帧、字幕安全区、缺字形、画面静止），
  标题却写"交付前必过"。已拆成 **error 档 / warning 档两张表**，逐行标注级别。
- 响度「-16 ± 1 LUFS」既不是 error 带（< -19 / > -12）也不是 warn 带（< -17.2 / > -14.5），
  改成写真实两级阈值，并说明"±1 是目标区，不是判定阈值"。
- 音画同步行补上"**默认可能根本没在查**"，并指向新增的 `visual_at` / `QA_REQUIRE_SYNC` 两条补救。

**C. 描述了不存在的实现 / 内部矛盾**

- §5.5「母带压缩 threshold -9 / ratio 1.5」：**代码里没有压缩器**，链路只有 求和→duck→峰值保护→loudnorm。已改为如实描述。
- §5.5「旁白比配乐高 12 dB」「配乐比音效低 1.5 dB」：代码不测量也不保证，改标"人耳经验值 / 默认增益"。
- `audio.md` §3「各轨目标 LUFS + 前瞻限幅器」：本仓库没实现，标为"设计建议"并给出实际链路。
- `quality-gates.md` G3 音频判据与 `sfxkit.qc()` 实际判据不同 → 改为以 `qc()` 为准。
- `SUB_MAX_CHARS=24` 定义后**无任何代码引用** → 文档改为"像素宽度折行为准，24 字是大致上限"。
- `scene-authoring` 的「正文安全区到 974，天然满足 880 规则」**是错的**（SAFE 974 与字幕带 872 重叠 102 px）→ 改为"正文实际止于 ~860；SAFE 是硬边界不是排版建议"。
- 「120 BPM 的八分音符 = 0.5 s」算术错（八分是 0.25 s）→ 改为四分音符。
- `CHANGELOG` v0.3「新增 5 个 stage」实为 3 个；qa_frames「18 项」实为 19 项；§11/§12 编号漂移 → 全部校正。
- `README`「SKILL.md 第 10 节是实测基准」→ 实为 §12；stage 列表补全为 10 个。
- `web-engine.md`「可以按段混用两条路线」→ render.py 的 engine 是全局单选项，不支持按段切。
- `scene-authoring` 的 JPEG q94 数据属**上游经验**，本仓库固定 PNG → 标注来源与适用范围。
- `sfx-design.md` 引用不存在的 `MODAL` 符号 → 改为可指认的"模块顶部模态区"。

**D. 同一事实多套数字 → 统一成一个权威值 + 条件**

浏览器引擎速度曾有 4 处互斥数字（190–400 ms/帧 / 3–6 fps / 82 ms/帧 / 70–120 ms/帧），
统一为「80–130 ms/帧（核显、PNG、空载）」并注明条件；PIL 统一「40–80 fps（模板片 68–82）」；
scorekit 389 s 统一「4–8 s（随负载）」；sfxkit 389 s 环境声标出区间与"rain 余量最小"。
**原则：区间可以宽，但同一个事实只能有一套。**

### selfcheck / ci_check 之外新增的两道静态检查

- `ci_check` 从 9 项扩到 **10 项**：新增 **Markdown 围栏平衡**（这次真的发现
  `quality-gates.md` 里有个被反斜杠转义、从未闭合的围栏，会让后面整篇渲染成代码块）。
- `cv2` 从"文档说可选、代码硬 import"改成**真的可选**：缺 cv2 时 `sharpness`/`encode_quality`
  返回 `None`，`qa_report` 记一条**明确说明"锐度比门禁本次未执行"**的 warning，
  而不是抛 `ImportError` 崩掉整个 `--stage qa`。

### 已知局限（写在这里，因为门禁干净时最容易忽略）

1. **锐度比在 web 引擎路线仍是跳过**（没有参考帧），只打 WARN。web 路线要查锐度，得先让
   `webengine` 提供一个"渲染单帧并截图"的参考帧接口。
2. **`_peak_bound_ratio` 不是严格上界**（实测 300 组输入有 14 例超出，最坏 0.8 dB），
   当前靠 `finalize` 的第二道真峰校验兜底；36 次真实渲染未发现越限。
3. **`sync` 依赖命名约定或 `visual_at` 锚点**：两者都不写时，`sync` 恒空但会有 warning 明示
   "没在查"。看到 `sync: []` 请先看 `sync_info.judged`。
4. **跨机器性能数字只是参考**：所有 fps / 耗时都标注了机型，换机须重测（SKILL.md §12）。

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
- **`scripts/qa_frames.py` —— 帧级/时间轴级深检**（当时 19 项自测，v0.4 扩到 22）。单帧闪烁
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

新增 3 个 stage（`cues` / `score` / `sfx`，v0.2 的 7 个变 10 个），`--stage all` 顺序变为：

```
tts -> layout -> cues -> score -> sfx -> audio -> frames -> mux -> qa
```

新增 `--engine pil|web`（默认 `pil`，老项目行为不变）、`--force-music`。
`qa` 阶段会顺手跑帧级深检（没有 `qa_frames.py` 就跳过，不阻断）。

### selfcheck：14 条 → 23 条

新增 9 条，全部对应"v0.3 新代码可能出的错"（另外每个新模块还自带 `--selftest`：
sfxkit 16 项、scorekit 17 项、webengine 18 项、qa_frames 19 项，全部不联网、合计约 40 秒）：
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
| 场景分块改了画面却不重渲 | 分块只按"文件存在"跳过 | 这是**已知语义**：改画面必须清掉分块目录（0.3.0 时靠人记；**0.4.0 起改为计划指纹 + 显式拒绝拼接**，见上） |

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
  新增「升级须知」「换机 60 秒自检」两节（v0.2 时编号为 §11/§12，v0.3 起后移为 §13/§14）。
- references：quality-gates 的锐度比示例改用 `ref_img`；pipeline 更新 stage/mux；audio 补 TTS 缓存与裁剪。

### 兼容性

`filmkit` 公共 API 保持向后兼容（`tts`/`build_timeline`/`sub_window`/`mux`/`qa_report` 旧参数仍可用），
但默认行为有变，迁移项见 SKILL.md §13。