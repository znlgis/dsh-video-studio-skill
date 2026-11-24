# 提案层（Proposal）与设计 DSL

> 一句话：**出片之前先出方案；方案由用户拍板；拍板的那份设计被冻结成 design.json，渲染只认它。**
> 还有一条：**交付过的设计会进台账**，下一次提案主动避开——这是"不同片不重复"的落地机制。

## 0. 为什么要有这一层

长相不能由剧本决定，也不能由程序替用户拍板：同一份稿子该长什么样，是个需要看图的审美决策，
而"看过了再点一个"正是用户唯一能参与决策的位置。所以 `propose.py` 现场采样 N 个**两两可区分**
的设计，每个配 4 张静帧，你确认哪一个，那一个才被冻结成 `design.json`（渲染只认它）。
**没有"以某套预设为起点"这回事**，每次都是现场采样。

## 1. 三个产物

| 文件 | 谁写 | 语义 |
|---|---|---|
| `work/proposal.json` | `propose.py` | 一版提案：N 个候选（含 design + 静帧 + 成本 + 差异度 + 台账比对结果）+ 确认状态 |
| `work/design.json` | `propose.py --confirm` | **冻结**后的那一份设计（唯一真源，渲染只认它） |
| `work/proposals/revN/proposal.json` | `propose.py` | 每一版的快照（rev 不覆盖，可 diff、可回滚） |

## 1.5 先问诊，再提案（首次提案的硬前置）

`propose.py` 只决定「外观谁来拍板」；素材 / 时长 / 气质 / 声线 / 配乐这些**输入**来自问诊：

```bash
python <SKILL_DIR>/scripts/intake.py --work work --questions --json   # 8 题，一次性问完
python <SKILL_DIR>/scripts/intake.py --work work --recommend "中国高铁十年，纪录片"  # 制作方式推荐
python <SKILL_DIR>/scripts/intake.py --work work --set tone:bright-lively ...
python <SKILL_DIR>/scripts/intake.py --work work --finalize           # -> intake.brief.txt
```

| 项 | 行为 |
|---|---|
| 没答完 | 首次 `propose.py` 退 1，并打印「怎么问 / 怎么答 / 怎么定稿」三步 |
| 答完了 | 提案读 `work/intake.json`，答案合成 brief；`proposal.intake` 记 complete + 答案 + 制作方式（`intake.mode`） |
| `--no-intake` | 显式放行，`proposal.intake.status = bypassed`（事后可查） |
| 气质默认 | 任何题材的推荐项都是「明亮活泼」；没点名气质时**首个候选固定 bright** |
| 制作方式 | 第 2 题（`mode`）= 自己画 / 网上素材剪辑 / 两者结合；`auto` 按题材推荐落地。**素材剪辑线提案层不适用**：`propose.py` 仍能跑（hybrid 的**图文段**要用），但会明确提示，并把 `mode` 记进 `proposal.intake` |
| hybrid 的下一步 | 提案/冻结只决定图文段外观；冻结后必须跑 `matchstyle.py --apply` 把配色与画幅锚到素材（见 `references/footage.md`）。**不要**反过来给素材叠主题色 |

## 2. 流程（零拷贝：脚本留在技能目录里直接跑）

```bash
# 1) 出一版方案（离线，不联网，秒级）
python <SKILL_DIR>/scripts/propose.py --brief "AI 大模型的三年，30 秒科普" --script film.json --work work --n 3

#    产物：work/proposal.json + work/proposals/rev1/{A,B,C}/_sheet.png + _compare.png
#    每个候选带：立意一句话、每个候选 4 张静帧、与其它候选的设计距离（n = 不同的维度数）

# 2) 不满意就提要求重出（新 rev，不覆盖旧的）
python <SKILL_DIR>/scripts/propose.py --revise "太冷了，要暖一点、女声、节奏快一点" --work work

# 3) 对比两版
python <SKILL_DIR>/scripts/propose.py --diff 1 2 --work work

# 4) 确认其中一个 -> 冻结 design.json（同时记进片间去重台账）
python <SKILL_DIR>/scripts/propose.py --confirm --candidate B --work work

# 5) 现在才允许出片。--scenes 不写就用剧本同目录 / 技能内置的渲染器
python <SKILL_DIR>/scripts/render.py --script film.json --work work --out out/film.mp4 --stage all
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

六块：`structure`（几段/什么用途/目标时长）、`visual`（配色/字体/母题/转场/动效/重心/版式/版式轮换/HUD/字幕/画幅）、
`audio`（声音/配乐/音效）、`pacing`（留白与停留）、`id`/`pitch`（标识与立意）。

完整契约见 `<REPO>/schemas/design.schema.json`；校验器是 `designkit.validate_design()`，
命令行入口 `python <SKILL_DIR>/scripts/designkit.py --lint work/design.json`。

### 4.1 fail-closed（这条最重要）

设计里**缺字段、枚举越界、对比度不达标**，一律报错，**绝不静默回落默认值**。
理由：**改了不生效也不报错**是这套流程里最贵的故障——审计实测过 11 个静默失效的旋钮
（`voice` 写字符串时 map/jitter 全失效、`theme_overrides.ease` 被视觉语言遮住、
`sfx.cues[].seed` 必崩、`pan` 对立体声配方无效……）。设计层不允许出现这种状态：
要么字段有效，要么当场报错。

### 4.2 visual.subtitle（可选）

字幕样式由设计 DSL 决定（不是 `filmkit` 的写死常量）。所有字段可选，**不写 = 补齐默认值，与默认常量逐像素一致**：

| 字段 | 默认 | 取值 | 说明 |
|---|---|---|---|
| `size` | 46 | 20–96 | 字号 |
| `y` | null | 0–1030 | 字幕带基准线；null = 默认贴底安全区 |
| `align` | center | center / left | 对齐 |
| `plate_alpha` | 170 | 0–255 | 背板透明度（0 = 无背板） |
| `radius` | 10 | 0–48 | 背板圆角 |
| `family` | null | sans / serif / kai / round | null = 跟随大标题字族 |
| `color` | null | [r,g,b] 或 #rrggbb | null = 默认墨色 |
| `max_lines` | 2 | 1–3 | 最多折几行 |

渲染侧只读 `designkit.subtitle_of(design)` 这一份口径（`filmkit`、参考帧渲染共用），避免"字幕样式在两处漂移"。

### 4.3 visual.layout_cycle（可选）

按**章节**轮换版式：同一章内版式固定，换章才换。元素取 `left-stack` / `center-stack` / `split-right`，
**至少 2 种不同版式**（只写一种会被 `validate_design` 拒绝）。
注意派生发生在**采样器**（`sample_designs` 会按设计指纹派生 2–3 种写进候选）；手写 design 不写这个字段时
`design_to_plan` **不派生**，全片只用 `visual.layout` 那一种。`--stage layout` 会把每段选的版式写进 `work/storyboard.json` 的
`layout` 字段，可逐段审计。

### 4.4 配色不是随便取色

采样器的配色走**对比度求解**：定一个色相锚点，在 (s,v) 网格里找「最接近设计意图且满足
`theme_lint` 全部对比度门槛」的颜色。实测：纯 HSV 随机取色的通过率 **21/60 = 35%**（不可用）；
改成网格求解后 **60/60 = 100%**。配色是**连续色相空间**里的一点，不是"8 个命名配色族"。

## 5. 采样器（sample_designs）

输入 brief（题材 + 可选时长/必须有/不要），输出 N 个**两两可区分**的设计：

- **默认每次都不一样**：不传 `seed` 就生成新的 nonce，同一 brief 跑两次得到不同设计；
  传 `--seed` 才逐字节可复现。这是"每条视频都不一样"的来源。
- 所有选择由 `sha1(nonce + brief + index + seed + attempt)` 派生，**禁 random / time**。
- **两两必须在 ≥2 个设计维度**上不同（10 个离散轴：配色 / 母题 / 转场 / 缓动 / 编号 / **配乐（调式+BPM 档+情绪）** /
  声线 / 情绪 / 版式 / 调式），否则视为「伪多方案」（只换颜色不算）。
- **底色还要拉开色相**：色相差 < 6° 一律重采；< 12° 时要求结构差异 ≥ 3 个轴。
  带配色约束（如 `--brief "深蓝"`）时可选色相窗口只有 52°，采样器把窗口铺成 24 个格点、
  按「与已接受候选的间距」分档挑点（不是哈希碰运气）：2160 次 n=3 的约束采样 0 失败。
  这类窄窗口下两个候选的色相差可能落在 10° 左右——那是**预期**，靠版式/转场/编号/配乐等
  ≥ 3 个结构轴保证「并排看是不同方案」；要底色拉得更开就放宽 brief 或减少候选数。
- **片间去重台账**（见 §6）：候选还要与"最近交付过的设计"至少差 2 维。
- 每个候选自带一句中文立意 `pitch`，写进提案卡。

## 6. 片间去重台账（默认开启）

"每条视频都不一样"不能只靠随机：同题材连拍三部仍可能撞到相近的配色/版式组合。
`propose.py` 把**确认过（= 真要出片）的设计**记进用户级台账，提案时主动避开：

| 项 | 值 |
|---|---|
| 默认路径 | `~/.dsh/video-studio/design-ledger.json`（最多保留 500 条） |
| 环境变量 | `VIDEO_STUDIO_LEDGER` 改路径；设为 `off` / `0` / `false` / `no` 即关闭 |
| CLI | `--no-ledger`（本次不读不写）/ `--show-ledger`（打印路径与最近 20 条） |
| 判据 | 候选与最近交付设计的最小距离 < **2 维** → 换新 nonce 重采（最多 4 轮），并输出告警 |
| 记录时机 | `--confirm` 时才记（草稿候选不进台账） |

读写失败/文件损坏一律当空表处理——**台账永远不会阻塞出片**。
封锁测试或写文档时把 `VIDEO_STUDIO_LEDGER` 指到临时目录，不要动用户真实台账。

## 7. 离线预览（previewkit）

**不联网、不合成旁白**：用 `FAKE_VOICE_DUR=2.0` 的合成时间轴排轴（同 `tools/golden_frames.py` 的口径），
渲 3–4 帧/候选 + 联系表 + N 个候选纵向并排的对比图。

成本（作者机器 2026-11-11 实测）：单候选 3 帧 **0.6–0.9 s**；3 个候选含并排对比图 **约 2.1 s**；
全程离线。成本大头是每个候选的首帧准备（主题/母题派生），同一候选内多渲几张几乎不要钱。
换机会有波动，**别把这里的秒数当承诺**。

> 为什么不用 `--stage stills`：它缺 `tts.json` 时会**自动联网合成旁白**。
> 提案阶段不该付这个网络成本，也不该往用户的 TTS 缓存里写东西。

## 8. 常见误用

| 现象 | 原因 | 修法 |
|---|---|---|
| 改了 design.json 但画面没变 | 没重跑 frames（或没确认） | `python render.py --stage explain` 看是否 `source-changed` |
| `--confirm` 报「设计没过校验」 | 手改过 design.json 或采样器版本变了 | 按报错逐条修，或 `--revise` 重出 |
| 候选都「太像」 | 采样器的差异度约束被放宽了 | 看 `--show` 里的 `diversity.vs[].n`，< 2 就是伪多方案 |
| 提案闸一直 WARN | 没跑过 `propose.py --confirm` | 按提示两步走；要硬拦加 `--require-proposal` |
| 预览图和成片不一致 | 预览用合成时长，成片用实测时长 | 正常现象：静帧只用于看构图/配色，节奏以成片为准 |
| 「这批候选和上一部太像」告警 | 片间去重台账命中（< 2 维） | 自动重采过 4 轮仍撞：`--revise "换个完全不同的方向"`，或 `--no-ledger` 忽略 |
| 带「深蓝」这类要求时提案失败 | 约束把可选空间收窄到不可行 | 判重按色相距离、不按固定角度分桶；仍失败就放宽 brief 或 `--n 2` |
| `film.json` 里写 `style`（`"auto"` / 风格 id / `"legacy"`）画面没变 | 外观只认 `work/design.json`，这个字段不参与选型 | 删掉它；没有 design 就先 `propose.py --brief ...` 再 `--confirm` |

## 附：SKILL.md 正文迁入（原 0.4 铁律：先问诊，再提案）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

## 0.4 【铁律】先问诊，再提案（每个新成片任务的第一步）

> **先把只有用户能决定的事一次问完，再去出方案。** 这条是为了消掉"有时一个问题不问就一路跑完、
> 有时只问一两个"的随机性：`propose.py` 只负责"外观由谁来拍板"，而题材/时长/气质/声线/配乐
> 这些**输入**不在它里面；输入由 agent 当场猜，用户就得到成片才第一次看到自己的片子长什么样，
> 返工全在渲染之后。

**固定动作（排在 §0.5 提案之前）**：

```bash
# 1) 生成问诊单：固定 8 题（题材 / 制作方式 / 时长 / 主题气质 / 画幅 / 旁白声线 / 配乐 / 补充）
#    带 --topic 时，「制作方式」题会附 recommended / recommend_reason（按题材算好）
python <SKILL_DIR>/scripts/intake.py --work work --questions --json --topic "AI 大模型的三年"
#    想先单独看推荐也可以（不给文本就用问诊单里已写的题材）：
python <SKILL_DIR>/scripts/intake.py --recommend "中国高铁十年，纪录片"

# 2) 把 questions 里的每一题用 ask_user_question **一次问完**（不要一句一轮），再写回：
python <SKILL_DIR>/scripts/intake.py --work work \
       --set "topic:AI 大模型的三年，讲给普通观众" --set mode:auto --set duration:45s \
       --set tone:bright-lively --set voice:female-bright --set music:upbeat \
       --set "note:不要放射线，结尾放二维码"

# 3) 定稿：校验必答题、合成 --brief 文本（work/intake.brief.txt），并按制作方式打印下一步
python <SKILL_DIR>/scripts/intake.py --work work --finalize
```

| 规则 | 说明 |
|---|---|
| **一次问完** | 8 题在同一次 ask_user_question 里给出，每题都带推荐项；用户只需勾选/一句话补充 |
| **制作方式先定产线** | 第 2 题问「自己画 / 网上素材剪辑 / 两者结合」：自己画走 `render.py`、素材剪辑走 `footage.py`、两者结合默认形态 3（`footage.py` + `compose.py`），按段拼接须显式 `--hybrid-form 1`。选错产线整片返工，所以它是必答题；`auto` 档按题材推荐落地（真实影像→footage、数据/原理→draw、两边都占→hybrid），显式选择优先 |
| **推荐要有理由** | `--recommend` / 问诊单的 `recommend_reason` 会列出命中的题材词；把推荐说给用户听，但用户不同意就按用户的来——不看理由直接默认等于替用户拍板 |
| **明亮活泼是默认** | 任何题材（科普、纪录片、宣传片、动画短片、复盘汇报）的气质题推荐项都是「明亮活泼」，且它排在第一位 |
| **首个候选必亮** | 没点名气质时，提案的候选 A 固定落到明亮活泼档；其余候选保留完整空间（含暗色）——"优先明亮"不等于"只有一种口味" |
| **没答完不许出方案** | 首次 `propose.py` 会因问诊未完成而失败，并打印"怎么问 / 怎么答 / 怎么定稿"三步 |
| **逃生口留痕** | 确实不需要问（批量补渲、已在别处答过）用 `propose.py --no-intake`，提案里会记 `intake.status = bypassed`，事后查得到 |
| **回答进 brief** | 答案合成 `intake.brief.txt`，再喂给 `--brief`：气质→tone、声线→voice、时长→duration_target，`不要 X` 进 avoid。**制作方式不进 brief**：它是产线路由，写进去只会污染 `propose._brief_of` 的关键词解析 |

有用户直接说"你替我定"时：**仍然先问诊**（用推荐项作为各题默认，只让用户确认一次），
再出提案；不要因为"用户没意见"就把 8 个决策全部改成程序默认值。

## 附：SKILL.md 正文迁入（原 0.5 铁律：先提案，再出片）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

## 0.5 【铁律】先提案，再出片（不论用户是否要求）

> **用户没要求，也必须先给方案。** 这是本技能最重要的一条产品行为，不是建议。
> 理由：外观一旦由程序替用户选，用户就从头到尾没参与决策，结果必然是"每个视频都差不多"。
> 所以流程固定为：**现场采样出几个方案 → 用户挑一个 → 冻结成设计 → 再出片**。

**固定动作（问诊答完后的第一件事）**：

```bash
python propose.py --brief "<题材/受众/时长/语气>" --script film.json --n 3
```

它会产出 **N 个候选方案**（每个 = 一份设计 + 4 张静帧 + 成本 + 与其它候选的设计距离），
落盘 `work/proposal.json`，并打印一句"确认其中一个 / 都不满意就 `--revise`"。**全程离线、秒级。**

| 用户反应 | 你要做的 |
|---|---|
| "就这个" / 选了某个 | `python propose.py --confirm --candidate B`，然后才允许渲染 |
| "太冷了/太花哨/换个声音/节奏快点" | `python propose.py --revise "<原话>"` —— 新 rev，旧 rev 保留可 diff |
| "都不行，重来" | 同上，把要求写具体（`--revise` 的文本会存进 `proposal.feedback`） |
| 明确说"你定就行" | **仍然先出方案**，挑一个你推荐的并说明理由，让用户点头 |

**硬约束**：

1. `propose.py --confirm` 之前，`render.py` 的 `frames`/`all` 会被提案闸拦下（默认警告，`--require-proposal` 失败）。
2. 候选必须**两两在 ≥2 个设计维度上不同**（配色族/母题/转场/缓动/编号/配乐/声线）。只换颜色不算多方案，`diversity.vs[].n < 2` 会被标出来。
3. 每个候选必须有一句中文**立意**（`pitch`），说清"这个方案想干成什么样"——不是"方案 A/方案 B"。
4. **没有内置模板可套**：`design.json` 的所有字段都开放（35 字段主题 + 五组视觉语言签名 +
   声音/配乐/节奏），配色是**连续色相空间**里的任意一点，不是从若干套现成外观里挑。
   每次提案都是现场生成——同一个 brief 连拍两次会得到不同设计（要复现就传 `--seed`）。
5. **首个候选默认明亮活泼**：没点名气质时候选 A 走浅底带色（"明色"标签），
   其余候选在完整空间里取（含暗色）。任何题材的气质题推荐项都是「明亮活泼」。
6. **片间去重台账（默认开启）**：`--confirm` 会把冻结的设计记进
   `~/.dsh/video-studio/design-ledger.json`（最多 500 条），下次提案主动避开最近交付过的外观——
   候选与台账里任一部片的**设计距离 < 2 维**时自动换 nonce 重采并告警。
   这是"不同片不重复"从"靠随机"变成"有记忆"的落地机制。`--show-ledger` 查台账、
   `--no-ledger` 本次忽略、环境变量 `VIDEO_STUDIO_LEDGER` 改路径或设 `off` 关闭。
   台账读写失败一律当空表，**永不阻塞出片**。

**只有一种情况可以跳过提案**：纯修复 / 纯重渲（不改内容、不改设计，只修音画不同步或重出片）。
这种情况下用 `--require-proposal` 之外的方式跑，并在回复里说明"本次未提案，因为…"。

## 附：SKILL.md 正文迁入（原 5.5.0b 手改 design.json 之后：--adopt）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

#### 5.5.0b 手改 `design.json` 之后：`--adopt`，别重跑提案

`work/design.json` 的每个字段都是开放的（配色、节奏、字幕、母题都能改），但 `proposal.json`
存的是**确认那一刻**的设计指纹。改完直接渲染，提案闸会判 `stale` 并在 QC 报告里留 WARN；
而重跑 `propose --confirm` 会把你刚改的东西覆盖掉。正确做法是重新绑指纹：

```bash
python <SKILL_DIR>/scripts/propose.py --adopt --work work --script film.json
#   == adopt：把 work/design.json 重新绑成 confirmed
#      新指纹: 37f33435d890c2c3
#      旧指纹: 92479a7fccfb917e（手改过 design.json（符合预期））
```

- 只改 `designHash`（+ 剧本指纹），**不动** candidates / chosen；
- 设计过不了 `validate_design`（比如改出一个对比度不达标的强调色）时**拒绝**，不会把坏设计绑成已确认；
- 旧指纹与提案里的候选指纹也对不上时，会把两种可能都说出来（手改 / 选错文件）。

## 附：SKILL.md 正文迁入（原 5.5.2b 时长要求：不少于 N 分钟是下限）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

#### 5.5.2b 时长要求：「不少于 N 分钟」是**下限**，不是精确目标

问诊的时长题给的是「不少于 1 / 3 / 5 / 7 / 9 分钟」+ 自定义原话（也认「2 分钟」「90 秒」这类精确说法）。
解析结果分两档，落在 `design.structure.target_sec` 与 `target_mode`：

| `target_mode` | 触发写法 | 渲染对账 |
|---|---|---|
| `at_least` | 「不少于 5 分钟」「至少 3 分钟」「12 分钟以上」 | **只有不足**才告警（超出去是好事，不报） |
| `exact` | 「2 分钟」「90 秒」「一分半」 | 双向告警（差 >15%） |

两档都只影响两件事：候选的段落数（`designkit` 按 `target_sec` 决定 `structure.segments`）与渲染时的对账口径。

**都不会截断旁白**：成片长度永远由 TTS 实测时长决定。写了「不少于 5 分钟」而台词只够 4 分钟，
渲染器只会告诉你「没有达到下限（差 60.0s）」，要补的是台词量（或 `hold`/`lead`/`tail`），
不是指望它凭空拉长。

## 附：SKILL.md 正文迁入（原 5.5.3 设计多样性）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

#### 5.5.3 设计多样性：候选之间、以及片与片之间

`propose.py` 的候选不是"换个颜色"就叫多方案。两个设计在 **10 个离散轴**上比较
（`designkit.design_distance`）：配色 / 母题 / 转场 / 缓动 / 编号 / **配乐（调式 + BPM 档 + 情绪复合键）** /
声线 / 情绪 / 版式 / 调式。规则：

1. **候选之间**至少 **2 个轴**不同，否则重新采样（`propose` 会把"距离"打在候选表里，一眼能看出谁和谁像）。
2. **底色必须拉开色相**：两个候选的底色色相差 < 6° 一律重采（并排看就是同一片底色）；
   色相差 < 12° 时，只有**结构差异 ≥ 3 个轴**才接受。
   判重比的是**色相距离**而不是"配色族分桶"：带 `--brief "深蓝"` 这类约束时可选色相窗口只有 52°，
   分桶会把可行区间切碎成不到 3 个桶、n=3 直接采不出来。实测带约束的 brief 连跑 10 次 **10/10 成功**。
3. **配乐轴**按 **调式 / BPM 档（±6 归一）/ 情绪** 的复合键判定——"两个候选配乐不一样"是真的在比。

## 附：SKILL.md 正文迁入（原 5.5.4 片间去重台账）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

#### 5.5.4 片间去重台账（默认开启）

"每条视频都不一样"不能只靠随机：同一个题材连拍三部，仍可能撞到相近的配色/版式组合。
`propose.py` 现在把**确认过（= 真要出片）的设计**记进用户级台账，提案时主动避开：

| 项 | 值 |
|---|---|
| 默认路径 | `~/.dsh/video-studio/design-ledger.json`（最多保留 500 条） |
| 环境变量 | `VIDEO_STUDIO_LEDGER` 可改路径；设为 `off` / `0` / `false` / `no` 即关闭 |
| CLI | `--no-ledger`（本次不读不写）/ `--show-ledger`（打印路径与最近 20 条） |
| 判据 | 这批候选与最近交付设计的最小距离 < **2 维** → 换新 nonce 重采（最多 4 轮），并在输出里告警 |
| 记录时机 | **`--confirm` 时**才记（草稿候选不进台账） |

读不到 / 写坏台账都当空表处理——**台账永远不会因为文件损坏而阻塞出片**。
写文档或做回归时把 `VIDEO_STUDIO_LEDGER` 指到临时目录，别动用户真实台账。

## 附：SKILL.md 正文迁入（原 5.5.6 没有设计就出不了片（fail-closed））

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

#### 5.5.6 没有设计就出不了片（fail-closed）

`film.json` 里写 `"style"`（`auto` / `legacy` / 任何 id）都不影响画面；`render.py --preset` 也不存在。
没有 `work/design.json` 时 `render.py` 与 `filmkit.resolve_theme()` / `style_plan()` 都**直接报错**
并给出两步指引，而不是静默回落一套默认外观——静默回落等于用一套不存在的模板出片。

外观细节（配色怎么解、主题 35 个字段、如何手写一份设计）见
[references/appearance.md](references/appearance.md)。提案流程与契约见 [references/proposal.md](references/proposal.md)。
