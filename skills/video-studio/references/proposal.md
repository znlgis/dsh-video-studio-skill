# 提案层（Proposal）与设计 DSL —— v1.0

> 一句话：**出片之前先出方案；方案由用户拍板；拍板的那份设计被冻结成 design.json，渲染只认它。**

## 0. 为什么要有这一层

v0.9 及以前，`style: "auto"` 会让 `pick_style` 按关键词 + 指纹**自动挑一套**内置风格。
这带来两个后果（都有实测）：

| 后果 | 实测 |
|---|---|
| 同一份内容的身份是确定的 —— 改不出第二种样子 | 8 个不同题材只命中 5/8 套风格，其中 3 个都落进 tech-deep |
| 不写 `style` 就直接落到 legacy —— 全片只有一套皮肤 | 8/8 题材全落 legacy：120BPM / Dm / 同一套配色 |

v1.0 把这条路径**整条删掉**：剧本不再决定长相，长相由 `design.json` 决定；
`design.json` 只能由「用户确认过的提案」产生。

## 1. 三个产物

| 文件 | 谁写 | 语义 |
|---|---|---|
| `work/proposal.json` | `propose.py` | 一版提案：N 个候选（含 design + 静帧 + 成本 + 差异度）+ 确认状态 |
| `work/design.json` | `propose.py --confirm` | **冻结**后的那一份设计（唯一真源，渲染只认它） |
| `work/proposals/revN/proposal.json` | `propose.py` | 每一版的快照（rev 不覆盖，可 diff、可回滚） |

## 2. 四步流程

```bash
# 1) 出一版方案（离线，不联网，秒级）
python propose.py --brief "AI 大模型的三年，30 秒科普" --script film.json --n 3

#    产物：work/proposal.json + work/proposals/rev1/{A,B,C}/_sheet.png + _compare.png
#    每个候选带：立意一句话、4 张静帧、与其它候选的设计距离（n = 不同的维度数）

# 2) 不满意就提要求重出（新 rev，不覆盖旧的）
python propose.py --revise "太冷了，要暖一点、女声、节奏快一点"

# 3) 对比两版
python propose.py --diff 1 2

# 4) 确认其中一个 -> 冻结 design.json
python propose.py --confirm --candidate B

# 5) 现在才允许出片
python render.py --script film.json --scenes scenes.py --out out/film.mp4 --work work
```

## 3. 提案闸（frames 之前）

`render.py` 在 `frames` / `all` 之前会调 `_proposal_gate()`，判定五档：

| 状态 | 含义 | 默认行为 |
|---|---|---|
| `confirmed` | 已确认，且 `design.json` 指纹与确认时一致 | 放行（记 pass） |
| `absent` | 没有任何提案 | 警告（`--require-proposal` 下失败） |
| `draft` | 有提案但没确认 | 同上 |
| `stale` | 确认过但 `design.json` 被改过 | 同上 |
| `broken` | `proposal.json` 坏了 | 同上 |

为什么默认只警告：`--stage frames` 是开发期高频命令（改一帧看一帧）。要硬拦就加 `--require-proposal`。

`design.json` 同时是 stagewatch 的**第三个源头输入**（另外两个是 `film.json` 与 `scenes.py`）：
改了设计但没重跑 `frames`，`--stage explain` 会直接报出来。

## 4. design.json 的形状

六块：`structure`（几段/什么用途/目标时长）、`visual`（配色/字体/母题/转场/动效/重心/版式/HUD/画幅）、
`audio`（声音/配乐/音效）、`pacing`（留白与停留）、`id`/`pitch`（标识与立意）。

完整契约见 `schemas/design.schema.json`；校验器是 `designkit.validate_design()`。

### 4.1 fail-closed（这条最重要）

设计里**缺字段、枚举越界、对比度不达标**，一律报错，**绝不静默回落默认值**。
理由：v0.5–v0.9 最大的病就是「改了不生效也不报错」——本轮审计实测出 11 个静默失效的旋钮
（`voice` 写字符串时 map/jitter 全失效、`theme_overrides.ease` 被视觉语言遮住、
`sfx.cues[].seed` 必崩、`pan` 对立体声配方无效……）。设计层不允许重演这一幕。

### 4.2 配色不是随便取色

采样器的配色走**对比度求解**：定一个色相族，在 (s,v) 网格里找「最接近设计意图且满足
`theme_lint` 全部对比度门槛」的颜色。

实测：纯 HSV 随机取色的通过率 **21/60 = 35%**（不可用）；改成网格求解后 **60/60 = 100%**。

## 5. 采样器（sample_designs）

输入 brief（题材 + 可选时长/必须有/不要），输出 N 个**两两可区分**的设计：

- 所有选择由 `sha1(brief + index)` 派生，**禁 random**：同一 brief 永远同一组候选
- 两两必须在 **≥2 个设计维度**上不同（配色族/母题/转场/缓动/编号/配乐/声线），
  否则视为「伪多方案」（只换颜色不算）
- 每个候选自带一句中文立意 `pitch`，写进提案卡

## 6. 离线预览（previewkit）

**不联网、不合成旁白**：用 `FAKE_VOICE_DUR=2.0` 的合成时间轴排轴（同 `tools/golden_frames.py` 的口径），
渲 3–4 帧/候选 + 联系表 + N 个候选纵向并排的对比图。

成本（实测）：单候选 **2.5–4.4 s**（1080p，含联系表）；4 个候选约 **14.5 s**。
成本大头是每套风格的**首帧烘焙**（背景/母题缓存），同风格内多渲几张几乎不要钱。

> 为什么不用 `--stage stills`：它缺 `tts.json` 时会**自动联网合成旁白**（render.py:2529-2537）。
> 提案阶段不该付这个网络成本，也不该往用户的 TTS 缓存里写东西。

## 6.1 只有一条起点：现场采样（v1.0.1）

v1.0.0 曾经有 `--preset <id>`（以出厂预设为起点），v1.0.1 把预设与风格表**全部删除**——
有预设就意味着用户的片子只能在预设里挑，第 9 种不存在。现在只有一条路：

```bash
python propose.py --brief "题材/要求" --script film.json --n 3
```

不满意就用 `--revise "更冷一点 / 少一点装饰"` 重出。想复现同一份设计就用 `--seed` 固定，
而不是去找一套预设。

## 7. 常见误用

| 现象 | 原因 | 修法 |
|---|---|---|
| 改了 design.json 但画面没变 | 没重跑 frames（或没确认） | `python render.py --stage explain` 看是否 `source-changed` |
| `--confirm` 报「设计没过校验」 | 手改过 design.json 或采样器版本变了 | 按报错逐条修，或 `--revise` 重出 |
| 候选都「太像」 | 采样器的差异度约束被放宽了 | 看 `--show` 里的 `diversity.vs[].n`，< 2 就是伪多方案 |
| 提案闸一直 WARN | 没跑过 `propose.py --confirm` | 按提示两步走；要硬拦加 `--require-proposal` |
| 预览图和成片不一致 | 预览用合成时长，成片用实测时长 | 正常现象：静帧只用于看构图/配色，节奏以成片为准 |

## 8. P3 之后会删掉什么（迁移须知）

| 现状 | P3 之后 |
|---|---|
| `style: "auto"` 自动选风格 | 删除。没有 design 就不出片 |
| `stylekit.THEMES/STYLES/VISUAL_LANGUAGES` 8 套 | **已全部删除**（v1.0.1） |
| legacy（不写 style 走 v0.4 绘制器） | 删除。老片复现只能靠当年留存的 work/ 快照 |
| `scorekit.MUSIC_STYLES` 9 套 | **已删除**（v1.0.1）：配乐改由 `musicgen.py` 参数化编排，不再需要 `audio.music.arranger` 这一层 |
| `voicekit.VOICE_PERSONAS` 9 个 | 换成裸 edge voice id + 自动响度校准（不再固定 9 个） |