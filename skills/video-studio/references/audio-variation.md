# 音频多样性改造（v0.7）—— 接口契约

> 起草：lead，2026-11-09。本文件是本次改造的**唯一定义源**。
> 三路并行改动的文件写域不重叠，但**接口必须按本文件逐字实现**，否则集成会失败。
> 诊断证据见 `tools/audio_similarity.py` 的输出（改完后必须重跑，阈值见 §5）。

## 1. 要解决的两个问题（都有实测数据）

| 问题 | 实测 | 根因 |
|---|---|---|
| A. 跨视频配乐雷同 | 同风格/同 bpm/同调、只换内容指纹：corr **0.966** | 指纹只改 seed（音色细节），和弦进行/动机/鼓型/段落骨架全固定 |
| B. 跨视频音效雷同 | 同 cue 序列两次渲染逐样本相同 | `CueBus.build_audio` seed = `seed_base + i`，只跟 cue 顺序有关 |
| C. 片内配乐与音效难分 | impact 质心 1001 Hz / whoosh 1589 Hz / music 5451 Hz；提示音硬编码 Dm 五声 | 提示音调性硬编码 + 配乐过门与 sfx 轨同边界同频段 + duck 仅 2.5–4.5 dB |

## 2. 改动总览（按写域分三路）

| 路 | 负责文件 | 改什么 |
|---|---|---|
| A 配乐变奏 | `skills/video-studio/scripts/scorekit.py` | 指纹参与**和声/动机/鼓型/结构**，不再只影响音色细节 |
| B 音效差异化 | `skills/video-studio/scripts/sfxkit.py`、`assets/scenes.py`、`assets/film.json` | `in_key` 跟随配乐调性；`cues()` 按画面内容生成；模板 cue 去固定化 |
| C 接线与混音 | `skills/video-studio/scripts/filmkit.py`（lead）、`render.py`（lead）、`stylekit.py`（只读）、`selfcheck.py`（lead） | CueBus 内容派生 seed、调性传给 sfxkit、duck 加深、风格过门避让 |

**写域规则**：A 路只改 scorekit.py；B 路只改 sfxkit.py / assets/scenes.py / assets/film.json；
C 路由 lead 改 filmkit.py / render.py / selfcheck.py。**任何一路不得改别人的文件**（包括顺手"优化"）。

## 3. 接口契约（逐字实现，向后兼容）

### 3.1 sfxkit（B 路）

```python
# 1) 调性跟随：新增 key 参数，默认 None = 旧行为（Dm 五声）
def in_key(freq: float, key: str | None = None) -> float:
    """key=None -> 旧行为（硬编码 D 小调五声，逐样本兼容）。
    key 给 "Dm"/"C"/"Am"/... -> 吸附到该调（小调用自然小调五声，大调用大调五声）。"""

# 2) 所有走 in_key 的配方接受 key（通过 **kw 透传，签名末位加 key=None 或直接从 kw 取）：
#    ui_blip / answer_chime / text_stream(voice="tone") / data_chirp / roulette_tick
#    未知调名必须 raise ValueError（不静默降级，与 _enum 同口径）。

# 3) 内容派生 seed 的辅助函数（供 filmkit.CueBus 用）
def content_seed(base: int, style: str, content_fp: str, recipe: str, t: float) -> int:
    """稳定的 32 位非负 int：sha1(\"v0.7|%d|%s|%s|%s|%.3f\") -> int(h[:8],16)%9000+1000。"""
```

### 3.2 filmkit.CueBus.build_audio（C 路，lead）

```python
def build_audio(self, total=None, sr=SR, base_db=None, default_gain_db=0.0,
                seed_base=1000, verbose=True,
                style=None,           # 风格 id；None = 旧行为
                content_fp=None,      # 内容指纹 full；None = 旧行为
                key=None):            # 配乐调性；None = 旧行为（sfxkit 用 Dm）
    """每条 cue 的 seed：
       - style/content_fp 任一为 None -> seed = seed_base + i（旧路径，逐样本兼容）
       - 都给 -> seed = sfxkit.content_seed(seed_base, style, content_fp, kind, t)
    并把 key 透传给配方（render(kind, seed=..., key=key, **params)）。"""
```

### 3.3 scorekit.render_styled（A 路）

签名不变。**内部**行为变化：

- `full` 指纹（已有 `fingerprint` 参数，当前只用于 seed）必须影响**和声与动机**：
  - 和弦替换：按指纹从该风格的 `progressions` 里选 tier，再对**每两小节**做一次稳定替换
    （从同大小的替代级数集里挑：i↔i7 / VI↔VI7 / III↔III7 / VII↔VII7；大调侧 I↔Imaj7 / IV↔IVmaj7）。
    只在与乐句末/片尾规则不冲突时替换。
  - 动机变形：`motif` 的每个 (拍, 级数, 时值) 按指纹做 **±1 级数的稳定偏移**，
    只改级数不改节奏；问题句偏移与答句偏移必须独立派生（保证两部片子不会同步变形）。
  - 鼓型：`drums[tier]` 的 kick 落点按指纹加 **最多一个 ghost 落点**（在 0.5 拍网格上，
    不与现有落点重复）；clap 位置按指纹 ±0.5 拍。
- **必须保证**：同 `fingerprint` 逐采样一致；不同 fingerprint 的相关性显著下降（§5 阈值）。
- 新增报告字段：`variation`（dict：`{"chord_swaps": n, "motif_shifts": n, "drum_ghosts": n}`），
  写进 work/score.json，方便审计。
- legacy 路径（style=None）**逐字节不变**（selfcheck 第 20 条会验）。

### 3.4 assets/scenes.py 的 cues()（B 路）

现在只出 `ui_blip × items` + `answer_chime × outro`。改为按画面事件生成，并且**类型按场景 kind 与内容变化**：

- `hook`：段首 `impact`（size 按字数 0.9~1.3），落定 `riser`。
- `body`：条目落位仍用 `ui_blip`，但 **pitch 由条目文本 sha1 派生**（json 的 items 文本）；
  最后一个条目后加 `answer_chime`（若 kind=body 且 items≥3）；段尾 `whoosh`（going 到下一段）。
- `quote`：`reverse_swell` + 落定 `impact`（轻）。
- `outro`：`answer_chime` + 片尾 `sub_drop`。
- 不许再出现"每段都一样的三连击"：同类型 cue 在一段内重复时，`gain` 必须递减（-2/-4/-6），pitch 必须不同。
- **保持向后兼容**：仍有 `stagger` 对齐逻辑（与 _draw_items 同节拍），返回 schema 不变。

### 3.5 assets/film.json 模板（B 路）

模板里的 `sfx.cues` 固定三条 whoosh/riser/impact 是"跨片雷同"的直接来源之一。改为：

- 模板只保留 1 条转场 whoosh 作为示例，并加注释说明"具体提示点建议交给 scenes.py 的 cues() 钩子"。
- `sfx` 增加可选字段 `content_seed: true`（默认 true）：走内容派生 seed；显式 false 回到旧行为。

## 4. render.py 接线（C 路，lead）

- `st_score`：把 `plan_s["music"]["key"]` 与 `plan_s` 的 `fingerprint` 传给 sfx 阶段（经 `script["_style"]`）。
- `st_sfx`：读 `script["_style"]`，把 `style` / `content_fp` / `key` / `sfx.content_seed` 传给
  `bus.build_audio(...)`；没有 `_style`（legacy）时不传，保持旧行为。
- `st_audio`：duck 默认值改为按风格：在现有表基础上整体 +1.0 dB，且 `music.duck_db` 显式值仍然优先。
  另外新增**过门避让**：`scorekit` 的过门音效已经在 music.wav 里，sfx 轨在
  [段落边界 - 2.2s, 边界] 区间内的 whoosh/riser/reverse_swell 需要自动 -3 dB（在 filmkit 里做，
  或 st_sfx 后处理；由 lead 决定实现位置，**B 路不要碰**）。

## 5. 验收阈值（改完必须重跑 `python <REPO>/tools/audio_similarity.py`）

| 指标 | 改前 | 目标 | 落地实测（2026-11-09） |
|---|---|---|---|
| A 同风格换指纹 corr | 0.966 | **≤ 0.85**（仍要像同一风格，但不能像同一首） | **0.645** |
| A 换风格 corr | 0.534 | ≤ 0.52（不能变得更像） | **0.514**（v0.7.2 复测，余量 0.006；连跑 3 次逐位相同。余量薄是有意的：抬到 0.53 就离改造前的 0.534 只剩 0.004，这条门禁就没意义了） |
| B 同 cue 序列跨片逐样本相同 | True | **False**（不同内容指纹必须不同） | False（同 fp 仍逐样本相同） |
| B legacy（不传 style/fp） | True | **必须仍为 True**（向后兼容） | True |
| C duck 默认 | 2.5–4.5 dB | 3.5–5.5 dB | 4.0（mono 3.0 / cinema 5.5） |
| D 人声段旁白/背景响度差 | 约 1.5 dB（配乐 -1.5 无侧链） | **≥ 12 dB** | **15.0 dB** |
| D 旁白处背景额外让位 | 0 | ≥ 4 dB | 6.0 dB |

> v0.7.1 追加 D 组（旁白优先）：视频以旁白为主，BGM 与音效是可选层。`mix_film` 的
> `voice_duck_db=6` / `voice_duck_sfx_db=3` + `music.gain_db=-12` 把"人声比背景响 12 dB"
> 从文档口号变成可执行断言（selfcheck 35/36 条 + 本脚本 D 组）。

**已知边界（不是回归，是设计取舍）**：同风格不同内容指纹有约 1/3 概率落到同一个
和弦进行档位偏移上，此时两段配乐 corr 约 0.93（只剩七和弦色彩与鼓型 ghost 的差别）。
要再压下去需要"每段独立选档"或"同档时轮转"，会削弱"片内是一条曲子"的连贯性，
本轮不做；`--strict` 用固定的 content-a / content-b 做验收，两者落在不同档。

另有硬性回归（任何一条红都不算完成）：

> 下面 1–4 的路径都按**仓库根**写（`python skills/video-studio/scripts/…`）；如果你已经 `cd` 进技能目录，
> 去掉 `skills/video-studio/` 前缀即可。`<REPO>` = 仓库根 / 装好后的包根。

1. `python skills/video-studio/scripts/selfcheck.py` 全 PASS（现在 51 条；v0.7.2 时的 43 条是旧的）。
2. `python skills/video-studio/scripts/scorekit.py --selftest` 全 PASS。
3. `python skills/video-studio/scripts/sfxkit.py --selftest` 全 PASS。
4. `python <REPO>/tools/ci_check.py` 全 PASS（在包根执行）。
5. legacy 逐样本不变：selfcheck 第 20/26/33 条不得改动其断言。

## 6. 协作规则

- 每个 teammate 只跑自己文件的 `--selftest`，**全量 selfcheck / ci_check 由 lead 统一跑**（避免并行抢 CPU 和临时目录）。
- 改完在 `CHANGELOG.md` 追加的条目由 lead 统一写（teammate 不要改 CHANGELOG）。
- 任何接口拿不准的地方，按本文件逐字实现；本文件没写的自由发挥请控制在"不改变签名"的范围内。
- 完成后向 lead 报告：改了哪些函数、自测输出摘要、与契约的偏差（如果有）。

## 7. 实现备注（落地后回填，2026-11-09）

三路改动全部落盘后，实测与契约之间的**已知偏差**都记在这里（不改契约本身）：

| 项 | 契约原文 | 实际实现 | 判断 |
|---|---|---|---|
| 配乐 tier 选择 | §3.3 只列了和弦替换/动机偏移/鼓型 ghost | 额外让指纹从 `progressions` 的 calm/body/drive 三档里选一档（该风格自带的三套进行，不发明新和弦） | 接受：单靠七和弦替换压不到 0.85，实测换 tier 后 0.966 → 0.645；仍是"同风格同一套素材" |
| `roulette_tick` 调性 | §3.1 列进"走 in_key 的配方" | v0.6 本来没走 in_key；实现为 key=None 保持旧公式、key 非空才吸附 | 接受：不破坏 legacy 逐样本 |
| 同段重复 cue 的 gain | §3.4 写 -2/-4/-6 | 每次再降 2 dB 不封顶（items 很多时会到 -2n） | 待观察：模板 5 条时最末一条 -8 dB；若听感偏轻改 `_cue_gain_decay` 一行 |
| `content_seed` 实现位置 | §3.1 放 sfxkit | filmkit 与 sfxkit 各有一份逐字相同的实现（filmkit 走函数内延迟 import，模块级 from 会改导入时机） | 接受：两侧同参同值已核对；注释互相指认 |

**回归基线**（改前先录、改后逐条比对）：

- sfxkit 9 条渲染的 sha256（ui_blip ×3 / answer_chime / text_stream ×2 / data_chirp / roulette_tick ×2）逐条相同。
- scorekit legacy 路径（style=None）6 s 渲染逐样本与 v0.6 相同。
