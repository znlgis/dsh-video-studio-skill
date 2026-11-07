# @dsh-gis/dsh-video-studio-skill

![version](https://img.shields.io/badge/version-0.3.0-blue) ![license](https://img.shields.io/badge/license-MIT-green)

**DeepSeek Harness 技能插件：用代码从零生成中文视频。**

写剧本 → edge-tts 旁白 → **实测时长**排时间轴 → **提示点**驱动配乐与音效 → PIL 或浏览器逐帧渲染 →
ffmpeg 编码 → 采样级混音 → **四层质检**（容器 / 信号 / 时间 / 页面）。
零素材、可复现、可断点续跑、可脚本化验收；装成插件后，整片生产流程随技能一起进入每个会话。

> 源自 `dsh-v` 资产包的 `02-技能包-video-studio`（817 个 DSH 会话的考古分析 + 5 套现存工程逆向 + 本机实测）。
> **v0.2.0**：用这套技能真的做出一部 11 分钟成片后，发现 10 处"文档承诺了、代码没做到"的地方
> （门禁失效、缓存不校验、stage 假独立、mux 二次编码等），全部修掉并补了 14 条回归断言。
> **v0.3.0**：与另一部 6 分半成片（`ai-history-film`）对照后，补上本项目最缺的四块——
> 物理建模音效库、程序化配乐、浏览器逐帧渲染引擎、帧级质检，以及把三者串起来的**提示点系统**。
> 详见 [CHANGELOG.md](CHANGELOG.md) 与 SKILL.md §13。

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
**时长 30.10 s / 1920×1080 / 30 fps / h264+aac 48 kHz / -16.0 LUFS / 真峰 -1.3 dBTP / 黑帧 0 /
帧级深检：闪烁 0、空窗 0、音画同步超差 0**。

## 用法

触发词：做一个视频 / 生成宣传片 / 把报告变成视频 / 做动画 / 配音配字幕 / 出成片 /
修视频音画不同步 / 视频画质糊，或提到 ffmpeg、edge-tts、逐帧渲染、字幕、分镜、配乐、混音。

最短路径（技能 `SKILL.md` 第 1 节有完整版）：

```bash
mkdir -p myfilm && cd myfilm
cp <SKILL_DIR>/assets/film.json .                       # 剧本模板
cp <SKILL_DIR>/assets/scenes.py .                       # 场景模板
cp <SKILL_DIR>/scripts/*.py .                           # filmkit/render/selfcheck + sfxkit/scorekit/qa_frames/webengine
cp -r <SKILL_DIR>/assets/web ./web                       # 只在走浏览器引擎时需要

python selfcheck.py                                     # 23 条环境/回归断言（20 秒，不联网）
python render.py --stage stills --stills "l01:3,l02:5"  # 母版预览：先看构图
python render.py --script film.json --scenes scenes.py --out out/film.mp4 --work work
```

分阶段（每步落盘、可单独重跑）：

```
tts → layout → cues → score → sfx → audio → frames → mux → qa
```

**v0.3 的三种玩法**：

| 想要 | 怎么做 |
|---|---|
| 只加"该响的地方响一下" | 写 `film.json` 的 `sfx.cues` 或 `scenes.py` 的 `cues()` 钩子 → `--stage cues sfx audio` |
| 整片要一段像样的配乐 | 设 `bpm` / `music.mode=score` → `--stage score` |
| 画面要粒子/形变/着色器 | `--engine web` + 拷 `assets/web/`，改页面里的场景代码 |

`<SKILL_DIR>` 是 `skill` 工具加载时回报的 resourceBase 目录（`.../skills/video-studio/`）。

分阶段（v0.2 起**每个 stage 只做自己的事**）：`--stage tts | layout | audio | frames | mux | qa | stills`。

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
  scripts/selfcheck.py       回归自检（23 条"曾真的坏过"的断言）
  scripts/sfxkit.py          物理建模音效库（24 配方，scipy 可选）
  scripts/scorekit.py        程序化配乐（和弦表 / 分段能量 / 吸气）
  scripts/webengine.py       浏览器逐帧渲染（CDP 截图、分块并行、崩溃重开、cue 导出、文字质检）
  scripts/qa_frames.py       帧级深检（单帧闪烁 / 画面空窗 / 音画同步 / 亮度响度曲线）
  assets/film.json           剧本模板（含 hold / bpm / music / sfx.cues）
  assets/scenes.py           场景模板（开场 / 正文卡片 / 收尾 + cues() 钩子）
  assets/web/                浏览器页面模板（原生 Canvas2D 确定性时间轴）
```

## 环境前提

**必需**：ffmpeg + Python 的 numpy / Pillow / edge-tts。
**可选**：cv2（编码锐度比）、scipy（音效滤波加速，缺了走 FFT 卷积）、
playwright + Chromium/Edge（只在 `--engine web` 时需要）。
不需要 moviepy / pydub / soundfile / librosa / manim——本技能一行都不 import 它们。

`SKILL.md` 第 10 节是作者机器（Windows 11 + Ryzen AI 9 HX 370）的实测基准，**换机请先重测**：
`ffmpeg -version`、`python -c "import numpy,PIL,cv2"`、中文字体文件、`edge-tts --list-voices`。
ffmpeg 路径可用环境变量 `FILMKIT_FFMPEG` 覆盖，字体候选在 `filmkit.FONT_CANDIDATES`。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 兼容性

peer 范围 `@deepseek-ai/dsh-skill-filesystem >=0.1.0-rc.6 <0.3.0`，覆盖 DSH 0.1.x / 0.2.x。

## License

MIT。新增的四个模块（sfxkit / scorekit / webengine / qa_frames）的**算法思想**改编自
[ai-history-film](https://github.com/Re-Summer75/ai-history-film)（MIT），未复制其文件、未引入其三方依赖；
详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
