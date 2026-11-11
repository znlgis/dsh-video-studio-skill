# 风格包（Style Packs）——**v1.0.1 已删除，本文仅存档**

> **现在没有「风格包」了。** v0.5 的 `stylekit.THEMES/STYLES/VISUAL_LANGUAGES` 八套命名风格、
> `presets/*.json` 预设定档、scorekit 的 9 套 `MUSIC_STYLES` 配乐风格表**全部删除**。
> 本技能不提供模板：外观由 `propose.py` 按 brief 现场采样出 `design.json`，配乐由
> `musicgen.py` 按参数现场编排。下面保留的是 v0.5–v0.9 的实现记录，只用于理解历史；
> 照着做会报错（`style` 字段现在会被明确拒绝）。

## 0. 为什么需要它（v0.4 的病）

v0.4 及以前，所有"身份参数"都是模块级常量：

| 层 | 被写死的地方 | 结果 |
|---|---|---|
| 画风 | assets/scenes.py 的 THEME 字典 + 3 个绘制函数 | 每部片都是深蓝底 + 橙强调 |
| 配乐 | 当时 scorekit 的 4 个小调 + 3 套和弦进行 + 固定动机（**该风格层 v1.0.1 已删除**） | 每部片都是 Dm / 120BPM 那一套 |
| 旁白 | render.py 的单个 voice/rate/pitch | 每部片都是同一个声音、同一语速 |
| 流程 | SKILL.md 教的是 "cp 模板 -> 改 lines[].text" | 模型自然只会改文案 |

结果就是：**内容换了，气质没换**。用户的原话是"背景音乐风格都差不多、旁白声音都差不多、整体画风也差不多"。

## 1. 三层结构

    film.json   style: "auto" | "tech-deep" | ... | "legacy"
        |
        v
    stylekit.resolve_script_style(script)        <- 纯函数，确定性
        |  +- 关键词打分（title/chapter/text/items）
        |  +- 三级内容指纹（full / tempo / style）
        |  +- 显式覆盖（visual.theme / music.style / voice.map / pacing）
        v
    work/style.json                               <- 审计产物：能看到"为什么是这套风格"
        |  +- style / theme_id / chapter_layout
        |  +- music{bpm,key,mood,seed_effective}
        |  +- voice{map,policy}
        |  +- pacing{lead,tail,min_dur,item_stagger}
        v
    画面（scenes.py 读主题）    配乐（当时由 scorekit 读风格；v1.0.1 起改为 musicgen 参数）    旁白（voicekit 读 persona/tone）

## 2. 8 套内置风格

| id | 中文 | 画风 | 配乐 | 旁白 | 适合内容 |
|---|---|---|---|---|---|
| tech-deep | 科技深蓝 | 深蓝渐变 + 橙 | 小调 100–124 / 琶音 + 铃声 | 男声叙述 + 青年开场 | AI、工程、数据、科普 |
| magazine | 杂志暖白 | 米白 + 朱红，衬线体 | 大调 88–100 / 电钢 | 女声叙述 + 权威男声引用 | 报告、复盘、研究 |
| paper | 手绘纸感 | 牛皮纸 + 靛青，楷体 | 大调 84–92 / 马林巴 | 软糯女声 + 男声引用 | 手工、自然、历史 |
| vivid | 活力撞色 | 深紫 + 柠檬黄，大字 | 大调 120–132 / 短促和弦 | 活泼女声开场 | 活动、营销、上新 |
| mono | 极简黑白 | 纯白 + 黑，超宽字距 | 极简 60–68 / 钢片琴 | 沉稳男声 + 女声引用 | 观点、演讲、思辨 |
| cinema | 电影青橙 | 近黑 + 青橙，扫描线 | 小调 76–84 / 弦乐铺底 | 纪录片男声 + 女声引用 | 纪录、叙事、城市 |
| lo-fi | 暖灰薄荷 | 暖灰 + 薄荷绿 | 大调 72–84 / 电钢 + 轻鼓 | 温柔女声 | 日常、学习、专注 |
| epic | 深黑鎏金 | 近黑 + 金，衬线体 | 小调 88–96 / 铜管 + 人声铺 | 有力男声 + 男声开场 | 史诗、宣传、高潮 |

外加 legacy：不写 style 字段时的兼容档，画风/配乐/旁白全部等于 v0.4（配乐逐样本一致）。

查表：

    python stylekit.py --list          # 8 套风格 + 主题 + 对比度
    python stylekit.py --show cinema   # 单套风格的全部字段
    python stylekit.py --lint          # 主题对比度门禁（CI 会跑）

## 3. film.json 怎么配

最小配置（推荐，几乎不用写）：

    {"style": "auto"}

自动模式：命中题材关键词就锁定对应风格；零命中时按内容指纹稳定落桶
（同一份稿子永远同一个风格，不同稿子大概率不同）。

想锁定：

    {"style": "cinema"}

想只改某一块（其余跟着风格走）：

    {
      "style": "magazine",
      "visual": {
        "theme": "paper",
        "layout": "split-right",
        "theme_overrides": {"vignette": 0.18, "grain": 8.0}
      },
      "music": {
        "style": "lo-fi",
        "bpm": "auto", "key": "auto", "mood": "calm",
        "seed": 7, "seed_from_content": true
      },
      "voice": {
        "map": {"hook": "bright_female", "body": "narrator_female"},
        "jitter": 0.35,
        "max_distinct": 3
      },
      "pacing": {"lead": 0.6, "tail": 0.9, "item_stagger": 0.3}
    }

逐句覆盖（最高优先级）：

    {"id": "l03", "kind": "body", "text": "他说：「这不可能」",
     "voice": "authority_male", "tone": "quote", "lead": 0.8}

### 字段速查

| 字段 | 默认 | 说明 |
|---|---|---|
| style | 无（= legacy） | 风格 id / "auto" / "legacy" |
| visual.theme | — | 等价于锁 style；与 style 1:1 |
| visual.layout | 风格默认 | left-stack / center-stack / split-right |
| visual.theme_overrides | — | 深合并覆盖主题单字段 |
| music.style | 跟随 style | 只换配乐风格 |
| music.bpm | "auto" | 数字或 "auto"（按风格梯度 + 指纹） |
| music.key | "auto" | 调名或 "auto"（按风格调池 + 指纹） |
| music.mood | 风格默认 | calm/reflective/hopeful/steady/driving/tense/uplifting |
| music.seed_from_content | 非 legacy 时 true | 音色细节由内容指纹派生 |
| voice.map | 风格默认 | kind -> persona 名 |
| voice.jitter | 0.35 | 语速/音高微扰，0 = 关 |
| voice.max_distinct | 3 | 全片最多几把声音 |
| pacing.* | 风格默认 | 语前留白/语后留白/最短段长/条目落位步长 |
| lines[].persona / lines[].voice / lines[].tone | — | 逐句覆盖（最高优先级，voicekit 真的读这三个） |
| lines[].lead | — | 逐句语前留白（filmkit.build_timeline 读） |
| lines[].rate / pitch / volume / tail | — | **当前不生效**（保留字段）：voicekit 只认 persona/voice/tone，filmcheck 会对这三个报"未知键"；要改语速/音高请换 persona 或 tone，或在顶层写 rate/pitch/volume |

## 4. 三级指纹：改错别字不会换整首曲子

| 指纹 | 由什么算 | 决定 |
|---|---|---|
| full | 标题 + 全部台词 | 音色细节（seed 派生） |
| tempo | 标题 + kind 序列 + 体量桶 | BPM 与调性 |
| style | 标题（配合关键词评分破平） | 风格 id |

所以：改一个错别字 -> full 变（音色纹理微变），tempo/style 不变（还是同一首曲子、同一个调）；
换一部片 -> 三个都变。`music.seed_from_content: false` 只锁住 **seed 派生**（音色细节）；
v0.7 时代 `full` 指纹会传给 scorekit 参与结构变奏（和弦替换/动机偏移/鼓型 ghost，见
[audio-variation.md](audio-variation.md) §3.3）。**v1.0.1 起这条链路已删除**：没有 `style` 字段，
也就没有「legacy」可回去——外观与配乐都必须来自 `design.json`。

## 5. 片内变化：整体是一套，但每段有呼吸

**锁三件事**（全片一致，否则会"花"）：

1. 明暗族（全深色 or 全浅色）
2. accent 色相
3. 字体族与字号阶梯

**其余可以按章节变**：

- 版式骨架轮换（同一 chapter 内不变）：left-stack / center-stack / split-right
- 背景明度微移、纹理强度、accent 出现的面积
- 配乐：逐小节能量弧线、配器进出（1 小节渐入渐出）、主奏轮换、动机变形
- 旁白：hook/body/quote/outro 的语气档（语速、音高、停顿）

**禁止**：同一部片里跨明暗族换主题（深蓝段直接切暖白段）。实测这会精确触发
qa_frames 的单帧闪烁门禁（阈值 6.0 灰度，整屏换底可达 100+ 级），而且观众看着像"换了一部片"。
真需要强对比，把它做成两个片子，或登记成带 visual_at 的视觉事件。

## 6. 门禁：主题必须过对比度体检

stylekit.theme_lint(theme) 在 --stage style 就拦，不用等渲完 300 帧：

| 角色 | 对背景的最低对比度 | 理由 |
|---|---|---|
| ink（正文/标题） | 7.0 | WCAG AAA 正文线 |
| muted（副标） | 4.5 | WCAG AA |
| dim（HUD 小字） | 3.0 | 装饰级 |
| accent / accent2 | 4.5 | 强调色常当文字用 |
| good / bad | 3.0（对卡片底） | 只承载图形，靠位置+颜色双编码 |
| bg 与 bg2 亮度差 | >= 0.002 | 防纯色底 |

加第 9 套风格时：先写 _theme(...)，再跑 python stylekit.py --lint，过了再进 STYLES。

## 7. 想要一套新外观怎么办（v1.0.1 的做法）

再也不能「加一套风格」了——没有风格表可加。正确做法是**让采样器按你的要求现场生成**：

1. `python propose.py --brief "题材/要求" --script film.json` 出 3 个候选；
2. 不满意就 `--revise "更冷一点 / 更亮一点"` 重出，旧候选可 `--diff` 对比；
3. `--confirm --candidate A` 冻结成 `work/design.json`，此后出片都用它；
4. 想固定到回归里：把 `design.json` 存档，`--seed` 可复现同一份设计。

第 9 种外观不是「新增模板」，而是把 brief 或色相参数改一下自然得到的东西。

## 8. 常见误用

| 现象 | 原因 | 修法 |
|---|---|---|
| 画面换了、配乐还是老的 | 只改了 visual.theme 没改 music 参数 | 改 design.json 的 audio.music（bpm/mood/sections），或重新 `propose.py --revise` |
| 同一部片前后像两部片 | 候选设计里明暗族/色相跨度过大 | 换一个更收敛的候选，或 `--revise "统一暗底"` |
| 改台词后配乐变了 | `seed_from_content` 把指纹掺进了 seed | 写死 `music.seed` 或设 `seed_from_content: false` |
| 旁白听感像广播剧 | max_distinct 太大 / 相邻句频繁换人 | 降到 2，靠 tone 而不是换人做变化 |
| 字幕在浅色主题上看不清 | 用了 v0.4 的黑底白字 | 传 theme= 给 draw_subtitle（render.py 已自动传） |

## 9. 视觉语言包（v0.9）：结构签名，不只是配色

### 9.1 为什么还要这一层

v0.5 的 `STYLES` / `THEMES` 管的是**皮肤**：配色、字体族、纹理、颗粒、字距、缓动、HUD、
3 种版式几何。8 套风格共用同一批绘制器与同一套装饰，于是"换风格"在画面上只剩换色——
灰度量出来的构图骨架（前景占比 / 重心 / 边缘占用 / 左右对称）几乎不动。

`stylekit.VISUAL_LANGUAGES` 补的是**结构签名**：同一段内容在两套风格下，剪影必须能区分。
它是纯数据（不 import PIL / filmkit，否则与 `scenes.py` 成环），与 `STYLES` 同层，
`--stage style` 一起体检。

### 9.2 五组签名字段

| 组 | 字段 | 取值 / 例子 | 谁读它 |
|---|---|---|---|
| `motion` | `ease` | `out3` / `out5` / `inOut3` / `outBack` | `scenes.py`（所有绘制器的入场缓动） |
| | `breathe_hz` | 0.28–1.10（Hz） | `scenes.py`（强调线/下划线/光点呼吸频率） |
| | `enter` | `(起, 止)` 秒，如 `(0.70, 1.20)` | **已声明、visual_lint 会查、绘制器暂未读** |
| | `push` | 0–0.16（背景缓推幅度） | **已声明、visual_lint 会查、绘制器暂未读**（主题自带的 `push` 仍生效） |
| `motif` | `kind` | `brackets` / `hairlines` / `handdrawn` / `slabs` / `minimal` / `letterbox` / `signal` / `rays` | `scenes._motif_layer` 烘焙母题、`scenes._plate` 合成进背景底版；v0.9.1 起逐帧只 copy/EXTENT（约 1.3–2.0 ms），不再逐帧 paste（实测 5.6 ms） |
| | `density` | 0–1（母题浓度；0 只允许 `minimal`） | `scenes._decor_scale`（再乘片内密度档 0.55/0.75/1.0） |
| | `detail` | 母题专属参数（`corner`/`rule_step`/`wobble`/`top`/`bars`/`rays` …） | 同上 |
| `transition` | `kind` | `dip` / `wash` / `paper` / `wipe` / `cut` / `iris` | `scenes._apply_veil`（段首 ≤0.45 s 内生效） |
| | `dur` | 0–0.60 秒 | 同上；超过 0.60 s 会糊掉 3–6 s 节奏下限 |
| `typography` | `tracking` | 0–8 px（字距；与主题自带字距相加） | `scenes._tracking` |
| | `line_gap` | 1.15–1.55（行高倍数） | `scenes._gap` |
| | `number` | `dim` / `serif` / `cn` / `block` / `roman` / `timecode` / `dot` / `gilt` | `scenes._number_text`（章节编号、列表序号、流程节点号） |
| | `align` / `ratio` | `left`/`center`/`right`；display:body 字号比 1.0–3.6 | **已声明、visual_lint 会查、绘制器暂未读** |
| `bias` | `x` / `y` / `inset` | px，如 `{"x": -150, "y": 80, "inset": 40}` | `scenes._bias`（整体重心偏移；`inset` 只作用于左对齐起点） |

两条设计约束（改的时候别破）：

1. **每条签名都要"能被剪影看出差别"**：只改颜色/字重/描边的项不算签名。
2. **受单帧预算约束**：装饰必须在烘焙阶段合成进底版（`_plate`），逐帧只剩 copy/EXTENT；
   禁每帧全屏 GaussianBlur（实测 30–60 ms）、全屏双线性 resize（29 ms）与全屏 RGBA paste
   （实测 5.6 ms——0.9.0 就是栽在这，0.9.1 才改掉），预算 25 ms。

### 9.3 8 套风格的签名摘要

| 风格 | motion（ease / 呼吸） | motif（母题 / 密度） | transition | typography（字距 / 行高 / 编号） | bias(x,y,inset) |
|---|---|---|---|---|---|
| tech-deep | out3 / 0.85 | brackets / 1.00（四角 L 括角 + 刻度尺 + 扫描线） | dip 0.28 s | 0 / 1.30 / dim | (0, 0, 0) |
| magazine | out5 / 0.50 | hairlines / 0.85（双横线页眉 + 栏间线 + 页码位） | wash 0.35 s | 2 / 1.34 / serif | (+60, −20, 0) |
| paper | inOut3 / 0.42 | handdrawn / 1.00（手绘内框 + 胶带 + 印章） | paper 0.35 s | 1 / 1.36 / cn（一、二、三） | (−150, +80, 40) |
| vivid | outBack / 1.10 | slabs / 1.00（粗色块 + 斜切 + 贴纸） | wipe 0.20 s | 1 / 1.26 / block | (0, +40, 0) |
| mono | inOut3 / 0.35 | minimal / 0.55（极细线 + 直角框） | cut 0.00 s | 6 / 1.40 / roman | (0, 0, 0) |
| cinema | inOut3 / 0.30 | letterbox / 1.00（上下遮幅 + 光斑） | dip 0.45 s | 3 / 1.38 / timecode | (0, +30, 0) |
| lo-fi | out3 / 0.60 | signal / 0.90（信号条 + 时间戳） | wash 0.30 s | 1 / 1.36 / dot | (+60, −70, 0) |
| epic | out5 / 0.28 | rays / 1.00（放射射线 + 金线框） | iris 0.40 s | 4 / 1.32 / gilt | (0, −30, 0) |
| legacy | out3 / 0.85 | brackets / 1.00（detail 为空） | cut 0.00 s | 0 / 1.30 / dim | 不参与（走老绘制器） |

`layout_cycle` 字段也写在每套里（版式轮换顺序），但**当前没有消费者**——
按章节轮换版式的逻辑在 `STYLES[style]["layouts"]` 那一层（见 §5 与 SKILL §5.5）。

### 9.4 `visual_lint` 查什么

在 `--stage style` 就拦（`render.py` 解析风格时调用，坏配置直接 `SystemExit`），
不用等渲到第 300 帧才发现"每段转场糊 1 秒"：

| 检查 | 线 |
|---|---|
| `motif.kind` | 必须在 `VISUAL_MOTIFS` 的 8 个里 |
| `transition.kind` | 必须在 `VISUAL_TRANSITIONS` 的 6 个里 |
| `transition.dur` | 0–0.60 s |
| `typography.number` | 必须在 `NUMBER_STYLES` 的 8 个里 |
| `typography.ratio` | 1.0–3.6 |
| `typography.tracking` | 0–8 px |
| `typography.line_gap` | 1.15–1.55 |
| `motif.density` | 0–1；为 0 只允许 `minimal` |
| `motion.enter` | `(起, 止)` 且 0 < 起 ≤ 止 ≤ 1.6 s |
| `motion.push` | 0–0.16（超过会把内容推出安全区） |

`selfcheck` 第 53 条另查两件 `visual_lint` 查不到的：8 套签名齐全（motion / typography 都有），
且**母题 / 转场 / 重心三元组不重复**（全同 = 这套语言只是复制粘贴）。

剪影是否真的可区分，由 `tools/silhouette_qa.py`（门禁 G13）量：同内容同段落在两套风格下
各渲 3 帧 → 灰度 → 480×270 → 四角亮度中位数三值化 → 比前景占比 F / 质心 (Cx,Cy) /
四边覆盖率 B / 左右对称度 S；五项里任一项达标即 PASS（ΔF≥6pp、|ΔCx|≥8pp、|ΔCy|≥8pp、
ΔB≥14pp、ΔS≥0.20），同一指标必须 3 帧全达标。当前实测 **28 对全过（同明暗族 12/12）**；
改造前基线同族只过 8/12。

### 9.5 加第 9 套风格时，视觉语言要补什么

主题与风格包的 5 步见 §7；v0.9 起还要多做四步，**否则新风格在剪影上仍然只是换色**：

1. **`stylekit.VISUAL_LANGUAGES["my-style"]` 写全五组**：motion（ease / enter / push /
   breathe_hz）、motif（kind + density + detail）、transition（kind + dur）、
   typography（tracking / line_gap / align / number / ratio）、bias（x / y / inset）。
   照抄一套现有签名再改配色 = 白做，第 53 条会报"母题/转场/重心完全相同"。
2. **母题不要临时发明新名字**：`motif.kind` 必须是 `VISUAL_MOTIFS` 里的 8 个之一。
   确实需要新图形语言时，先在 `scenes._motif_layer` 里实现一个分支、加进 `VISUAL_MOTIFS`，
   再在风格里引用——顺序反了会 `visual_lint` 直接拦。
3. **跑 `python stylekit.py --lint`**（主题对比度）与**单独跑 `visual_lint`**
   （写个三行的 `.py` 调 `stylekit.visual_lint("my-style")`，或直接 `--stage style`）。
4. **跑剪影门禁**：`python tools/silhouette_qa.py --styles my-style,tech-deep,...`，
   确认新风格与其余 8 套两两可区分；再跑 `selfcheck`（第 53 条会要求三元组不重复）。
5. **看片**：`--stage stills` 渲 3 段亲眼确认母题/转场/字距不是"看着像另一套"——
   数字过了不等于观感对了（铁律 6）。

