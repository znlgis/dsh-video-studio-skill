# @dsh-gis/dsh-video-studio-skill

![version](https://img.shields.io/badge/version-1.0.3-blue) ![license](https://img.shields.io/badge/license-MIT-green)

**DeepSeek Harness 技能插件：用代码从零生成中文视频。**

写剧本 → edge-tts 旁白 → **实测时长**排时间轴 → **提示点**驱动配乐与音效 → PIL 或浏览器逐帧渲染 →
ffmpeg 编码 → 采样级混音 → **四层质检**（容器 / 信号 / 时间 / 页面）+ **v0.8 的四个新闸**
（渲前资源预检 `G0` / 词级时间戳 `G10` / 全片 VMAF `G11` / ASR 回读 `G12`）。
零素材、可复现、可断点续跑、可脚本化验收；装成插件后，整片生产流程随技能一起进入每个会话。

> **v1.0.3（当前）**：**取景不再抖**——46 个真实会话回放里用户的"明显的抖动感"，根因是底图把颗粒
> 烘进去了、再逐帧用 NEAREST 缩放取景（采样网格随缩放比整像素跳变，颗粒整体错位 1px）。
> 改成端点预烘 + `Image.blend` 插值（4.4 ms/帧），相邻帧差从 4~6 灰阶降到 0~1；
> 自检新增第 **68** 条断言守住它（改回 NEAREST 立刻 fail），第 **69** 条守住 ASR 掉分可定位。ASR 回读低于 pass 线时，
> 现在会直接列出"剧本里有、成片里没对上"的字符（`lost_top`），不再只给一个 0.813 的数字。
>
> **v1.0.2**：**零拷贝出片**——技能脚本留在技能目录里直接跑，工程目录里只需要一个 `film.json`；
> `render.py` 不写 `--scenes` 就先用剧本同目录的 `scenes.py`、再回落到技能内置的 `assets/scenes.py`，
> 不需要再拷脚本/模板。设计与画面新增两块可调：**字幕样式** `visual.subtitle`
> （字号/位置/对齐/背板透明度/圆角/字族/颜色/最多几行，不写 = 与过去逐像素一致）与
> **片内版式轮换** `visual.layout_cycle`（按章节换骨架，默认派生 2–3 种，防"全片一张版式"）。
> 同时修掉一批"照文档做就崩"的命令（`--stage style` 不再 KeyError、`--stage cues` 可单跑、
> 已删除 API 改为明确报错 exit 2 而不是 NameError/假绿），并给"不同片不重复"加了**片间去重台账**
> （`--confirm` 记录交付过的设计，下次提案主动避开）。
> 另外 brief 里写「2 分钟 / 90 秒」现在会被接住：解析成 `duration_target`，决定候选段落数，
> 渲染时对账并在差 >15% 时告警——但**成片长度仍由实测旁白决定，不会截断**。自检 **69 条**全绿。

> 源自 `dsh-v` 资产包的 `02-技能包-video-studio`（817 个 DSH 会话的考古分析 + 5 套现存工程逆向 + 本机实测）。
> 版本演进与每一版的实测证据都在 [CHANGELOG.md](CHANGELOG.md)；这里只留一句方向：
> **v1.0 起不再提供任何模板/风格目录**——技能给的是「生成视频的技术」，外观由每次现场采样的设计决定。
>
> 重要节点：v0.6 交付可信化（门禁账本 / QC 报告 / 签核）→ v0.7 声音片间差异 → v0.8 词级时间轴 + 四道新门禁
> → v0.9 场景语法（10 类构图）与视觉语言 → **v1.0 提案优先**（先出方案再出片）→ **v1.0.1 清除全部模板**
> → **v1.0.2 零拷贝 + 可定制字幕/版式轮换 + 片间去重台账**。
>
> 想直接看「这一版改了什么、为什么改、怎么验证」，读 [CHANGELOG.md](CHANGELOG.md) 的 1.0.2 段；
> 想学怎么控制画面/声音/节奏，读 [SKILL.md](skills/video-studio/SKILL.md)。
## 安装

桌面版：插件管理器 → 填 `@dsh-gis/dsh-video-studio-skill`（或你的本地 checkout 目录，如 `D:/path/to/dsh-video-studio-skill`）

命令行：

```bash
dsh plugin --profile desktop add "@dsh-gis/dsh-video-studio-skill"
# 本地调试（不发布也能装）：
dsh plugin --profile desktop add "D:/path/to/dsh-video-studio-skill"
```

安装后在「插件」页启用 `video-studio` 这一行，新开会话即可在技能目录看到 **video-studio**。

### 与手工技能目录的关系

技能按 rank 解析：`~/.dsh/skills`（rank 400）**优先于**插件提供的 bundled 技能根（rank 600）。
如果你之前手工装过 `~/.dsh/skills/video-studio`，它会把插件版本遮住。装好插件后**把旧目录移出 `~/.dsh/skills` 根目录**（只改名不够——扫描认的是 `<root>/<name>/SKILL.md`，`.bak` 后缀的目录照样会被扫到）：

```bash
mkdir -p ~/.dsh/backup
mv ~/.dsh/skills/video-studio ~/.dsh/backup/video-studio.bak-$(date +%Y%m%d)
```

需要回滚时把目录移回 `~/.dsh/skills/video-studio` 即可。

## 效果：同一个 brief，两次就是两部不同的片（可复现）

这一节不贴"宣传图"——**贴的是你现在就能跑出来的东西**。向同一个 brief 连续提案两次，
配色、母题、转场、配乐、声线、版式轮换、字幕样式都会不同（这就是 v1.0 提案优先 + v1.0.2 片间去重的目标）：

```bash
# 第一轮：看 3 个候选并排（每个候选 = 一份完整设计 + 静帧联系表 + 与其它候选的设计距离）
python <SKILL_DIR>/scripts/propose.py --brief "AI 大模型的三年，30 秒科普，深蓝" --script film.json --work work
#   候选 A  深色 h201·四角括线     3 帧  0.9s  -> work/proposals/rev1/A/_sheet.png
#   候选 B  深色 h220·细线页眉     3 帧  0.6s  -> work/proposals/rev1/B/_sheet.png
#   候选 C  深色 h213·极简留白     3 帧  0.6s  -> work/proposals/rev1/C/_sheet.png
#   联系表（并排对比）: work/proposals/rev1/_compare.png

# 换一个 work 目录再跑一次同一句 brief —— 会得到另一组设计（不传 --seed 就是每次新 nonce）
python <SKILL_DIR>/scripts/propose.py --brief "AI 大模型的三年，30 秒科普，深蓝" --script film.json --work work2
```

> 实测（作者机器，2026-11-11）：同一 brief 连跑 3 次，9 个候选的 `design_fingerprint` **两两不同**；
> 每批 3 个候选的 `layout_cycle` 都是 2–3 种版式，`subtitle` 的字号/对齐/背板透明度/圆角/字族也各不相同。
> 带「深蓝」这类配色约束时，v1.0.2 之前 n=3 有近一半概率采样失败（约束窗口只有 52°，
> 而旧实现按 24° 分桶判重），现在实测 **10/10 成功**。

**三个可复现的卖点**：

| 卖点 | 怎么自己验证 | 看什么 |
|---|---|---|
| 同一个 brief 两次结果不同 | 同一句 `--brief` 换 `--work` 目录跑两次 | 候选表的「立意」与配色色相都不一样；`design_fingerprint` 不同 |
| 三个候选并排对比 | `propose.py` 末尾会打印联系表路径 | `work/proposals/rev1/_compare.png`：一行一个候选，带中文立意标签 |
| 版式与字幕可变 | `render.py --stage layout` / `--stage style` | `storyboard.json` 每段的 `layout` 字段；`--stage style` 打印的「版式轮换」与「字幕」行 |

**画面长什么样，由设计决定而不是由"第几套模板"决定**：10 类构图（hero / statement / list / stat /
compare / process / quote / chapter / chart / outro）× 8 种装饰母题 × 6 种转场 × 连续色相配色 ×
字幕样式 × 按章节轮换的版式——而且每个候选都要过 `theme_lint` 对比度体检和 G13 剪影可区分门禁。

> 说明：仓库里的 `docs/` 图片是 **v0.5/v0.9 时期的历史快照**，对应的 `style:auto` 选型与风格表
> 在 v1.0.1 已删除，**这些图不能再当"现在跑出来就是这样"的证据**，所以本 README 不再引用它们。
> 现在的证据是**命令 + 你机器上的实测输出**。

## 用法（零拷贝：不拷脚本、不拷模板）

触发词：做一个视频 / 生成宣传片 / 把报告变成视频 / 做动画 / 配音配字幕 / 出成片 /
修视频音画不同步 / 视频画质糊，或提到 ffmpeg、edge-tts、逐帧渲染、字幕、分镜、配乐、混音。

**最短路径**（[SKILL.md §1](skills/video-studio/SKILL.md) 有带注释的完整版）：

```bash
mkdir -p myfilm && cd myfilm
# 1) 只写一个 film.json（工程目录里唯一需要你手写的文件；最小骨架见 SKILL.md §1）

# 2) 环境自检（可选，69 条断言，不联网）
python <SKILL_DIR>/scripts/selfcheck.py

# 3) 先出方案：3 个候选，各带静帧联系表；2 秒级、全程离线
python <SKILL_DIR>/scripts/propose.py --brief "AI 大模型的三年，30 秒科普" --script film.json --work work

# 4) 确认候选，冻结设计 -> work/design.json（此后渲染只认这份设计）
python <SKILL_DIR>/scripts/propose.py --confirm --candidate A --work work

# 5) 先看静帧，再出全片。--scenes 不写 = 剧本同目录 scenes.py -> 技能内置 assets/scenes.py
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage stills --stills "l01:3,l02:5"
python <SKILL_DIR>/scripts/render.py --script film.json --work work --out out/film.mp4 --stage all

# 6) 交付凭据 + 人工签核
cat out/QC-REPORT.md
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage accept --gate release
```

`<SKILL_DIR>` 是 `skill` 工具加载时回报的 resourceBase 目录（`.../skills/video-studio/`）；
`<REPO>` 是包根（仓库根，或装好后 profile 里的 `node_modules/@dsh-gis/dsh-video-studio-skill/`），
即 `<SKILL_DIR>` 往上两级——`tools/`、`schemas/` 在 `<REPO>` 下。

**分阶段**（`--stage` 每个只做自己的事，落盘后可单独重跑）：
`style | tts | layout | cues | score | sfx | audio | frames | mux | qa | stills | accept | explain | all`。

**你想控制什么，就改哪里**：

| 想控制 | 改哪里 | 怎么验证 |
|---|---|---|
| 画面配色/字体/装饰/转场/版式 | `work/design.json` 的 `visual.*`（或重新提案 `--revise`） | `render.py --stage style`；`designkit.py --lint work/design.json` |
| 字幕字号/位置/对齐/背板/圆角/字族/颜色/行数 | `visual.subtitle`（v1.0.2） | `--stage style` 打印的「字幕」行；改前后抽帧对比 |
| 片内版式轮换（防全片一张版式） | `visual.layout_cycle`（v1.0.2），不写则自动派生 2–3 种 | `work/storyboard.json` 每段的 `layout` |
| 这段画面的构图类型 | `film.json` 的 `lines[].type`（10 类之一），不写由 grammar 选 | `--stage layout` 打印的分镜表 |
| 配乐调式/BPM/调性/情绪 | `work/design.json` 的 `audio.music.*`（`film.json` 的 `music.*` 可覆盖） | `--stage score` 的打印 + 听 |
| 旁白声线与语气 | `audio.voice.*`（裸 edge voice id + tone）；`voicekit.py --list` 查音色 | `--stage tts`；`work/voice_plan.json` |
| 音效密度与提示点 | `audio.sfx.*`；提示点写 `film.json` 的 `sfx.cues` 或 `scenes.py` 的 `cues()` | `--stage cues` / `--stage sfx` |
| 节奏（停顿/最小段长/条目步进） | `pacing.*`；单句停留写 `film.json` 的 `lines[].hold` | `--stage layout` 的总时长 |
| 目标时长（如「2 分钟」） | `--brief` 里写出来 → `design.structure.target_sec` | `propose` 打印目标时长；渲染时对账，差 >15% 告警。**成片长度仍由实测旁白决定，不会截断** |
| 画面本身（绘制代码） | 剧本同目录放自己的 `scenes.py`（实现 `draw_frame`，可选 `cues()`） | `--stage stills` 抽帧看 |

**常用开关**：

| 想要 | 怎么做 |
|---|---|
| 交付 720p / 换帧率 | `film.json` 写 `"video": {"width":1280,"height":720,"fps":24}`（只做 16:9 横屏、只能下采样） |
| 批量出片 | `python <SKILL_DIR>/scripts/render.py --batch films.json`（≤100 条、开跑前全量预检、单条失败不阻断） |
| 只渲前 N 秒给人看 | `--preview 15`（出 `out/preview/*.mp4` + `.gif`，不写交付账本） |
| 让 QA 快一点 | `--vmaf sample`（每 5 帧取 1）或 `--vmaf off`（G11 记 skipped） |
| 交付前把「没执行」也算失败 | `--strict-gates` |
| 改完东西不知道要重跑什么 | `--stage explain`（机器判断过时阶段 + 给最小重跑命令） |
| 换一个候选/方向重出方案 | `propose.py --revise "再冷一点，少装饰，换女声"`；旧 rev 留着眼 diff |
| 复现某一次提案 | 首次提案加 `--seed 42`（不传 seed = 每次新 nonce） |
| 忽略/查看片间去重台账 | `--no-ledger` / `--show-ledger`；路径可用 `VIDEO_STUDIO_LEDGER` 覆盖 |
| 浏览器渲染要跨会话字节级可复现 | `--deterministic`（或 `WEBENGINE_DETERMINISTIC=1`），代价约 +22%/帧 |

长片：`--stage frames --jobs 4`（按绝对帧号分块并行）+ `--resume`（中断只补缺块）。

## 这个技能解决什么

| 铁律 | 怎么保证 |
|---|---|
| 音画不同步 | 时间轴由 TTS **实测时长**反推，绝不按字数估算 |
| 文字发虚 | PIL 帧走 `rawvideo` 直通 libx264，不用 cv2 中间文件二次压缩 |
| 后半段静音 | numpy 采样级混音，不用一条 ffmpeg 命令喂上百个输入 |
| 该有的淡入/字幕静默消失 | 质检门禁查"必须有"和"不能有"两类断言 |
| 成片"像草稿" | -16 LUFS / 真峰 ≤ -1 dBTP / 编码锐度比 ≥ 0.85 |
| **声音和画面对不上（v0.3）** | **提示点系统**：画面登记 cue → 音效按 cue 合成，改节奏不用重新对时间 |
| **声音单薄、只有"叮叮咚咚"（v0.3）** | `sfxkit` 物理建模音效 + `musicgen` 按参数现场编排配乐（没有现成曲风可选） |
| **一帧白闪/两秒空窗没人发现（v0.3）** | `qa_frames` 帧级深检：单帧闪烁、画面空窗、音画同步 |
| **"我查过了"无法复现（v0.6）** | `out/QC-REPORT.md` + `work/gates.json`：三档 gate matrix，"没执行"独立成 `skipped` |
| **"我签过了"变成空头支票（v0.6）** | 签核绑内容指纹：改剧本 / `scenes.py` / 引擎，旧签核自动失效 |
| **改一句台词不知道要重跑什么（v0.6）** | `work/manifest.json` 记每阶段输入/输出指纹，`--stage explain` 直接给最小重跑命令 |
| **`holds` 写成拼写错误却静默失效（v0.6）** | `schemas/film.schema.json` + `filmcheck.py`，并接进 `ci_check` 第 12 项 |
| **机器替你选风格、你从头到尾没参与（v1.0）** | **提案优先**：`propose.py` 先出 N 个候选（各带静帧联系表 + 设计距离），人 `--confirm` 后才冻结 `design.json` |
| **每个视频都差不多（v1.0/v1.0.2）** | 设计 DSL 现场采样（每次新 nonce）+ 候选两两 ≥2 维不同 + 底色色相分叉 + **片间去重台账**（默认记录交付过的设计并主动避开） |
| **外观只能从几套模板里挑（v1.0.1）** | 没有内置风格表：配色是连续色相空间、母题/转场/缓动/版式/字幕全字段开放，且必须过 `theme_lint` 与 G13 剪影门禁 |

## 文件结构

```
index.js                     插件入口：把 skills/ 挂成 bundled 技能根
cordis.patch.yml             组合包 patch（insert: video-studio）
icon.svg / locale/*.json     插件页展示元数据
skills/video-studio/
  SKILL.md                   主线：零拷贝上手 / 提案 / 设计 DSL / 八阶段 / 引擎 / 画质 / 声音 / 提示点 / 质检 / 升级
  references/                proposal · scene-grammar · pipeline · scene-authoring · audio · audio-variation
                             sfx-design · web-engine · frame-qa · quality-gates · word-timeline · v08-quality
                             change-impact · style-packs（v1.0.2 重写为设计 DSL 外观定制）· pitfalls
  scripts/propose.py         v1.0 提案器（--brief/--revise/--confirm/--diff；v1.0.2 片间去重台账）
  scripts/designkit.py       v1.0 设计 DSL（采样/校验/转计划/设计距离/字幕参数）
  scripts/previewkit.py      v1.0 离线预览（合成时间轴 + 联系表，不联网）
  scripts/grammar.py         v0.9 场景语法（10 类构图 -> storyboard.json）
  scripts/voicekit.py        v1.0 声线/语气计划（裸 edge voice id + 响度补偿）
  scripts/filmkit.py         生产内核（字体/排版/字幕/音频总线/CueBus/TTS/时间轴/编码/质检）
  scripts/gates.py           v0.6 门禁账本 / QC 报告渲染 / 人工签核（绑内容指纹）
  scripts/stagewatch.py      v0.6 阶段新鲜度（work/manifest.json + --stage explain）
  scripts/filmcheck.py       v0.6 film.json 契约校验（未知键/枚举/跨字段；style 键为 inert 警告）
  scripts/render.py          编排器（14 个 stage、pil|web 双引擎、分块并行、断点续跑、母版预览、门禁账本；
                             v1.0.2 起 --scenes 缺省回落到技能内置 scenes.py）
  scripts/preflight.py       v0.8 渲染前资源预检（字形 / TTS 音频 / 字体 / web 入口页，门禁 G0）
  scripts/qavmaf.py          v0.8 全片 VMAF/SSIM（参考帧由场景函数实时重渲，门禁 G11）
  scripts/qaasr.py           v0.8 ASR 回读（成片音轨转文字对账，门禁 G12）
  scripts/selfcheck.py       回归自检（69 条"曾真的坏过"的断言）
  scripts/sfxkit.py          物理建模音效库（24 配方，scipy 可选）
  scripts/scorekit.py        配乐合成原语（和声 / 律动 / 混音 / 响度 / 24-bit 落盘）
  scripts/musicgen.py        参数化配乐编排器（没有风格表：和弦进行 / 动机 / 鼓型 / 音色全可传参）
  scripts/webengine.py       浏览器逐帧渲染（CDP 截图、分块并行、崩溃重开、cue 导出、文字质检）
  scripts/qa_frames.py       帧级深检（单帧闪烁 / 画面空窗 / 画面静止 / 音画同步 / 亮度响度曲线）
  requirements.txt           Python 依赖（必需 / 强烈建议 / 可选三档，带注释）
  assets/film.json           测试夹具（含 bpm / music / sfx.cues / 逐句 hold；不是给用户抄的模板）
  assets/scenes.py           技能内置渲染器（10 类构图 + draw_frame + cues() 钩子；--scenes 的默认回退）
  assets/design_fixture.json 金帧基准设计（像素回归绑它，不绑任何内置风格）
  assets/voice_loudness.json 音色响度补偿实测表
  assets/golden_frames.json  v0.8 金帧基线（3 帧 sha256 + 字体/环境指纹，改绘制代码后重录）
  assets/web/                浏览器页面示例（--engine web 需要你自己的页面；不是出片模板）
schemas/design.schema.json   设计 DSL 契约（v1.0；含 visual.subtitle / layout_cycle）
schemas/proposal.schema.json 提案契约（v1.0）
schemas/film.schema.json     film.json 的契约（draft-07 子集）
tools/ci_check.py            仓库级体检（12 项；语法/JSON/依赖白名单/打包卫生/文档引用/film 契约，不需 ffmpeg）
tools/estimate.py            开渲前成本预演（帧数/耗时/磁盘/分块建议，--json 可机读；含 preflight/vmaf/asr）
tools/golden_frames.py       v0.8 金帧像素回归（--write 重录 / --check 校验；同环境像素变了 = fail）
tools/silhouette_qa.py       v0.9 剪影可区分门禁 G13（换设计不能只是换色）
tools/audio_similarity.py    音频差异/相似度验收（--strict 阈值判定，改音频代码后必跑）
tools/selftest_noscipy.py    无 scipy 兜底路径自检（CI 用；屏蔽 scipy 后跑 sfxkit/musicgen 自检）
.github/workflows/ci.yml     CI：repo 体检（ubuntu+windows）+ 技能自检（ubuntu）
```

## 环境前提

**插件运行**：Node.js ≥ 20（`package.json` 的 `engines`；这是 DSH 加载插件用的运行时，不必另装）。
**必需**：ffmpeg + Python 的 numpy / Pillow / edge-tts。
**可选**：cv2（编码锐度比）、scipy（音效滤波加速，缺了走 FFT 卷积）、
playwright + Chromium/Edge（只在 `--engine web` 时需要）、
faster-whisper（v0.8 的 ASR 回读门禁 G12；没装会降级到 ffmpeg whisper 滤镜，仍不可用则记 skipped）。
v0.8 的 G11（全片 VMAF）不需要新依赖，但要求 ffmpeg 构建**带 libvmaf**（9.0.2 full_build 有）。
不需要 moviepy / pydub / soundfile / librosa / manim——本技能一行都不 import 它们。

依赖清单有机器可读的一份，别再手抄：

```bash
pip install -r skills/video-studio/requirements.txt   # 必需 + 强烈建议；可选项目已注释，按需打开
```

`SKILL.md` 第 12 节是作者机器（Windows 11 + Ryzen AI 9 HX 370）的实测基准，**换机请先重测**：
`ffmpeg -version`、`python -c "import numpy,PIL,cv2"`、中文字体文件、`edge-tts --list-voices`。
ffmpeg 路径可用环境变量 `FILMKIT_FFMPEG` 覆盖，字体候选在 `filmkit.FONT_CANDIDATES`。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 兼容性

peer 范围 `@deepseek-ai/dsh-skill-filesystem >=0.1.0-rc.6 <0.3.0-0`，覆盖 DSH 0.1.x / 0.2.x 的**正式版**。

⚠️ **预发布版按 semver 规则不满足这个范围**：`>=0.1.0-rc.6 <0.3.0` 这组比较器里没有带预发布标签的
基准，所以 `0.2.2-rc.1` 这类版本 `semver.satisfies()` 返回 false（实测）。因为该 peer 标了
`optional`，pnpm 不会因此阻断安装；插件入口也已经把依赖改成 `apply` 内按需 `import`——
缺这个包时**只降级（不挂载 bundled 技能根）并打一条能照着做的日志，不会让整个插件行加载失败**。

## 仓库自检（给贡献者与 CI 用，不是给用户用的）

```bash
python tools/ci_check.py -v     # 12 项：py 语法（含 tools/，SyntaxWarning 记失败）/ node --check /
                                #         JSON + 版本号一致性（package.json vs README 徽章）/ 技能清单 /
                                #         依赖白名单 / npm pack 打包卫生 / 文档路径 + 正文相对链接 /
                                #         Python 3.9 语法 / Markdown 围栏（状态机）/ CI workflow
                                #         （裸冒号 + run 里的 python 脚本是否存在）/ 单文件体积 /
                                #         film.json 契约（调 filmcheck.py，规则只留一份）
```

它**不需要 ffmpeg、字体或网络**，任何机器都能跑；它检查的是"这个仓库作为可分发包是否完整"。
**它不能替代** `skills/video-studio/scripts/selfcheck.py`——那个要 ffmpeg + 中文字体，真的调编码器，
查的是"技能本身还能不能出片"（**69 条**断言）。两层都在 `.github/workflows/ci.yml` 里跑。

## License

MIT。新增的四个模块（sfxkit / scorekit / webengine / qa_frames）的**算法思想**改编自
[ai-history-film](https://github.com/Re-Summer75/ai-history-film)（MIT），未复制其文件、未引入其三方依赖；
详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。