# @dsh-gis/dsh-video-studio-skill

![version](https://img.shields.io/badge/version-0.5.0-blue) ![license](https://img.shields.io/badge/license-MIT-green)

**DeepSeek Harness 技能插件：用代码从零生成中文视频。**

写剧本 → edge-tts 旁白 → **实测时长**排时间轴 → **提示点**驱动配乐与音效 → PIL 或浏览器逐帧渲染 →
ffmpeg 编码 → 采样级混音 → **四层质检**（容器 / 信号 / 时间 / 页面）。
零素材、可复现、可断点续跑、可脚本化验收；装成插件后，整片生产流程随技能一起进入每个会话。

> 源自 `dsh-v` 资产包的 `02-技能包-video-studio`（817 个 DSH 会话的考古分析 + 5 套现存工程逆向 + 本机实测）。
> **v0.2.0**：用这套技能真的做出一部 11 分钟成片后，发现 10 处"文档承诺了、代码没做到"的地方
> （门禁失效、缓存不校验、stage 假独立、mux 二次编码等），全部修掉并补了 14 条回归断言。
> **v0.3.0**：与另一部 6 分半成片（`ai-history-film`）对照后，补上本项目最缺的四块——
> 物理建模音效库、程序化配乐、浏览器逐帧渲染引擎、帧级质检，以及把三者串起来的**提示点系统**。
> **v0.4.0**：修掉 4 处"门禁看着干净、其实没在查"的静默问题（`--resume` 复用旧分块、
> `music.mode=off` 仍混入旧配乐、PIL 路线音画同步恒空、亮度静止判据误报 4 段），
> 补上仓库级体检（`tools/ci_check.py`）、CI、`requirements.txt`，并修掉管道中文乱码。
> **v0.5.0（风格包）**：解决"每部片都长一个样"。一个 `style` id 同时决定画风（8 套主题 ×
> 3 种版式）、配乐（8 套风格：调池/BPM 梯度/进行/动机/配器/鼓型）、旁白（9 个声线 × 6 档语气）
> 与节奏；`"style": "auto"` 按内容自动选，三级内容指纹保证"改错别字不换整首曲子"。
> 不写 `style` 字段 = v0.4 行为（配乐 PCM 逐样本一致，selfcheck 有断言）。
> 详见 [CHANGELOG.md](CHANGELOG.md)、SKILL.md §5.5 与 references/style-packs.md。

## 安装

桌面版：插件管理器 → 填 `@dsh-gis/dsh-video-studio-skill`（或本地目录 `D:\self\code\dsh-video-studio-skill`）

命令行：

```bash
dsh plugin --profile desktop add "@dsh-gis/dsh-video-studio-skill"
# 本地调试（不发布也能装）：
dsh plugin --profile desktop add "D:/self/code/dsh-video-studio-skill"
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

## 效果（不是渲染出来的"宣传图"，是默认模板片的真实抽帧）

<p align="center"><img src="docs/demo-pil-sheet.png" width="100%" alt="默认模板片联系表：开场 / 正文卡片 / 正文卡片 / 收尾"></p>

上面四帧是用仓库自带的 `assets/film.json` + `assets/scenes.py` 从零跑一遍 `python render.py` 的
`work/qa/sheet.png`，未经任何修图。配套实测（同一台机器）：
**时长 30.10 s / 1920×1080 / 30 fps / h264+aac 48 kHz / -16.0 LUFS / 真峰 -1.2 dBTP / 黑帧 0 /
帧级深检：闪烁 0、空窗 0、画面静止 0**（音画同步在 PIL 路线上需要写 `visual_at` 才会真的查，见 SKILL.md §7）。

## 效果：同一个模板，八种气质（v0.5）

同一份文字，只改 `"style"`（画风 / 配乐 / 旁白 / 节奏一起变）：

<p align="center"><img src="docs/styles/sheet-8-styles.png" width="100%" alt="8 套风格对比：科技深蓝 / 杂志暖白 / 手绘纸感 / 活力撞色 / 极简黑白 / 电影青橙 / 暖灰薄荷 / 深黑鎏金"></p>

三份**不同题材**的短片（全流程真跑：TTS → 排轴 → 配乐 → 音效 → 混音 → 逐帧 → 编码 → 质检）：

| 题材 | 自动选中的风格 | 配乐 | 旁白声线 | 质检 |
|---|---|---|---|---|
| AI 大模型的三年 | tech-deep 科技深蓝 | 小调 116BPM / Em | 青年开场 → 男声正文 → 女声引用 | PASS（17.3s，-16.0 LUFS） |
| 手工木作的一天 | paper 手绘纸感 | 大调 84BPM / C | 女声 + 软糯女声正文 | PASS（19.9s） |
| 深夜学习指南 | lo-fi 暖灰薄荷 | 大调 84BPM / G | 温柔女声 + 女声开场 | PASS（20.1s） |

<p align="center"><img src="docs/styles/sheet-ai.png" width="32%" alt="AI 题材"><img src="docs/styles/sheet-craft.png" width="32%" alt="手作题材"><img src="docs/styles/sheet-night.png" width="32%" alt="深夜题材"></p>

三片的联系表（左→右为时间顺序）肉眼可见不是同一套风格：深蓝科技 / 牛皮纸手作 / 冷灰薄荷深夜。

## 用法

触发词：做一个视频 / 生成宣传片 / 把报告变成视频 / 做动画 / 配音配字幕 / 出成片 /
修视频音画不同步 / 视频画质糊，或提到 ffmpeg、edge-tts、逐帧渲染、字幕、分镜、配乐、混音。

最短路径（技能 `SKILL.md` 第 1 节有完整版）：

```bash
mkdir -p myfilm && cd myfilm
cp <SKILL_DIR>/assets/film.json .                       # 剧本模板（"style": "auto"）
cp <SKILL_DIR>/assets/scenes.py .                       # 场景模板
cp <SKILL_DIR>/scripts/*.py .                           # filmkit/stylekit/voicekit/render/selfcheck + sfxkit/scorekit/qa_frames/webengine
cp -r <SKILL_DIR>/assets/web ./web                       # 只在走浏览器引擎时需要

python selfcheck.py                                     # 29 条环境/回归断言（20 秒，不联网）
python render.py --stage stills --stills "l01:3,l02:5"  # 母版预览（缺 tts/layout 会自动补跑）
python render.py --script film.json --scenes scenes.py --out out/film.mp4 --work work
```

分阶段（每步落盘、可单独重跑）：

```
tts → layout → cues → score → sfx → audio → frames → mux → qa
```

**三种玩法**：

| 想要 | 怎么做 |
|---|---|
| 只加"该响的地方响一下" | 写 `film.json` 的 `sfx.cues` 或 `scenes.py` 的 `cues()` 钩子 → `--stage cues sfx audio` |
| 整片要一段像样的配乐 | 设 `bpm` / `music.mode=score` → `--stage score` |
| 画面要粒子/形变/着色器 | `--engine web` + 拷 `assets/web/`，改页面里的场景代码 |

**v0.4 的两个新开关**（都是"让门禁真的会失败"，不是新玩具）：

| 想要 | 怎么做 |
|---|---|
| 让 PIL 路线的音画同步门禁真的生效 | cue 加 `"visual_at": 绝对秒`；CI 里再加 `QA_REQUIRE_SYNC=1` 把"没查"变成失败 |
| 浏览器渲染要跨会话字节级可复现 | `--deterministic`（或 `WEBENGINE_DETERMINISTIC=1`），代价约 +22%/帧 |

`<SKILL_DIR>` 是 `skill` 工具加载时回报的 resourceBase 目录（`.../skills/video-studio/`）。

分阶段（v0.2 起**每个 stage 只做自己的事**；完整 10 个 stage 见上）：`--stage tts | layout | cues | score | sfx | audio | frames | mux | qa | stills`。

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
| **声音单薄、只有"叮叮咚咚"（v0.3）** | `sfxkit` 物理建模音效 + `scorekit` 一张和弦表贯穿全片 |
| **一帧白闪/两秒空窗没人发现（v0.3）** | `qa_frames` 帧级深检：单帧闪烁、画面空窗、音画同步 |

## 文件结构

```
index.js                     插件入口：把 skills/ 挂成 bundled 技能根
cordis.patch.yml             组合包 patch（insert: video-studio）
icon.svg / locale/*.json     插件页展示元数据
skills/video-studio/
  SKILL.md                   主线：上手 / 八阶段 / 引擎选型 / 画质 / 声音 / 提示点 / 停留窗口 / 质检 / 故障速查
  references/                pipeline · scene-authoring · audio · sfx-design · web-engine · frame-qa
                             quality-gates · pitfalls
  scripts/filmkit.py         生产内核（字体/排版/字幕/音频总线/CueBus/TTS/时间轴/编码/质检）
  scripts/render.py          编排器（10 个独立 stage、pil|web 双引擎、分块并行、断点续跑、母版预览）
  scripts/selfcheck.py       回归自检（29 条"曾真的坏过"的断言）
  scripts/sfxkit.py          物理建模音效库（24 配方，scipy 可选）
  scripts/scorekit.py        程序化配乐（和弦表 / 分段能量 / 吸气）
  scripts/webengine.py       浏览器逐帧渲染（CDP 截图、分块并行、崩溃重开、cue 导出、文字质检）
  scripts/qa_frames.py       帧级深检（单帧闪烁 / 画面空窗 / 画面静止 / 音画同步 / 亮度响度曲线）
  requirements.txt           Python 依赖（必需 / 强烈建议 / 可选三档，带注释）
  assets/film.json           剧本模板（含 hold / bpm / music / sfx.cues）
  assets/scenes.py           场景模板（开场 / 正文卡片 / 收尾 + cues() 钩子）
  assets/web/                浏览器页面模板（原生 Canvas2D 确定性时间轴）
tools/ci_check.py            仓库级体检（语法/JSON/依赖白名单/打包卫生/文档引用，不需 ffmpeg）
.github/workflows/ci.yml     CI：repo 体检（ubuntu+windows）+ 技能自检（ubuntu）
```

## 环境前提

**必需**：ffmpeg + Python 的 numpy / Pillow / edge-tts。
**可选**：cv2（编码锐度比）、scipy（音效滤波加速，缺了走 FFT 卷积）、
playwright + Chromium/Edge（只在 `--engine web` 时需要）。
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
python tools/ci_check.py -v     # 11 项：py 语法 / node --check / JSON / 技能清单 / 依赖白名单 /
                                #         npm pack 打包卫生 / 文档路径引用 / Python 3.9 语法 /
                                #         Markdown 围栏平衡 / CI workflow 语法 / 单文件体积
```

它**不需要 ffmpeg、字体或网络**，任何机器都能跑；它检查的是"这个仓库作为可分发包是否完整"。
**它不能替代** `skills/video-studio/scripts/selfcheck.py`——那个要 ffmpeg + 中文字体，真的调编码器，
查的是"技能本身还能不能出片"。两层都在 `.github/workflows/ci.yml` 里跑。

## License

MIT。新增的四个模块（sfxkit / scorekit / webengine / qa_frames）的**算法思想**改编自
[ai-history-film](https://github.com/Re-Summer75/ai-history-film)（MIT），未复制其文件、未引入其三方依赖；
详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
