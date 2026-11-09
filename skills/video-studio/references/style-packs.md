# 风格包（Style Packs）：让每部片的画风 / 配乐 / 旁白都不一样

> v0.5.0 新增。读这一篇之前先读 SKILL.md 的 §5.6。
> 一句话：**一个 style id 同时决定画风、配乐、旁白声音和节奏**，不能三样各自乱配。

## 0. 为什么需要它（v0.4 的病）

v0.4 及以前，所有"身份参数"都是模块级常量：

| 层 | 被写死的地方 | 结果 |
|---|---|---|
| 画风 | assets/scenes.py 的 THEME 字典 + 3 个绘制函数 | 每部片都是深蓝底 + 橙强调 |
| 配乐 | scorekit 的 4 个小调 + 3 套和弦进行 + 固定动机 | 每部片都是 Dm / 120BPM 那一套 |
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
    画面（scenes.py 读主题）    配乐（scorekit 读风格）    旁白（voicekit 读 persona/tone）

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
| lines[].voice/tone/rate/pitch/volume/lead/tail | — | 逐句覆盖（最高优先级） |

## 4. 三级指纹：改错别字不会换整首曲子

| 指纹 | 由什么算 | 决定 |
|---|---|---|
| full | 标题 + 全部台词 | 音色细节（seed 派生） |
| tempo | 标题 + kind 序列 + 体量桶 | BPM 与调性 |
| style | 标题（配合关键词评分破平） | 风格 id |

所以：改一个错别字 -> full 变（音色纹理微变），tempo/style 不变（还是同一首曲子、同一个调）；
换一部片 -> 三个都变。要完全锁死就写 music.seed + music.seed_from_content: false。

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

## 7. 加一套自己的风格（5 步）

1. stylekit.THEMES["my-style"] = _theme(...)（配色 + 材质 + 排版 + 版式 + 动态 + HUD）
2. stylekit.STYLES["my-style"] = {...}（theme / layouts / music{bpm_ladder,keys,mood} / voice / pacing）
3. STYLE_IDS 元组末尾追加（只追加不改顺序，否则指纹映射会漂）
4. python stylekit.py --lint 全绿；配乐侧在 scorekit.MUSIC_STYLES 里加同名条目
5. 用 3 段样片跑 --stage stills 亲眼看（铁律 6：能出图 != 没问题）

## 8. 常见误用

| 现象 | 原因 | 修法 |
|---|---|---|
| 画面换了、配乐还是老的 | 只改了 visual.theme 没改 music.style | 直接改 style，或两个都写 |
| 同一部片前后像两部片 | 段界跨了明暗族 | 用同族主题；或拆成两部 |
| 改台词后配乐变了 | 改了标题或 kind 结构 -> tempo 指纹变 | 写死 music.key/music.bpm，或 seed_from_content:false |
| 旁白听感像广播剧 | max_distinct 太大 / 相邻句频繁换人 | 降到 2，靠 tone 而不是换人做变化 |
| 字幕在浅色主题上看不清 | 用了 v0.4 的黑底白字 | 传 theme= 给 draw_subtitle（render.py 已自动传） |
