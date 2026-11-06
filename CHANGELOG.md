# 更新日志

版本号规则：`主.次.修订`；技能正文（SKILL.md / references / scripts / assets）有实质改动才升次级。

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
