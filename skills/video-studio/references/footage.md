# 素材剪辑线（footage 模式）

> 一句话：**画面从现成素材来，声音和时间轴仍然由代码保证。**
> 零素材线（render.py + design.json）解决"没有画面可拍、要自己画"；
> 这条线解决"有画面素材（下载的、自己的、手上的片子），要按文案剪成片"。

## 0. 什么时候走这条线（分诊）

按顺序判断，**先素材后自绘**：

| 顺序 | 情形 | 走哪条 |
|---|---|---|
| 1 | 用户给了素材（视频 / 图片 / 链接 / 一个片子文件） | **footage** |
| 2 | 用户没给，但网上能找到可用画面（纪录片、新闻片、公有领域影像） | **先 fetch，再 footage** |
| 3 | 都找不到，或题材不该用现成画面（数据、抽象、品牌、动画） | 零素材线（render.py） |

两条线共用底盘：TTS 实测时长 → 时间轴 → numpy 采样级混音 → 两遍 loudnorm → 质检报告。
区别只在"画面从哪来"：零素材线来自 draw_frame(t, frame)，素材线来自 sources[].at + len。

> **素材不必提前准备好**：sources[] 可以直接写 url，--stage fetch 负责下载；
> 也可以写本地 path（推荐用于回归与离线复现）。

> **这条线由问诊第 2 题决定**：`intake.py` 的 `mode` 若解析成 `footage`（用户自己选了素材剪辑，
> 或选了 `auto` 且题材是真实影像），`--finalize` 会直接给出 `footage.py` 的下一步命令，
> 并提醒**提案层不适用**。走到这里说明产线已经定过了，别再回头跑 `propose.py` 给素材线定外观。
> `mode=hybrid` 时：素材段走本线，图文段走 `render.py`，最后 ffmpeg 拼接/叠加（没有一键命令）。

## 1. 契约：footage.json

与 film.json **并列、不通用**。完整字段见 schemas/footage.schema.json，
可跑通的模板见 assets/footage/footage.template.json。

```jsonc
{
  "title": "片子名",
  "sources": [
    {
      "id": "film1983",
      "kind": "video",
      "path": "media/source.mp4",          // 或 "url": "https://..."
      "license_note": "出处/授权说明",       // F5 要求非空
      "burned_subs": {"y0": 750, "y1": 824},                 // 原片烧录字幕带（源像素）
      "watermark": {"x": 1580, "y": 16, "w": 316, "h": 44}   // 原片水印框（源像素）
    }
  ],
  "treatment": {                            // 画面治理：一次定义、全片复用
    "crop": "1576:660:172:80",
    "scale": "1920:804",
    "letterbox_color": "0x0b0c0e"
  },
  "video": {"width": 1920, "height": 1080, "fps": 30},
  "audio": {
    "voice": "zh-CN-YunyangNeural", "rate": "-6%",
    "env_gain": 0.12,                       // 素材原声（环境层）
    "music_gain": 0.10,
    "music": {"bpm": 92, "key": "Dm", "scale": "minor", "mood": "steady", "seed": 11}
  },
  "subtitle": {"size": 46, "margin_v": 38},
  "credit": {"text": "素材：<作品名>（<出品方>，<年份>）", "at": 188.0},
  "pacing": {"lead": 0.35, "tail": 0.55, "outro": 2.2},
  "lines": [
    {"id": "s01", "text": "朗读文本", "sub": "上屏字幕",
     "shot": {"source": "film1983", "at": 175.0, "why": "开场空镜"}}
  ]
}
```

四条硬规则：

1. **shot.at 是源文件内绝对秒数**，len 可省（省掉就跟随该句旁白的实测时长）。
2. **每句都要 shot**：只有一张图就一路指同一个源，at 不变。
3. **burned_subs / watermark 是门禁的输入**（F2/F3 做几何判定），
   --stage probe 会打印建议值，直接粘进 sources[]。
4. **license_note 与 credit.text 是交付条件**（F5），缺了直接 fail。

## 2. 八个阶段

```bash
python <SKILL_DIR>/scripts/footage.py --footage footage.json --work work --out out/film.mp4 --stage all
```

| stage | 做什么 | 落盘 |
|---|---|---|
| probe | 素材体检（时长/分辨率/有无音轨），给治理建议与声明值 | work/sources.json |
| fetch | 下载/落地素材（直链走 urllib，页面走 yt-dlp；sections 只下时间窗） | sources[].path / dl/ |
| transcribe | faster-whisper 转写（喂 float32 PCM，绕开 PyAV 版本坑） | work/transcripts/*.json |
| locate | 关键词定位镜头（**中文关键词不要走 shell 参数**） | work/locate.json |
| tts | 逐句旁白 + 实测时长（复用 filmkit.tts_batch） | work/tts/、work/tts.json |
| edl | 时间轴 + EDL（越界镜头在这里拦住） | work/timeline.json、work/edl.json |
| cut | 按 EDL 切镜（统一画幅 + 抽原声当环境层） | work/segs/ |
| mix | 三总线混音（旁白 1.0 / 原声 0.12 / 配乐 0.10） | work/mix.wav |
| assemble | 拼接 + ASS 字幕 + 两遍 loudnorm | 成片 |
| qa | F1..F7 门禁 + 通用质检 | out/QC-REPORT-footage.md |

--stage all 只跑出片必需阶段；素材都在本地时自动跳过 fetch，
transcribe / locate 是**定位镜头**用的准备阶段，单独跑。

## 3. 素材定位：从"我要讲这句话"到"用第几秒"

素材线最耗时的一步是**找画面**。流程：

```bash
# 1) 转写（全片音轨，small 模型在 CPU 上约 15–20 倍速）
python footage.py --footage footage.json --stage transcribe --model small

# 2) 关键词定位（一次可给多个词；返回候选时间码 + 上下文）
python footage.py --footage footage.json --stage locate --keywords "土城,茅台,乌江,金沙江"

# 3) 把候选时间码填进 lines[].shot.at，再跑 edl/cut/...
```

三条经验（都是踩出来的）：

1. **中文关键词不要走 shell 命令行参数**。Windows 控制台是 GBK，
   python x.py 茅台 传进 Python 的字符串已经乱码，在 UTF-8 转写文本里永远 0 命中——
   会让人误判"转写里没有这个词"。要么走本脚本的 --keywords（Python 内处理），
   要么把关键词写进 JSON 再读。
2. **地名容易被转写错**：茅台/毛台、娄山关/楼山关、土城/土成 会混着出现。
   给同义词，或用 --n 多看几条，别只看第一条。
3. **先用 at 落点，再看 1–2 秒偏移**。电影的镜头切换很密，差 1 秒就换了画面；
   cut 出来的片段先抽帧看一眼，不对就调 at（改一句重跑只要几秒）。

## 4. 画面治理：烧录字幕、水印、画幅

素材常带三种"不该出现在成片里"的东西：**烧录中文字幕**、**平台水印**、**不统一的画幅**。
treatment 一次定义、全片复用：

| 字段 | 作用 | 典型值 |
|---|---|---|
| crop | w:h:x:y，先裁（去字幕带、去水印带、去黑边） | 1576:660:172:80 |
| scale | w:h，再缩放（等比；不拉伸） | 1920:804 |
| color | 最后调色（eq=...），统一不同素材的色差 | eq=saturation=0.92:contrast=1.04 |
| letterbox_color | 补遮幅的颜色 | 0x0b0c0e |

选配方的顺序（不要反）：

1. --stage probe 看建议值（默认把底部 12% 当字幕带、右上 8% 当水印区）；
2. **抽一帧亲眼看**字幕带到底占哪几行（有些片字幕在正中间，自动裁底部反而切一半）；
3. 把真实区间写进 burned_subs / watermark，让 F2/F3 能判；
4. 定 crop，确认 at + len 都在源时长内。

> **别用 delogo 硬盖水印**：实测会留下竖条纹，比水印本身更显眼。
> 能裁就裁，裁不掉就换素材。

## 5. 门禁（F1–F7）

```bash
python footage.py --footage footage.json --stage qa          # 出 out/QC-REPORT-footage.md
python footage.py ... --stage qa --strict-qa                 # fail 即失败（交付前建议开）
```

| 门禁 | 查什么 | 判据 | skipped 什么时候出现 |
|---|---|---|---|
| **F1** | 镜头越界 / EDL 合法性 | at+len ≤ 源时长；镜头总长与时间轴差 ≤0.6s | 没有 EDL |
| **F2** | 原字幕残留 | burned_subs 与 crop 后可见区**几何相交** | 源没声明 burned_subs |
| **F3** | 水印残留 | watermark 与可见区几何相交 | 源没声明 watermark |
| **F4** | 音画等长 | 视频与音频时长差 ≤ 0.05s | 读不到流时长 |
| **F5** | 素材溯源 | 每个源有 license_note + 有 credit.text | 不会 skipped（缺就是 fail） |
| **F6** | 旁白/原声比例 | 旁白窗口 RMS 比间隙 RMS 高 ≥12 dB | 旁白首尾相接、没有间隙窗口 |
| **F7** | 人工签核 | 联系表存在 + 签核指纹匹配 | 未签核 / 内容已变 |

**F2/F3 为什么是几何判定而不是"看画面像不像有字"**：实测过图像判据（底部/中部能量比、
亮像素占比、跨帧静止度），在真实素材上正反例区间重叠——已治理的成片 1.41、
未治理的原片 1.81，任何阈值都会误杀一类。而"该裁的地方裁了没有"是几何问题，
有唯一正确答案。所以门禁只认声明 + 可见区，图像指标仅作为人工复核线索打印出来。

### 签核（F7）

--stage qa 会生成 work/qa/footage-sheet.png（每镜一帧 + 镜头号/时间码）。
看过之后写 work/footage-signoff.json：

```json
{"fingerprint": "<报告里的指纹>", "verdict": "approved", "by": "user", "note": "看过"}
```

指纹 = footage.json 的 line 文本 + shot + treatment + subtitle + EDL 的哈希。
**改了台词或镜头，旧签核自动失效**（F7 变 skipped 并写明"内容已变"），必须重新看片。

## 6. 常见故障

| 现象 | 根因 | 修法 |
|---|---|---|
| 成片比旁白短、片尾被吃 | 尾留白没并进最后一镜 → 视频短于音频 | 已修：build_timeline 把 outro 并进最后一镜 |
| 音轨全空 / 混音没声音 | --download-sections 只给视频流，没有音轨 | 另下一路音轨，或换整段下载；probe 会报 audio=NO |
| 画面变形（人变瘦/胖） | scale 与 crop 宽高比不一致，被强行拉伸 | crop 后按**同比例**写 scale；或让 scale 省掉自动算 |
| 字幕烧不上去 / ffmpeg 报 No option name near | ass=filename='D:/a/b.ass' 里盘符冒号被当参数分隔 | 用相对文件名 + cwd（footageedit.assemble 已处理） |
| 成片底部还挂着原片字幕 | crop 没盖住字幕带 | 看 F2 报的区间，改 crop 或 burned_subs |
| 右上角台标还在 | crop 只裁了上下，没裁左右 | 水印在角上时，crop 要同时缩 x 与 w |
| 后段听不清旁白 | 原片自带解说，env_gain 太高 | 降到 0.06–0.12；F6 会量"旁白/背景差" |
| 转写一条词都没有 | 中文关键词走 shell，或 faster-whisper 没装 | 走 --keywords；或 pip install -U faster-whisper |
| locate 命中一堆不相干的 | 转写把地名转错 | 给同义词、加时间窗、看上下文再定 |
| 改了台词但镜头没变 | EDL 用了显式 len（不跟旁白走） | 删掉 shot.len，让镜头跟随实测时长 |

## 7. 交付前对照清单

1. --stage qa --strict-qa 无 fail；报告里每个 skipped 都能说出为什么。
2. out/QC-REPORT-footage.md 里的时长/响度/真峰抄进回复，不要凭印象说"没问题"。
3. work/qa/footage-sheet.png **亲眼看过**，并在回复里说出具体观察（哪几镜、有没有错位）。
4. 片尾出处卡在成片里（F5 pass 只是"写了"，还要看它真的出现在画面上）。
5. python tools/footage_selftest.py 全绿（改了这条线的代码就跑一次，离线约 30 秒）。
