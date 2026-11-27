# 卡点与动效：BPM 网格、节拍锚点、动效预设

> 何时读：做音乐卡点片（鼓点切镜头 / 音效贴拍）、或觉得"动作语言不统一"时。
> 只想快速上手：`--stage beats` 看 BPM，把 cue 的 at 写成 `beat:12`，跑 `--stage qa` 看 G14。

## 1. 为什么卡点要"测"而不是"填"

`film.json` 里的 `audio.music.bpm` 是**声明**，不是实测。三种情况会让它不准：

1. 音乐是外面找的，BPM 靠肉眼/网站估的（常见偏差 ±2 BPM，10 秒就漂半拍）；
2. 音乐是本技能现场编排的，`bpm` 只是生成参数，落盘后节奏仍有 ±几 ms 的相位偏移；
3. 用户改了 BPM 却没重测，`beat_data.json` 还是上一版。

卡点错位**不会让画面糊、也不会让响度不对**——它只是"看着不对"。所以必须有一条
量出来的门禁（G14），而不是靠"我觉得卡上了"。

## 2. `--stage beats`：测出网格

```bash
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage beats
```

产物 `work/beat_data.json`：

| 键 | 含义 |
| --- | --- |
| `bpm` / `T` | 拟合出的 BPM 与每拍秒数（`T = 60/bpm`） |
| `t0` | 首拍绝对秒（相位） |
| `residualMs` | 网格与真实瞬态的 RMS 残差，**越小越可信**（<15ms 好，>30ms G14 fail） |
| `confidence` | 命中的瞬态比例（0–1） |
| `beats[]` / `bars[]` | 逐拍 / 逐小节（4/4）绝对秒 |
| `hits[]` | `{t, s, k}`：该拍是 kick / snare / hihat，`s` 是强度 |
| `rms[]` / `sections[]` | 0.5s 能量曲线与段落（breakdown / body / climax）。**与是否测出节拍无关**：`ok=false` 的氛围乐/纯人声也照样给段落——这类片子做不了逐拍卡点，段落卡点就是唯一能用的结构信息 |
| `ok` / `why` | 没测出节拍时为 `false` 并给原因 |

**分析的是哪首曲子**：优先 `audio.music.file`（外部 BGM），其次 `work/music.wav`
（`--stage score` 现场合成的配乐）。所以自家配乐也能卡点。

算法是纯 numpy：三频段 STFT 能量 → 正差分 onset → 自相关给候选周期 →
**等距网格最小二乘回归**定稿。为什么不用自相关峰值当答案：自相关受倍频/半频干扰，
回归不会——实测 90/120/128 BPM 误差 <0.02 BPM、残差 <3.1ms。

不装 librosa。为一件"求节拍"的事拖进 numba/llvmlite（上百 MB、还挑 Python 版本）
不划算；本工具的口径是"装得上就能跑"。

## 3. 节拍锚点：cue 的 `at` 怎么写

`sfx.cues[].at` 与 `scenes.py` 的 `cues()` 都支持（`cue_time` 统一解析）：

| 写法 | 含义 |
| --- | --- |
| `beat:12` | 第 12 拍（下标从 0 开始） |
| `beat:-1` | 最后一拍（负下标从尾部数） |
| `beat:12+0.05` | 第 12 拍后 50ms（抢拍用负值） |
| `bar:3` | 第 3 小节（4/4，`t0 + 3*4*T`） |
| `bar:3.5` | 第 3 小节半（`bar` 允许小数） |

为什么要这种锚点：卡点片上"第 12 拍"才是人说话的语汇。写成 `abs:6.05` 之后，
换 BPM、重排轴、加一句旁白，这个数字就全错了，而且没人看得出它原本是哪一拍。

`cues.json` 每条会落盘 `at` 原串——它是**声明**，G14 靠它区分"卡点锚点"
（必须贴网格）与"旁白对齐锚点"（本来就不该贴网格）。

没跑 `--stage beats` 就写 `beat:` 会**明确报错**，不会静默落到 0 秒：

```
cue at='beat:3'：需要节拍网格，但 work/beat_data.json 不存在（先跑 --stage beats，或改用 abs:秒）
```

## 4. G14：卡点门禁（`--stage qa`）

| 情况 | 结论 |
| --- | --- |
| 没有音乐，也没有网格 | `skipped`（非卡点片，不假装通过） |
| 有音乐但网格不存在 | `fail`（先跑 beats） |
| 网格 `ok=false` | `fail`（有音乐却定不了拍） |
| `residualMs > 30` | `fail`（等距不成立，卡点必漂） |
| `beat:`/`bar:` cue 离最近拍 > 1 帧 | `fail`（列出前几条与偏差毫秒） |
| 以上都没有 | `pass`（报 BPM / 拍数 / 残差 / 核对了几条 cue） |

1 帧 = 33ms（30fps）。放宽到 1 帧是因为"抢拍/拖拍"是合法表演手段，但超过一帧就
不是表演而是没卡上。

## 5. 动效预设：动作语言要成套

`designkit.motion_for(energy, tone)` 把四个动效参数绑成六到九套"动作口音"：

| energy | tone | ease | enter | breathe_hz |
| --- | --- | --- | --- | --- |
| calm | light | out3 | (0.30, 0.70) | 0.30 |
| calm | bright | inOut3 | (0.35, 0.80) | 0.40 |
| steady | bright | outBack | (0.45, 0.95) | 0.60 |
| driving | dark | out5 | (0.25, 0.70) | 0.90 |
| …（见 `MOTION_PRESETS`） | | | | |

- `energy` 由 `mood` 推：`calm`/`reflective` → calm；`hopeful`/`steady` → steady；
  `driving`/`tense`/`uplifting` → driving。和配乐 BPM、pacing 用的是**同一个 mood**，
  所以声音、动作、节奏同向变化。
- 套内允许小抖动（`breathe_hz` ±10%、`enter` 终点 ±0.05），足够让候选之间有差别，
  又不会拼出"弹跳 + 极慢呼吸 + 无推镜"这种自相矛盾的组合。
- 想覆盖：手改 `design.json` 的 `visual.motion`（schema 校验 `ease/enter/push/breathe_hz`）。

## 6. 参考片逆向（`tools/refscan.py`）

```bash
python tools/refscan.py --video 参考片.mp4 --out work/refscan
```

产物：`sheet.png`（切点帧联系表）、`shots.json`（逐镜 start/dur/motion/luma/sat）、
`REVERSE.md`（三列表：**手法 | 参考片实现 | 要不要抄** + 实测指标）。

用它的正确姿势：

1. 先读 `REVERSE.md` 的实测指标表，别凭感觉说"这个片子节奏快"；
2. 逐镜填"手法"与"参考片实现"，**取舍那一列必须写**；
3. 抄不动的（真人表演、航拍、版权素材）明说放弃，不要在设计上假装有；
4. 结论落成可执行的一条句，再回 `film.json` / `scenes.py` 去改。

它只量画面，不量声音；也不联网、不改参考片。

## 7. 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 测出 60/180 BPM | 自相关抓到倍频/半频 | 已做等距回归，通常已修正；仍偏就传 `audio.music.bpm` 当 hint |
| `ok=false` | 音乐没有明确鼓点（氛围乐/纯人声） | 正常：这类片子不该做逐拍卡点，改做段落卡点（`sections[]` 仍然可用） |
| 残差 > 30ms | 音乐不是等距节拍（自由速度/现场演奏） | 不做逐拍卡点，只卡小节或段落 |
| 音效整片偏半拍 | `beat_data.json` 是旧版（BGM 换过） | 重跑 `--stage beats`；G14 会报出来 |
| cue 报"越界" | 片长变了，拍数变少 | 用 `beat:-1` 代替绝对序号，或重排轴后重写 |

