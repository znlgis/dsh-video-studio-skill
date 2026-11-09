# 更新日志

版本号规则：`主.次.修订`；技能正文（SKILL.md / references / scripts / assets）有实质改动才升次级。

## 0.6.0 — 交付可信化：让"我查过了 / 我签过了 / 改一句要重跑什么"都能拿证据

> 这一版不加生成能力，加的是**可追责性**。起因是一次对照：把本项目与另一个成熟插件
> （本地 ComfyUI 短剧工作室）逐项对比，发现对方的执行层本机完全跑不动，
> 但它的**编排层**——"人怎么被叫住""改一句要重跑什么""这笔账记在哪"——写得极细，
> 而这三件事在本项目里全是散文承诺。
> 对照后确认了五处"文档承诺了、代码没做"，本版全部补上并补了回归断言。

### 修掉的五处「承诺 vs 实际」

| 现象 | 根因 | 修法 |
|---|---|---|
| `out/QC-REPORT.md` 在 G6 交付清单里被点名，**全仓 grep 只有那一处命中，没有任何代码写它** | 质检结论只活在 stdout | 新增 `gates.py` 门禁账本 + 报告渲染，`--stage qa` / `--stage all` 收尾自动写 |
| "先确认风格""亲眼看 3 张静帧"全是散文，**换会话就无法证明看过哪一版** | 没有签核账本 | `--stage accept --gate style\|stills\|facts\|release` 写 `work/signoff.json`，绑内容指纹 |
| 除了 `frames` 的分块指纹，**其余 stage 全裸奔**：改 `style` 后 audio 仍读旧 `music.wav`、改 `hold` 后 frames 仍用旧 `cues.json`，且都不报错 | 只修过一个点 | 新增 `stagewatch.py`：每 stage 记输入/输出指纹，`--stage explain` 报过时 + 给最小重跑命令 |
| `SUB_MAX_CHARS = 24` 定义后**零引用**（SKILL `4 自己承认"别指望它拦你"） | 常量接了没人调 | 由 `filmcheck` 与报告矩阵接管超长字幕/契约类判据（见下） |
| `film.json` 未知键**静默忽略**：`hold→holds` / `kind` 拼错 / `cues[].at→time` 全都"跑得通但效果没了" | 只有 Python traceback 兜底 | 新增 `schemas/film.schema.json` + `filmcheck.py`（未知键给"是不是想写 X"），接进 `ci_check` 第 12 项 |

### 修掉一个**已发布版本里的功能性 bug**

`--jobs >1` 的并行渲染**一直是崩的**——而 SKILL/README 都在教用户用它。

- **形状**：worker 从 `work/_script_snapshot.json` 读剧本，JSON 把主题色的 `(r,g,b)` 元组变成 `list`；
  `scenes._bg()` 拿 `th["bg"]` 当缓存键 → `TypeError: unhashable type: "list"`。
- **为什么一直没被发现**：默认 `--jobs 1` 在父进程内渲染，不经过快照反序列化，所以单进程永远正常。
- **修法**（根因 + 防御）：worker 反序列化后**按剧本重解风格**（纯函数，拿回元组，也保证父子主题逐字段一致）；
  `scenes.py` 的 `_bg` 缓存键加 `_hashable()` 冻结兜底（它是用户会拷走改的模板）。
- **回归**：`selfcheck` 第 32 条断言"JSON 往返后重解必须回到 tuple"，并验 `_hashable` 可哈希。

### 新增

- **`scripts/gates.py`（约 440 行，纯标准库）**：`GateLedger`（pass / fail / skipped 三档）、
  `out/QC-REPORT.md` 渲染（gate matrix + 实测值 + 签核 + 耗时台账 + 结论话术）、
  `signoff` 读写与内容指纹校验、`append_cost` / `read_cost` 耗时台账、`atomic_write_json`。
- **`scripts/stagewatch.py`（约 260 行）**：`STAGE_IO` 依赖表、指纹（小文件 sha1 / >8 MB 走 size+mtime）、
  快照、`check` / `explain` / `report`。
- **`scripts/filmcheck.py`（656 行）** + **`schemas/film.schema.json`（528 行）**：draft-07 子集手写校验，
  未知键 WARN 带拼写建议、枚举/范围 ERROR、跨字段（cue 引用的 `l0x` 必须存在、hold+语音预估与 timeline 对账）。
  `style` 枚举从 `stylekit.STYLE_IDS` **单向读取，不复制**；stylekit 缺失时降级 WARN 不报错。
- **`tools/estimate.py`（578 行，纯标准库）**：开渲前算帧数 / 各阶段耗时 / 磁盘峰值 / 分块建议，
  `--json` 可机读。基准常量集中在文件顶部，换机请重测回填。
- **`references/change-impact.md`**：改了 `text` / `sub` / `hold` / `kind` / `style` / `scenes.py` / `crf` / `engine`
  分别失效什么、最小重跑什么、成本量级，外加三个真实犯过的错误做法。

### 命令面变化（全部向后兼容，老项目不用改）

| 新增 | 作用 |
|---|---|
| `--stage accept --gate <闸> [--note] [--by]` | 人工签核一道闸（`style / stills / facts / release`） |
| `--stage explain [--json]` | 哪些阶段过时了、为什么、最小重跑命令 |
| `--strict-gates` | 把 `skipped` 升级为失败（对齐 `QA_REQUIRE_SYNC` 语义） |
| `--strict-stale` / `--allow-stale` | 阶段过时：失败 / 显式放行（放行会留痕） |
| `--no-gate` | 跳过签核检查（**会在 QC 报告里留痕，不算通过**） |
| `--cost` | 每个 stage 往 `work/cost.jsonl` 记一行实测墙钟 |

### 门禁与回归

- **selfcheck 29 → 32**：新增「30 门禁账本与签核」「31 阶段新鲜度守卫」「32 并行渲染主题序列化」。
- **三个变异测试确认断言真的会失败**（不是橡皮图章）：
  把 `skipped` 当通过 → 第 30 条红；签核不绑指纹 → 第 30 条红；`stagewatch` 永不报过时 → 第 31 条红。
- **ci_check 11 → 12**：新增「film.json 契约」，直接调 `filmcheck.py`（校验规则只留一份，不在 CI 里重写）。
- 依赖白名单补 `gates / stagewatch / filmcheck`（同目录本地模块，不是三方依赖）。

### 实测（本机，2026-11-09）

- 模板片全链 `--stage all --jobs 2 --cost`：**门禁 pass 7 / fail 0 / skipped 1**（skipped = 人工签核未做），
  `work/manifest.json` 记录 9 个阶段，`--stage explain` 干净，`out/QC-REPORT.md` 正常生成。
- `--jobs 2` 修复前后：修前 `TypeError: unhashable type: "list"` 直接崩；修后 870 帧两段并行正常出片。
- `python selfcheck.py` → PASS 32 / FAIL 0 / SKIP 0；`python tools/ci_check.py` → PASS 12 / FAIL 0。

### 已知限制

1. `estimate.py` 是**预演**不是实测：没有 `work/timeline.json` 时按 4.2 字/秒粗估（±10–20%），
   `layout/cues/audio/sfx` 四行是经验模型；`--jobs` 按 12 核封顶线性折算。
2. `stagewatch` 的大文件（>8 MB）走 size+mtime 快路径，**内容变了但大小与 mtime 都没变**的情况查不出
   （代价考量：1080p 的 `audio.wav` 每 stage 算 sha1 会让 QA 慢一倍）。
3. `filmcheck` 的 cue `type` 不校验 sfxkit 配方名（配方表会演进，硬编码会误杀），
   timeline 对账是启发式，只 WARN。
4. 签核的证据检查是**"产物存在"级别**（如 `stills` 要求 `work/qa/sheet.png`），
   无法证明人真的看了——本会话拿不到会话记录钩子，退化方案到此为止。
5. `ffmpeg -version` 只能证明"能跑"，不能证明有 `xfade` / `drawtext`；
   缓解是把来源写进 `ffmpeg_source()` 并在探测顺序里把便携/WinGet 排到 PATH 之前。

### 升级须知（v0.5 → v0.6）

1. **必需**多拷两个文件：`scripts/gates.py`、`scripts/stagewatch.py`——
   `render.py` 直接 import 它们，少一个就是 `ModuleNotFoundError`。
2. 老项目照旧能跑：不写新字段、不加新开关 = v0.5 行为。首次跑会多出
   `work/manifest.json` / `work/gates.json` / `out/QC-REPORT.md`，都是新增文件。
3. `--stage qa` 的退出码语义不变（仍按 error 判定）；只有显式加 `--strict-gates` 才会因 `skipped` 失败。
4. 想用长片并行：现在可以真的用 `--stage frames --jobs 4` 了（修之前会崩）。

## 0.5.1 — 修 CI：selfcheck 第 29 条把"系统装了几族字体"当成产品缺陷

> 症状：`skill selfcheck (Linux)` 红，Windows 的 repo check 绿。
> 根因：新增的 case 29 断言"四个字体族量出来的宽度必须不同"，但 GitHub 的 ubuntu runner
> 只装 `fonts-noto-cjk`，没有雅黑/宋体/楷体 —— 四个族全部回退到同一个 `NotoSansCJK-Bold.ttc`，
> 宽度当然一样。**断言写死了本机的字体环境**，产品代码本身没问题。

### 修法

把 case 29 拆成两层，各验各的：

| 层 | 验什么 | 依赖环境吗 |
|---|---|---|
| 机制（必过） | 字体缓存键必须按 family 分键（否则 wrap 与绘制会用不同字体，文字溢出安全区） | 不依赖 |
| 语义（有条件） | 仅当系统**真的解析出 ≥2 个字体文件**时，才要求"宽度必须不同" | 依赖 |

单族系统（CI 的 fonts-noto-cjk、极简 Linux 容器）会打印
`4 个字体族 / 单族回退（NotoSansCJK-Bold.ttc）/ 只验缓存分键`，**跳过宽度比较而不是判失败**。

### 验证

- 在本机把 `os.path.exists` 换成"Linux 视角"（Windows 字体 -> False，Noto -> True）复现：
  修前 `FAIL 29`，修后 `29/29 PASS`；本机原生环境同样 `29/29 PASS`。
- **变异测试**（防止新断言变成"永远为真"）：故意把 `font()` 的缓存键改成 `(size, weight)`
  （丢掉 family），case 29 必须失败 —— 实测报 `字体缓存键没带 family（换族会串味）`，说明门禁仍在。
- CI 后续 4 个 selftest（sfxkit 16、scorekit 21、qa_frames 22、无 scipy 兜底）本地逐个复跑全绿。

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