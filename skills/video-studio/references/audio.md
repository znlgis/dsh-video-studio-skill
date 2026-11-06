# 音频：采样级混音、配乐与音效

## 1. 为什么不用 ffmpeg 混音

一条 ffmpeg 命令喂 100+ 个输入做混流，在 Windows 上会产生**非确定性静音**：输入线程池竞争导致某个输入未就绪就被读取，断媒体收到空流，后续输出静音。同一条命令连跑三次：正常 / 静音 / 正常。

**正确做法**：全部解码成 float32 PCM，在 numpy 总线上按采样点精确叠加，写 WAV，再一次性喂给 ffmpeg。

```python
bus = K.AudioBus(total=600.0, sr=48000)
bus.place(pcm, at=12.35, gain=1.0, fade_in=0.02, fade_out=0.06)
bus.place(music_seg, at=0.0, gain=0.35, loop=True, fade_in=1.5, fade_out=2.0)
wav = bus.mix(normalize=True, fade=0.6)
```

优点：确定性（同输入必同输出）、精度 1/48000 s、可单层重放、可任意点插入、可中途打印峰值自检。

## 2. 音频参数铁律

| 参数 | 值 | 说明 |
|---|---|---|
| 采样率 | 48000 | 视频配音标准，AAC 原生支持 |
| 位深 | float32 内部 / int16 落盘 | 内部保留余量，落盘前才量化 |
| 声道 | 单声道总线 → 双声道输出 | 旁白单声道足够 |
| 母带响度 | **-16 LUFS ± 1** | 网络视频标准 |
| 真峰 | **≤ -1 dBTP** | AAC 编码会推高约 0.9 dB，混流前留 1.4 dB 余量 |
| 旁白与间隙比 | ≥ 12 dB | 人声必须明显突出 |
| 配乐电平 | 旁白以下 12–18 dB | 否则听不清字 |

## 3. 响度链路（三步）

```python
# 1) 各轨目标 LUFS：旁白 -13.5 / 音效 -24.5 / 配乐 -26.5
# 2) 用 ebur128 实测每条轨积分响度，算增益差额
# 3) 差额用"前瞻限幅器"补，而不是整体拉高
# 4) 母带：两遍 loudnorm 到 -16 LUFS / -1.5 dBTP
af = K.loudnorm_two_pass("work/audio.wav", target_i=-16.0, target_tp=-1.5)
```

**不要用 RMS 近似 LUFS**：RMS 是线性平均，LUFS 有 K 计权 + 门限，误差可达 6–8 dB。
**不要整体拉高补响度**：会把峰值推爆。正确顺序是"分轨对齐 → 限幅压峰 → 母带归一"。

## 4. 程序化配乐（numpy 合成）

### 4.1 和弦铺底

```python
def pad_chord(freqs, dur, sr=48000, amp=0.05, detune=0.004):
    n = int(dur * sr); t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    for f in freqs:
        for d in (-detune, 0.0, detune):          # 三声部微失谐 = 合唱感
            out += amp * np.sin(2 * np.pi * f * (1 + d) * t)
    return (out * env / max(1, len(freqs))).astype(np.float32)
```

和弦进行模板：
- 抒情/纪录：Am–F–C–G（A2 110, F2 87.3, C3 130.8, G2 98.0 Hz）
- 推进/数据：Dm–Bb–F–C
- 收尾转大调：把最后一段根音升高小三度

### 4.2 节奏型

```python
kick = K.tone(55.0, 0.28, amp=0.5, decay=14.0)      # 底鼓
hat  = K.noise_burst(0.05, amp=0.12, decay=60.0)     # 踩镲
```

### 4.3 段落响度叙事

同一张和弦表，按段落改配器与电平：叙事段比推进段低 **4–6 dB**，高潮段多加一层八度。观众感觉"有起伏"，但换段不会有突兀的电平跳变。

## 5. 程序化音效（物理建模）

**核心通则：激励用加速度（接触力的一阶导），不用位移。**
- 位移激励 → 频谱质心只有约 200 Hz，听起来像闷鼓
- 加速度激励 → 质心提到 900+ Hz，才像"敲击"

```python
def impact(dur=0.35, sr=48000, freq=180.0, decay=18.0):
    n = int(dur*sr); t = np.arange(n)/sr
    body = np.sin(2*np.pi*freq*t) * np.exp(-decay*t)
    body = body * (-(2*np.pi*freq)**2)          # 二阶导 = 加速度激励
    click = np.random.RandomState(7).randn(n).astype(np.float32) * np.exp(-decay*3*t)
    body = body / (np.max(np.abs(body)) + 1e-9)
    return (0.3*body + 0.12*click).astype(np.float32)
```

常用配方：

| 音效 | 做法 |
|---|---|
| 纸张翻动 | 短噪声 8–20 ms + 带通 2–6 kHz + 快衰减 |
| 笔尖/墨水 | 极短噪声 + 高通 + 随机微延时（沙沙感） |
| 键盘/机械 | 冲击 + 金属共振（几个固定频率正弦，衰减 8–12） |
| 转场 whoosh | 噪声 + 带通频率扫描 500 Hz→4 kHz + 0.3 s 包络 |
| 数字落定 | 正弦 660/880 Hz + 极快衰减 + 短混响尾 |
| 环境风声 | 粉噪声 + 带通 200–800 Hz + 慢 LFO 调制幅度 |

## 6. 循环环境音（无缝）

**频域整周期噪声法**：在频域生成噪声，只保留整数个周期，逆变换后首尾天然相接。

```python
def loop_noise(dur=8.0, sr=48000, lo=180, hi=900, seed=5):
    n = int(dur*sr)
    rng = np.random.RandomState(seed)
    spec = np.fft.rfft(rng.randn(n).astype(np.float32))
    freqs = np.fft.rfftfreq(n, 1/sr)
    band = ((freqs >= lo) & (freqs <= hi)).astype(np.float32)
    win = np.hanning(64); win = win / win.sum()
    band = np.convolve(band, win, mode="same")     # 软化边缘防振铃
    out = np.fft.irfft(spec * band, n).astype(np.float32)
    return out / (np.max(np.abs(out)) + 1e-9) * 0.3
```

接缝跳变比 < 1.5 视为无缝（首尾样本差 / 常态相邻样本差）。

## 7. 旁白处理

```python
# edge-tts 每条尾部约 0.95 s 静音，55 条累计 61.5 s
# v0.2 起 filmkit.tts(trim=True) 默认裁掉首尾静音，并把裁剪后的 wav 作为 decode 源：
#   meta.path -> xxx.trim.wav, meta.dur = 裁剪后实测时长；原 mp3 保留在 meta.mp3
# 缓存 key = sha1(text|voice|rate|pitch)；改台词会自动重新合成（v0.1 只判文件存在）
pcm = K.decode_pcm(mp3)
trimmed = trim_tail(pcm, threshold=0.002, keep=0.12)

# 轻 EQ：200 Hz 以下衰减 2 dB（去闷），3 kHz 提 1 dB（清晰度）
```

角色多时固定音色映射：少年 YunxiaNeural、成年男 YunjianNeural、叙述 YunyangNeural、女声 XiaoxiaoNeural；同一角色全片不换。

## 8. 混音自检（必做）

```python
# 每 20 秒窗口测 RMS，低于 0.002 记 SILENT —— 防"后半段全程静音"
for i in range(0, len(wav), 20*48000):
    seg = wav[i:i+20*48000]
    if np.sqrt(np.mean(seg**2)) < 0.002:
        print("SILENT at %.0fs" % (i/48000))

print("peak:", np.max(np.abs(bus.bus)))      # 期望 < 0.99
```

## 9. 常见音频故障

| 现象 | 根因 | 修法 |
|---|---|---|
| 后半段静音 | ffmpeg 多输入混流 | numpy 总线 |
| 爆音/削波 | 先 astype 再判 dtype | 先判 dtype 再转；打印中间峰值 |
| 听起来糊 | 配乐电平过高 | 配乐压到旁白以下 12–18 dB |
| 响度上不去 | AAC 推高真峰后限幅 | 混流前留 1.4 dB 余量 |
| 环境音接缝咔 | 直接拼接 | 频域整周期噪声 |
| 配音机械 | 语速超过 +15% | 降到 +6% 左右，加自然停顿 |
| 字幕比声音慢/快 | 按字数估时长 | 实测反推 |

## 10. 两条混音路线的取舍（含实测坑）

### 路线 A：numpy 采样级叠加（推荐）

完全确定、精度 1/48000 s、可单层重放、可中途打印峰值。`filmkit.AudioBus` 就是这条。

### 路线 B：ffmpeg 滤镜链（轻量场景可用）

单遍完成，不用把 PCM 都拉进内存：

```bash
ffmpeg -i voice.wav -i bgm.wav -filter_complex \
  "[1:a]volume=0.13[b];\
   [0:a][b]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[m];\
   [m]loudnorm=I=-16:TP=-1.5:LRA=11[o]" \
  -map "[o]" -c:a aac -b:a 192k out.m4a
```

**三个必须记住的参数**：

| 参数 | 不这么写会怎样 |
|---|---|
| `amix=...:normalize=0` | 默认 `normalize=1` 按输入数均分，2 路输入直接**掉 6 dB** |
| `amix=...:duration=first` | 否则按最长一路，可能被循环 BGM 拉到无限长 |
| 显式 `-t <总时长>` | `-stream_loop -1` 会让 `-shortest` 失效，产出 13 小时的怪物文件 |

### 不要用 adelay 铺轨

`adelay=ms|ms` 存在**系统性约 -0.6 s 偏移**（实测），越长的片子错得越明显。要精确对位就用 numpy 按采样点写位：

```python
i0 = int(round(at * sr))
bus[i0:i0+len(src)] += src * gain      # 精确到 1/48000 秒
```

### 首镜必须从 0 开始

历史事故：第一个镜头 `start ≠ 0`，导致全片音画错位 1.2 s（画面先跑，声音从 0 开始）。**时间轴第一段 start 必须严格等于 0**，且 `voice_start ≥ start`。

### BGM 循环的淡出陷阱

BGM 素材自带淡出尾音，被 `-stream_loop` 循环后会在每个接缝处"死一下"。做法：循环时只取素材的**稳态段**（裁掉头尾淡入淡出），或改用频域整周期噪声。
