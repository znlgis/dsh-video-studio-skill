# 参考机器的已验证环境事实

> 这一篇是从 `SKILL.md` 挪出来的：里面的数字**只对一台机器成立**（Windows 11 + Ryzen AI 9 HX 370 +
> OSGeo4W Python 3.12），却每次触发技能都要占上下文。换机后按本文件末尾「换机 60 秒自检」重测，
> 把结论写回你自己的工程笔记，不要指望这里的数字。

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
| 实测渲染 | PIL 中等复杂度场景 **40–54 fps**（1080p30）；自带示例片 **68–82 fps**（多次实测区间） |
| 磁盘 | 当时约 C 300 GB / D 520 GB（会持续变动，别当基准） |

### 各模块实测（同机）

| 模块 | 实测数据 |
|---|---|
| `sfxkit` | 24 配方单次渲染 **软预算 ≤ 1.5 s**（超了只 WARN，**硬上限 2× = 3.0 s** 才判算法退化；selftest 的 05 用例断言的是"性能不退化"、不是绝对秒数；空载最慢 wind 1.08–1.27 s，4 并发时 rain/wind 1.4–1.7 s（8 并发 1.8–2.5 s））；**389 s 长环境声** wind 5.7–7.8 / rain 7.3–7.9 / campfire 6.3–6.7 s（预算 8 s，随负载浮动）；真峰与 ffmpeg 差 **0.05 dB**；响度与 ebur128 差 **0.01 LU** |
| `musicgen` | 12 s 合成 **0.30 s**、30 s **0.78 s**；输出峰值 -6 dBFS（母带交给 loudnorm）。参数全部参与编排（seed/mood/voices/progressions 相关性均 < 0.95） |
| `scorekit` | 作为合成原语：389 s 全片 **4–8 s**、60 s 2–4 s；24-bit 落盘与响度测量仍被复用。**它不再是配乐引擎**，`--selftest` 只验原语与 legacy 逐样本兼容 |
| `webengine` | **80–130 ms/帧**（1080p30，Edge 无头，PNG 截图）；12 s / 360 帧 **28–30 s**（2 workers，含启动）；**同一页面会话内**同帧两次截图 byte 完全一致（跨会话默认 GPU 光栅有 ≤4 像素、±1 级抗锯齿差异，要跨会话字节级一致请开 `--deterministic`，代价约 +22%/帧） |
| `qa_frames` | 30 s 片全量深检 **约 1 s**（160×90 流式解码 903 帧）；闪烁/空窗/静止/同步 |
| 端到端 | 30.10 s 示例片：TTS 4 句 → cues 由 scenes.py 按 kind 生成（示例片 17 条）→ 配乐 3.3 s → 音效逐条落位 → 混音 → PIL 75 fps 渲染 12 s → mux → 深检 PASS |
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

## 附：SKILL.md 正文迁入（原 12 参考机器的已验证环境事实）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

## 12. 参考机器的已验证环境事实

> **这些是作者机器（Windows 11 / Ryzen AI 9 HX 370 / OSGeo4W Python 3.12）的实测基准，不是跨机器承诺。**
> 换机先跑 §13 的 60 秒自检，把结论写回自己的笔记——别照抄这里的版本号与路径。
> 完整表格（ffmpeg 构建、编码速度、各模块耗时、排期公式、三条隐形坑）见
> `references/machine-facts.md`。

## 附：SKILL.md 正文迁入（原 13 换机 60 秒自检）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

## 13. 换机 60 秒自检（比读文档快）

```bash
cd <SKILL_DIR>/scripts
python selfcheck.py                  # 110 条断言；FAIL 就照提示修环境
python sfxkit.py --selftest          # 音效库（可选模块，装了才跑）
python musicgen.py --selftest        # 配乐编排器（参数化）
python scorekit.py --selftest        # 配乐合成原语（不是配乐引擎）
python webengine.py --selftest       # 浏览器引擎（36 项，会真起无头浏览器，约 60 秒）
python qa_frames.py --selftest       # 帧级质检（自己造测试视频）
python <REPO>/tools/footage_selftest.py   # 素材剪辑线（离线造素材跑全链 + F1–F9 断言）
```

selfcheck 自己造测试信号、现场调 ffmpeg 编码，**不联网、不依赖任何成片**。
每条 FAIL 都对应一个具体环境缺口（缺字体 / ffmpeg 无 libx264 / 无 numpy / 缺 sfxkit），
报错信息直接写该改哪里。可选模块缺失时 selfcheck 标 `SKIP` 而不是 FAIL——
**别把"没装可选件"和"坏了"混为一谈**。

> **仓库级体检**是另一层，别和 selfcheck 搞混：
> `python <REPO>/tools/ci_check.py -v`（在包根执行）只查"这个仓库作为可分发包是否完整"（语法/JSON/依赖白名单/
> 打包卫生/文档路径引用），**不需要 ffmpeg、字体或网络**，任何机器都能跑；
> selfcheck 查的是"技能还能不能真的出片"。两层都在 CI 里跑。
>
> ci_check 第 12 项是「film.json 契约」（直接调 `scripts/filmcheck.py` 校验模板，并把
>`schemas/film.schema.json` 的存在与合法性一起断言）、第 13 项是「footage.json 契约」
>（用 `footagekit.load_footage` 载入 `assets/footage/footage.template.json`，并校验
>`schemas/footage.schema.json` 可解析）。两边的口径一致：**校验规则只允许有一份**，不在这里重写
>——素材剪辑线的契约同样只有一份真源（见 [references/footage.md](references/footage.md)）。
