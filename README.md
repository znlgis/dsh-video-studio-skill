# @dsh-gis/dsh-video-studio-skill

![version](https://img.shields.io/badge/version-0.1.0-blue) ![license](https://img.shields.io/badge/license-MIT-green)

**DeepSeek Harness 技能插件：用代码从零生成中文视频。**

写剧本 → edge-tts 旁白 → **实测时长**排时间轴 → PIL 逐帧渲染 → ffmpeg 编码 → numpy 采样级混音 → 自动质检。
零素材、可复现、可断点续跑、可脚本化验收；装成插件后，整片生产流程随技能一起进入每个会话。

> 源自 `dsh-v` 资产包的 `02-技能包-video-studio`（817 个 DSH 会话的考古分析 + 5 套现存工程逆向 + 本机实测）。
> **v0.2.0 起技能正文有实质修订**：用这套技能真的做出一部 11 分钟成片后，发现 10 处
> "文档承诺了、代码没做到"的地方（门禁失效、缓存不校验、stage 假独立、mux 二次编码等），
> 全部修掉并补了 14 条回归断言。详见 [CHANGELOG.md](CHANGELOG.md) 与 SKILL.md §11。

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

## 用法

触发词：做一个视频 / 生成宣传片 / 把报告变成视频 / 做动画 / 配音配字幕 / 出成片 /
修视频音画不同步 / 视频画质糊，或提到 ffmpeg、edge-tts、逐帧渲染、字幕、分镜、配乐、混音。

最短路径（技能 `SKILL.md` 第 1 节有完整版）：

```bash
mkdir -p myfilm && cd myfilm
cp <SKILL_DIR>/assets/film.json .                       # 剧本模板
cp <SKILL_DIR>/assets/scenes.py .                       # 场景模板
cp <SKILL_DIR>/scripts/{filmkit.py,render.py,selfcheck.py} .

python selfcheck.py                                     # 14 条环境/回归断言（10 秒，不联网）
python render.py --stage stills --stills "l01:3,l02:5"  # 母版预览：先看构图
python render.py --script film.json --scenes scenes.py --out out/film.mp4 --work work
```

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

## 文件结构

```
index.js                     插件入口：把 skills/ 挂成 bundled 技能根
cordis.patch.yml             组合包 patch（insert: video-studio）
icon.svg / locale/*.json     插件页展示元数据
skills/video-studio/
  SKILL.md                   主线：上手 / 八阶段 / 引擎选型 / 画质清单 / 质检门禁 / 故障速查 / 升级须知
  references/                pipeline · scene-authoring · audio · quality-gates · pitfalls
  scripts/filmkit.py         生产内核（字体/字形自查/缓存排版/字幕安全区/音频总线/TTS/时间轴/编码/质检）
  scripts/render.py          编排器（7 个独立 stage、分块并行、断点续跑、母版预览）
  scripts/selfcheck.py       回归自检（14 条"曾真的坏过"的断言）
  assets/film.json           剧本模板
  assets/scenes.py           场景模板（开场 / 正文卡片 / 收尾）
```

## 环境前提

技能里所有代码只依赖 **ffmpeg + Python 的 numpy / Pillow / cv2 / edge-tts**；
不需要 moviepy / pydub / soundfile / librosa / manim。

`SKILL.md` 第 10 节是作者机器（Windows 11 + Ryzen AI 9 HX 370）的实测基准，**换机请先重测**：
`ffmpeg -version`、`python -c "import numpy,PIL,cv2"`、中文字体文件、`edge-tts --list-voices`。
ffmpeg 路径可用环境变量 `FILMKIT_FFMPEG` 覆盖，字体候选在 `filmkit.FONT_CANDIDATES`。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 兼容性

peer 范围 `@deepseek-ai/dsh-skill-filesystem >=0.1.0-rc.6 <0.3.0`，覆盖 DSH 0.1.x / 0.2.x。

## License

MIT
