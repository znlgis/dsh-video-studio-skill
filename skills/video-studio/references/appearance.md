# 外观定制：设计 DSL 实操手册

> **外观由 `work/design.json` 决定：`propose.py` 现场采样，你确认之后冻结。**
> 没有现成外观目录可挑，也没有 `style` 字段——剧本不决定长相。
> 同一份稿子换一份 design.json 就是另一种画面，反之亦然。
> 这篇讲的是：**想控制画面的哪一块，就改 design.json 的哪个字段，怎么验证它真的生效。**

## 0. 三条正确姿势

| 你想要 | 怎么做 | 不要这样做 |
|---|---|---|
| 换一种气质 | `propose.py --revise "更冷一点，少装饰，换女声"` 重出方案 | 去找"第 9 套风格"——没有这种目录 |
| 微调某个细节 | 手改 `work/design.json` 的字段 → `designkit.py --lint` 校验 → `render.py --stage stills` 看 | 直接改 `filmkit.py` 里的常量 |
| 换整个画面语言 | 写自己的 `scenes.py`（放剧本同目录），实现 `draw_frame(scene, t_local, dur, t_global, script, frame)`（完整签名见技能正文「五步上手」） | 期望 `--engine web` 跟随 design（它不会） |

## 1. design.json 的六块

| 块 | 决定什么 | 字段 |
|---|---|---|
| `structure` | 几段、目标时长 | `segments` / `beats` / `hold_policy` / `target_sec` |
| `visual` | 画面全部外观 | 见 §2 |
| `audio` | 配乐 / 声线 / 音效 | `music`（scale/bpm/key/mood/seed）/ `voice`（id/map/jitter/max_distinct，**默认单声线**）/ `sfx`（enabled/density/gain_db） |
| `pacing` | 节奏 | `lead` / `tail` / `min_dur` / `hold` / `item_stagger` |
| `id` | 指纹 | `design:<hash>`，由 `designkit.design_fingerprint()` 算出 |
| `label` / `pitch` | 中文标签 / 立意 | 提案卡上显示的那一句话 |

校验：`python <SKILL_DIR>/scripts/designkit.py --lint work/design.json`；
契约：`<REPO>/schemas/design.schema.json`。**fail-closed**：缺字段/越界/对比度不达标一律报错，
不会静默回落——设计层不允许「改了不生效也不报错」这种状态存在。
（实测过 11 个静默失效的旋钮：`voice` 写字符串时 map/jitter 全失效、`pan` 对立体声配方无效……
每个都"配置生效、画面不动"，且不报错。设计层改成一律报错，这类问题在 `--lint` 就暴露。）

## 1.5 默认长什么样（素净 / 无细线 / 不抖）

**默认设计不带任何装饰细线，画面也不跟着镜头漂。** 这三条是产品约束，不是审美建议：

| 默认值 | 效果 | 想开回来怎么做 |
|---|---|---|
| `motif.kind = "plain"` | 装饰层整层不画（没有括角/刻度/页眉线/网格/扫描线），背景纹理 `none`，卡片无描边 | brief 点名「括角 / 细线 / 手绘 / 色块 / 极简 / 遮幅 / 信号 / 放射」，或手改 `motif.kind` |
| `hud = "none"` | 没有顶部进度条与胶囊描边（`chip-left` 是 6px 通栏块 + 1px 胶囊描边、`corner-line` 是一组细线） | 手改 `visual.hud`；画出来的线宽见 §2 |
| `motion.push = 0` | 不推镜。前景永远按整像素落位，段落中段是**整帧静止** | 手改 `motion.push` 只推背景板；前景不会跟着动（慢速亚像素位移在 1x 光栅上必碎成 1px 台阶） |

素净母题下所有装饰描边（强调条、对比中缝、流程轴、章节线、扫描高光、表格网格）
要么不画（图表网格、扫描高光）、要么加粗到 6px（强调条、对比中缝、流程轴）：**默认设计里不存在 1~3px 的线**。
多样性来自配色（连续色相）、版式轮换、字族、编号写法、转场、配乐与声线，
不来自细线装饰——`selfcheck` 第 71、72 条盯这两条：改了母题层或让前景跟相机，自检立刻 fail。
## 2. visual 字段表（能控制什么）

| 字段 | 取值 | 改它会发生什么 |
|---|---|---|
| `palette` | 11 个颜色角色（bg/bg2/surface/ink/muted/dim/accent/accent2/good/bad/rule），每个 `[r,g,b]` 或 `#rrggbb`（`#rgb` 也接受） | 整片色调。它不是"从 8 套配色里挑"，而是**连续色相空间**里的一点：改 `bg` 的色相，其余角色会按对比度网格重新求解 |
| `family` | `sans` / `serif` / `kai` / `round` | 全片字族（标题优先，字幕默认跟随） |
| `typography` | `tracking`（字距）/ `line_gap`（行距）/ `number`（编号写法，8 种）/ `ratio`（字号比例）/ `align` | 文字气质；`number` 决定"01 / 壹 / I / 1.0"这类编号外观 |
| `motif` | `kind`（9 种：**plain** / brackets / hairlines / handdrawn / slabs / minimal / letterbox / signal / rays）× `density`（0–1；采样器只取 0.55/0.70/0.85/1.00，`density=0` 只对 `plain` / `minimal` 合法） | 装饰语言。**默认 `plain`：不画装饰、不叠底纹、卡片无描边——默认设计没有细线**；其余是显式选择：四角括线 / 细线页眉 / 手绘框 / 色块 / 极简 / 遮幅 / 信号条 / 放射线 |
| `transition` | `kind`（dip / wash / paper / wipe / cut / iris）+ `dur`（秒） | 段与段之间的转场 |
| `motion` | `ease`（out3/out5/inOut3/outBack）/ `enter`(dx,dy) / `push`（背景镜头缓推，默认 0）/ `breathe_hz` | 入场与背景运动。前景**不跟镜头**：慢速亚像素位移取整后就是 1px 抖动 |
| `bias` | `x` / `y` / `inset` | 整屏重心偏移与安全区收缩 |
| `layout` | `left-stack` / `center-stack` / `split-right` | 默认版式 |
| `layout_cycle` | 版式数组（≥2 种不同） | 按章节轮换版式；采样器会派生 2–3 种，**手写 design 不写则不派生**（全片只用 `visual.layout`） |
| `hud` | `chip-left` / `corner-line` / `none`（默认 `none`） | 页眉/角落装饰。默认关闭（这两种都靠细线构形） |
| `size` / `fps` | `1920x1080` / `30` | **保留字段，不进渲染计划**：画幅与帧率的真源是 `--layout` 与 `film.json` 的 `video` 块（横屏 16:9 / 竖屏 9:16 都支持，竖屏配 `--layout portrait`）。写在这里只是文档性记录，不会改变输出 |
| `subtitle` | 见 §3 | 字幕字号/位置/对齐/背板/圆角/字族/颜色/行数 |

> **能组合出多少种画面？** 离散维度是 9 母题 × 6 转场 × 4 缓动 × 8 编号 × 3 版式 × 3 HUD × 4 字族
> × 7 情绪 × 2 调式（= 870,912 种），再乘配色这个连续量（色相 0–360° 任意取值）。
> 这个数字只说明"字段可组合空间足够大"，**不是推荐你从中挑**——好看与否仍由对比度体检、
> 分镜语法和你的目检决定。

## 3. 字幕样式（visual.subtitle）

字幕的字号 / 位置 / 背板 / 圆角由设计 DSL 决定；写死的常量会让**任何两条片子**的字幕带一模一样。
**不写 = 用默认值**，老 `design.json` 不受影响、字幕与默认常量逐像素一致。

```json
"subtitle": {"size": 46, "y": 560, "align": "left", "plate_alpha": 170,
             "radius": 10, "family": "sans", "color": [245, 245, 245], "max_lines": 2}
```

| 字段 | 默认 | 取值 | 备注 |
|---|---|---|---|
| `size` | 46 | 20–96 | 采样器默认跑 40–56 |
| `y` | null | 0–1030 | null = 贴底安全区；采样器 1/3 概率抬高到 560–880 |
| `align` | center | center / left | |
| `plate_alpha` | 170 | 0–255 | 采样器默认 96–194 |
| `radius` | 10 | 0–48 | 采样器默认 2–20 |
| `family` | null | sans / serif / kai / round | null = 跟随大标题字族 |
| `color` | null | [r,g,b] 或 #rrggbb | null = 默认墨色 |
| `max_lines` | 2 | 1–3 | 决定了折行上限 |

验证：`render.py --stage style` 会打印一行「字幕: ... 对齐=... 背板α=... 圆角=... 最多N行」，
改 design 后重跑这一行就能看重算结果，不用等渲完。

## 4. 版式轮换（visual.layout_cycle）

```json
"layout_cycle": ["split-right", "left-stack", "center-stack"]
```

- 元素取 `left-stack` / `center-stack` / `split-right`；**至少 2 种不同版式**，只写一种会被校验拒绝。
- 由 `grammar.plan_scenes` 按**章节**轮换：同一章内固定，换章才换（观众视角一章是一段整体）。
- 不写时 `design_to_plan` 按设计指纹自动派生 2–3 种，**默认就不是全片一张版式**。
- 审计：`--stage layout` 打印每段构图；`work/storyboard.json` 每段都有 `layout` 字段。

## 5. 配色怎么改才不出事

配色不是"挑一个好看的颜色"，而是 11 个角色要同时满足对比度：
`theme_lint` 的门槛是 ink≥7 / muted≥4.5 / dim≥3 / accent≥4.5 / accent2≥4.5（good/bad≥3），
另外要求 bg 与 bg2 有可感知的亮度差（防纯色底）。

推荐两条路：

1. **让采样器算**（默认）：采样器在 (hue, s, v) 网格里找满足全部门槛的点；
   实测纯随机取色通过率 35%，网格求解 100%。
2. **手改色相**：只改 `palette.bg` 的色相/饱和度，然后跑 `designkit.py --lint`；
   过不了就调一档明度——报错会直接点名哪个角色不达标。

> 片内**不要跨明暗族**：同一部片锁一个明暗族，段界跨族会整屏换底、触发闪烁门禁，观众也看着像换片。
> 这条在采样器里是硬约束，手改 design 时请自己守住。

## 6. 片内不重复：三层一起用

| 层 | 机制 | 防的是什么 |
|---|---|---|
| 段内 | `lines[].type`（10 类构图，grammar 自动选） | 每段都长一个样 |
| 章内 | `layout_cycle`（按章节轮换） | 全片一张版式 |
| 片间 | 采样 nonce + 候选 ≥2 维 + 底色色相分叉 + **片间去重台账** | 不同片撞衫 |

## 7. 常见误用

| 现象 | 原因 | 修法 |
|---|---|---|
| 改了 `design.json` 画面没变 | 没重跑阶段 / 没重签核 | `render.py --stage explain` 看哪些阶段过时 |
| `--stage style` 报「找不到设计」 | 工程里没有 `work/design.json` | 先 `propose.py --brief ... --script film.json --work work` 再 `--confirm` |
| 手改 design 后 `--confirm` 报校验失败 | 字段越界/对比度不达标 | 按报错逐条修；或干脆 `--revise` 重出 |
| 想"用某套预设当起点" | 没有预设目录，外观只由 design.json 决定 | 现场采样 + `--revise` 表达要求，要复现用 `--seed` |
| `render.py --preset x` 报错 | render.py 没有 `--preset` 这个参数 | 用 `--brief` 或直接写 design.json |
| `stylekit.py --list` 只有一段说明、exit 2 | 没有可选外观清单（预期行为） | 看 `designkit.py --list-palettes` 的配色采样示例（那不是可选清单） |
| 加密后的片子上还是有一根横向细线 | 那是 `hud=chip-left` 的通栏进度条（6px）或 `corner-line` 的角线（2px）——已经显式打开了 HUD | 改回 `hud: "none"`（默认），或换成素净母题 |
| 画面在「慢慢地动 / 轻微抖」 | `motion.push > 0`（只推背景），或自己写 scenes.py 把前景又绑回了相机 | 把 `motion.push` 设回 0；要整幅推镜请自写 scenes.py 做超采样 |

## 附：SKILL.md 正文迁入（原 0.5.1 外观从哪来：只有两条路）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

### 0.5.1 外观从哪来：只有两条路

**没有"内置风格"可选。** `film.json` 里写任何 `style` 值（`auto` / `legacy` / 某个 id）都会
被明确拒绝并给两步指引——剧本不决定长相，长相由 `work/design.json` 决定。
同理，技能里没有 `THEMES`/`STYLES`/`VISUAL_LANGUAGES` 表，也没有 `presets/`；
配乐由 `musicgen.py` 按参数现场编排，`scenes.draw_frame` 也没有 legacy 分支。

要特定外观只有两条路：

```bash
# ① 自然语言提要求（认得 must_have/avoid 里的关键词）
python propose.py --brief "科技发布会，深蓝，要遮幅" --script film.json

# ② 直接手写 design.json（字段全开放，配色是任意 RGB）
python render.py --script film.json --stage all     # 前提：work/design.json 已存在
```

详细契约见 [references/proposal.md](references/proposal.md) 与 `schemas/design.schema.json`。

## 附：SKILL.md 正文迁入（原 5.5 设计 DSL：外观的唯一来源）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

### 5.5 设计 DSL：外观的唯一来源

**没有"内置风格"可选。** 技能里没有 `STYLES` / `THEMES` / `VISUAL_LANGUAGES` / `presets/`；
`film.json` 里写任何 `style` 值都不影响画面。
外观的唯一来源是 `work/design.json`——由 `propose.py` 现场采样、你 `--confirm` 之后冻结。

```bash
python <SKILL_DIR>/scripts/propose.py --brief "科技发布会，深蓝，要遮幅" --script film.json --work work
python <SKILL_DIR>/scripts/propose.py --confirm --candidate A --work work   # -> work/design.json
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage style   # 看这份设计到底定成什么样
```

`design.json` 的六块（完整契约见 [references/proposal.md](references/proposal.md) 与
`<REPO>/schemas/design.schema.json`）：

| 块 | 决定什么 | 关键字段 |
|---|---|---|
| `structure` | 段落数与目标/下限时长 | `segments` / `beats` / `hold_policy` / `target_sec` / `target_mode`（exact / at_least） |
| `visual` | 画面：配色、字体、版式、动效、装饰、字幕 | 见下面的字段表 |
| `audio` | 配乐参数、声线、音效密度 | `music`（调式/BPM/调性/情绪/seed）/ `voice` / `sfx` |
| `pacing` | 节奏 | `lead` / `tail` / `min_dur` / `hold` / `item_stagger` |
| `id` / `label` | 指纹与中文标签 | `design:<hash>` / 一句中文立意 |
| `pitch` | 说清"这个方案想干成什么样" | 中文一句话 |

`visual` 的字段（`design.json` 可手改，改完用 `python <SKILL_DIR>/scripts/designkit.py --lint work/design.json` 校验）：

| 字段 | 取值 | 说明 |
|---|---|---|
| `palette` | 11 个颜色角色 | **连续色相空间**里的一点（`hsv` 求解），每套都必须过 `theme_lint` 对比度体检 |
| `family` | `sans` / `serif` / `kai` / `round` | 字族 |
| `typography` | `tracking` / `line_gap` / `number`（8 种编号写法）/ `ratio` / `align` | 排版 |
| `motif` | 9 种装饰母题 × 密度（默认 `plain`） | `plain` 是**素净**：装饰层整层不画、底纹为 none、卡片无描边——默认设计不带任何细线。其余 8 种才是显式选择：`brackets` / `hairlines` / `handdrawn` / `slabs` / `minimal` / `letterbox` / `signal` / `rays`（brief 点名「括角 / 细线 / 手绘 / 色块 / 极简 / 遮幅 / 信号 / 放射」即可采到） |
| `transition` | 6 种段界转场 + 时长 | `dip` / `wash` / `paper` / `wipe` / `cut` / `iris` |
| `motion` | `ease`（4 档）/ `enter` / `push` / `breathe_hz` | 入场与**背景**镜头缓推。默认 `push=0`（不推）：慢速推镜在整数光栅上必然变成 1px 台阶抖动，所以前景永远锁定、只有背景板缩放 |
| `bias` | `x` / `y` / `inset` | 整体重心偏移 |
| `layout` | `left-stack` / `center-stack` / `split-right` | 默认版式 |
| `layout_cycle` | 版式数组（可选） | 按章节轮换，见下 |
| `hud` | `chip-left` / `corner-line` / `none`（默认 `none`） | 页眉装饰。默认关闭：`chip-left` 是「6px 通栏进度条 + 1px 胶囊描边」，`corner-line` 更是一组细线 |
| `size` / `fps` | `1920x1080` / `30` | 横屏 16:9（1920x1080，可下采样 720p/540p）与**竖屏 9:16**（1080x1920，experimental）都支持；竖屏要配 `--layout portrait`，见 §7.5.1 |
| `subtitle` | 字幕样式（可选） | 见下 |

离散维度合计 **9 母题 × 6 转场 × 4 缓动 × 8 编号 × 3 版式 × 3 HUD × 4 字族 × 7 情绪 × 2 调式 =
870,912 种组合**，再乘配色这个连续量（色相 0–360° 任意取值，只是采样器会在锚点附近取）；
**这个数是"字段能组合出多少种"，不是"推荐你从中挑一套"**——好看与否仍由对比度体检、分镜语法和你的目检决定。

## 附：SKILL.md 正文迁入（原 5.5.1 字幕样式 visual.subtitle）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

### 5.5.1 字幕样式 `visual.subtitle`（可选）

字幕的字号 / 位置 / 背板透明度 / 圆角 / 字族 / 颜色 / 折行数都在设计 DSL 里。
**不写 = 补齐默认值**（与最朴素的字幕带逐像素一致），所以手写的旧 `design.json` 不受影响。

```json
"subtitle": {"size": 46, "y": 560, "align": "left", "plate_alpha": 170,
             "radius": 10, "family": "sans", "color": [245, 245, 245], "max_lines": 2}
```

| 字段 | 默认 | 范围 / 取值 | 作用 |
|---|---|---|---|
| `size` | `46` | 20–96 | 字幕字号 |
| `y` | `null` | 0–1030 | 字幕带基准线；`null` = 默认贴底安全区 |
| `align` | `center` | `center` / `left` | 对齐 |
| `plate_alpha` | `170` | 0–255 | 字幕背板透明度（0 = 无背板） |
| `radius` | `10` | 0–48 | 背板圆角 |
| `family` | `null` | `sans` / `serif` / `kai` / `round`；`null` = 跟随大标题字族 | 字幕字族 |
| `color` | `null` | `[r,g,b]` 或 `"#rrggbb"`；`null` = 默认墨色 | 字幕颜色 |
| `max_lines` | `2` | 1–3 | 最多折几行 |

采样器默认就会在这些区间里变化（字号 40–56、背板透明度 96–194、圆角 2–20、
多数居中偶尔左对齐、1/3 概率抬高），所以**默认出的片字幕也不会两条一模一样**。

## 附：SKILL.md 正文迁入（原 5.5.2 片内版式轮换 visual.layout_cycle）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

#### 5.5.2 片内版式轮换 `visual.layout_cycle`（可选）

`visual.layout` 是单值，只能给一个默认骨架；`layout_cycle` 给一串版式让
`grammar` 在**章节**维度上轮换：同一章内版式固定（观众视角里一章是一段整体），**换章才换版式**。
这就是"同一视频内部不要大面积重复"的落地方式。

```json
"layout_cycle": ["split-right", "left-stack", "center-stack"]
```

- 元素只能取 `left-stack` / `center-stack` / `split-right`；**至少 2 种不同版式**（只写一种等于不轮换，
  `designkit.validate_design` 直接报错，不静默放过）。
- **不写**：`design_to_plan` 按设计指纹自动派生 **2–3 种**的轮换序列——默认就不会"全片一张版式"。
- 可审计：`--stage layout` 打印每段选了哪类构图，并落 `work/storyboard.json`，
  每段都带 `layout` 字段（哪一章用了哪个骨架一眼可查）。

## 附：SKILL.md 正文迁入（原 5.5.5 三条硬约束）

> 以下是 SKILL.md 瘦身时**原样迁移**的内容（为避免「精简即丢信息」，一字未改）；与本文上文重复处以上文细则为准。

#### 5.5.5 三条硬约束（违反会让片子"花"或者被门禁拦）

1. 同一部片**不许跨明暗族**换主题（深蓝段直接切暖白段会精确触发闪烁门禁，观众也看着像换片）。
2. 主题必须过 `stylekit.theme_lint` 的对比度体检（ink≥7 / muted≥4.5 / dim≥3 / accent≥4.5 / accent2≥4.5）。
3. **默认整片只有一个旁白声音**（`max_distinct=1`）。只有用户明确点名「多人对话 / 对谈 / 访谈 / 广播剧 / 分角色 / 男女声」时才采多声线，且固定 2–3 把；裸词「对话」「多人」不算——「大模型的对话能力」不是要两个旁白。显式说「一个人念 / 同一个人」则强制单声线。
