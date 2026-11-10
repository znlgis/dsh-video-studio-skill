# 更新日志

版本号规则：`主.次.修订`；技能正文（SKILL.md / references / scripts / assets）有实质改动才升次级。

**Git tag 约定（v0.8.0 起统一）**：每个发布版本打 annotated tag，名字**一律带 `v` 前缀**（`v0.8.0`），
与 `package.json` 的 `version` 字段只差前缀。历史上曾同时出现 `0.7.1` 与 `v0.7.1` 两种写法
（前者指向一个未推送的孤儿提交，已删除），此后不再产生不带 `v` 的 tag。

## 0.9.1 — 把装饰母题烘焙进底版：消掉 5.6 ms/帧的固定开销（2026-11-10）

> 0.9.0 落地后量了一笔账：单帧预算 25 ms，而"逐帧把全屏 RGBA 母题 paste 到画面上"
> 实测是 **5.6 ms/帧**（Pillow 13 / 1080p），是预算里最大的一笔固定开销——
> 比 list/hero 的绘制本体（约 3 ms）还贵。
>
> 0.9.0 的注释写的是"paste 只要 0.3 ms"，这是估算不是实测；这次用
> `draw_frame` 分段计时才发现差了近 20 倍。**估算当实测写进文档，就是下一个坑。**

### 改了什么

| 项 | 0.9.0 | 0.9.1 |
|---|---|---|
| 装饰母题的合成方式 | 母题单独烘焙成 RGBA 图层，**逐帧 `img.paste(lay, (0,0), lay)`** | 母题直接烘焙进**背景底版**（`_plate`），逐帧只 `copy` + `EXTENT`，不再单独 paste |
| 单帧固定开销 | 约 5.6 ms（paste） | 约 **1.3–2.0 ms**（底版 copy） |
| 装饰与镜头缓推 | 背景推、装饰不动（cinema / epic 的强 push 下会穿帮：框线留在原地，背景在动） | 装饰跟着底版一起推，两者同一张图，**不再穿帮** |

实现位置：`assets/scenes.py` 的 `_motif_key` / `_motif_layer` / `_plate`（母题图层与
合成底版共用同一把缓存键，改主题色时一起失效，否则会拿到"新图层 + 旧底版"的错配画面）。

### 实测数据（本机 Windows 11 / Python 3.12 / ffmpeg 9.0.2；换机请重测）

| 项 | 实测 |
|---|---|
| 模板片 frames 阶段 | **54–61 fps（约 16.7 s / 870 帧 ≈ 19 ms/帧）**；v0.8.1 约 75 fps（约 13 ms/帧）；0.9.0 未优化时 41–50 fps |
| `draw_frame` 本体（1080p） | `list` **3.1 ms** / `hero` **3.0 ms**（优化前 7.4 / 7.3 ms） |
| 最贵组合（10 类场景 + 字幕 + `tobytes`） | `epic·outro` **15.1 ms**，仍在 25 ms/帧预算内 |
| 金帧基线 | 再次重录（3 帧）；最终以仓库里的 `assets/golden_frames.json` 为准 |

### 结论口径（写文档时别写反）

**仍在预算内（约 19 ms/帧 vs 25 ms），但比 v0.8.1 的约 13 ms/帧慢**。
慢的原因是 10 类构图各自多了装饰与几何——这是功能换来的，不是回退；
0.9.1 只是把其中那笔 5.6 ms 的固定开销收掉了。

### 行为变化

| 变化 | 0.9.0 | 0.9.1 | 影响 |
|---|---|---|---|
| frames 速度 | 约 41–50 fps | **约 54–61 fps** | 更接近 v0.8.1，但仍慢于它 |
| 强 push 风格（cinema / epic） | 装饰层不随镜头推，边框与背景分离 | 装饰与背景一起推 | 观感更整；这两个风格的静帧像素与 0.9.0 不同 |
| 金帧基线 | v0.9.0 的 3 帧 | **重录** | 自己复刻过基线快照的要再 `--write` 一次 |

## 0.9.0 — 场景语法 + 视觉语言包：换风格不再只是换色（2026-11-10）

> 本轮解决的是 v0.5 没有解决的那一半问题。v0.5 的风格包让"画风/配乐/旁白一起变"，
> 但它只覆盖**皮肤**：配色、字体、纹理、颗粒、字距、缓动、HUD、3 种版式几何。
> 画面骨架（剪影）没变——用户的原话是"每个视频都差不多，只是主题和颜色不同"。
>
> 根因在代码里摆着：`assets/scenes.py` 当时只有 3 个绘制器（hook / body / outro），
> quote / emphasis / calm 三种 kind 全部回落到 body；三个绘制器共用同一套"背景板 → 眉标 →
> 大字 → 副标 → 强调线 → HUD"骨架，8 套风格换完只剩配色不同。
> 用剪影判据量改造前的 HEAD 快照：8 套风格两两 28 对里 **4 对完全不达标**（同明暗族 12 对里
> 只过 8 对）——换风格真的只是换色，这不是审美口水。
>
> v0.9 的做法不是"再加几套风格"，而是把"内容 → 构图"独立成一层，
> 再给每套风格补**结构签名**。三层正交：场景语法（选构图）→ 视觉语言包（怎么画）→
> 片内编排（同一部片里不复制粘贴）。

### 一、改了什么：三层正交

| 层 | 新增/改动 | 落点 |
|---|---|---|
| **① 场景语法** | 新模块 `scripts/grammar.py`（纯标准库，可单测、可被 `scenes.py` 反向 import）。按内容给 **10 类构图**打分选型：`hero` / `statement` / `list` / `stat` / `compare` / `process` / `quote` / `chapter` / `chart` / `outro`。规则：字段存在性 > 关键词 > 结构位置；段长门（每类有最短时长）；连续同类型 ≤2 段；平手按固定顺序取位 | `grammar.SCENE_TYPES`（grammar.py:30）、`MIN_DUR`（:49）、`DOWNGRADE`（:63）、`plan_scenes` → `work/storyboard.json` |
| **② 视觉语言包** | `stylekit.VISUAL_LANGUAGES`：8 套风格 + legacy，每套在主题色之上补**五组结构签名**——`motion`（缓动/入场区间/镜头 push/呼吸频率）、`motif`（装饰母题：brackets / hairlines / handdrawn / slabs / minimal / letterbox / signal / rays）、`transition`（段界转场：dip / wash / paper / wipe / cut / iris）、`typography`（字距/行高/对齐/编号写法/字号比）、`bias`（整体重心偏移 px）。装饰母题**预烘焙**成一张全屏 RGBA 图层，逐帧 paste。当时注释写的是"paste 约 0.3 ms"——**这是估算不是实测**，0.9.1 实测为 5.6 ms/帧，随即改成烘焙进底版（见 0.9.1 段） | `stylekit.py:568`（`VISUAL_LANGUAGES`）、`:681`（`visual_lint`）、`scenes.py:888`（`_motif_layer`）、`scenes.py:1020`（`_plate`，v0.9.1）、`:1068`（`_apply_veil`） |
| **③ 片内编排** | 版式变体（每类型 3 个、相邻段强制不同）、装饰密度 3 档（0.55 / 0.75 / 1.0，每 3 段回落）、相位偏移（防同频"复印"）。全部由 sha1 派生，**不读 random / time / hash()** | `grammar.variant_of / decor_of / phase_of`，写进 timeline 的 `scene_variant / scene_decor / scene_phase` |

配套改动：

- **`assets/scenes.py` 第三节重写**：3 个老绘制器 → **10 类构图绘制器**（`_DRAWERS`，
  scenes.py:1979），每类独立的视觉重心、留白与持续动效；新增段内 veil 转场
  （`_apply_veil`，只在段首 ≤0.45 s 内生效——相邻两段可能是完全不同的构图，整屏瞬切会
  精确触发闪烁门禁）。
- **film.json 新增 7 个行级可选字段**：`type` / `quote` / `by` / `stats[]` / `series[]` /
  `steps[]` / `sides{}`。全部可选；不写就从 `text` / `items` 推导（`grammar.derive_*`）。
  `type` 与 `kind` **正交**：kind 管声线与配乐能量，type 只管构图。**没有新增 kind 值**
  （会撞 voicekit 的语气 id 与 filmcheck 的 `KINDS`）。
- **`--stage layout` 打印分镜**：每段一行"id / kind → type / 变体 / 密度 / 为什么"，
  同时落 `work/storyboard.json`（审计产物：能看到每段为什么选这个构图
  → render.py:553）。
- **`--stage style` 打印视觉语言**：母题 / 转场 / 字距 / 编号写法 / 重心偏移各一行，
  `visual_lint` 在同一个 stage 就拦坏配置（render.py:322）。
- **新工具 `tools/silhouette_qa.py`**：跨风格剪影可区分门禁 **G13**（见下"门禁与兼容"）。

### 二、门禁与兼容

| 项 | 内容 |
|---|---|
| **legacy 逐字节不变** | 不写 `style` 字段 = 走 v0.4 三个老绘制器（`_LEGACY_DRAWERS`，scenes.py:232），timeline 不注入场景字段、不写 storyboard。selfcheck 第 26 条查 legacy 的配乐/旁白/节奏参数；**逐字节**另有实测：用 `git show HEAD` 取 v0.8.1 的 scenes/stylekit/filmkit，与当前树对同一份 legacy 剧本（4 句：hook/body/quote/outro，且故意塞入 `scene_type` 等新字段）各渲 4 帧比 sha256，**4/4 完全一致**（证据 `.team/legacy_byte_compare.json`，可复跑 `.team/legacy_evidence.py`）。注意金帧基线**不覆盖**这条——它渲的是 `style: auto` 的模板片，不走 legacy 路径 |
| **新判据 G13（剪影可区分）** | 同一份合成剧本只换 style，每个场景类型渲 3 帧（t=0.05/0.5/0.9×dur）→ 灰度 → 降采样 480×270 → 以四角亮度中位数为基准三值化 → 量五项：前景占比 F / 前景质心 (Cx,Cy) / 四边覆盖率 B / 左右对称度 S。两两五项里**至少一项达标**即算可区分（ΔF≥6pp、|ΔCx|≥8pp、|ΔCy|≥8pp、ΔB≥14pp、ΔS≥0.20），且同一指标必须在 3 帧上全部达标。判据写死在 `tools/silhouette_qa.py:125` |
| **G13 挂在哪** | 可选离线工具 + `selfcheck` 第 55 条（缺工具则 skip，不阻塞 CI）；**不进 `--stage qa` 的账本**——它渲的是合成剧本，不是成片。口径见 `references/scene-grammar.md` 与 `references/style-packs.md` |
| **`visual_lint`** | 母题名 / 转场名 / 转场时长（0–0.60 s）/ 编号写法 / 字号比（1.0–3.6）/ 字距（0–8 px）/ 行高（1.15–1.55）/ 母题密度 / 入场区间 / push（0–0.16）越界，`--stage style` 直接 `SystemExit`，不等到渲完 300 帧 |
| **ci_check** | `LOCAL_MODULES` 加 `grammar`（第 5 项依赖白名单）；`package.json` 的 `files` 加 `tools/silhouette_qa.py`（第 6 项打包卫生）。仍是 12 项 |
| **schema / filmcheck** | `lines[].type` 枚举 + 四个嵌套结构（stats / series / steps / sides）写进 `schemas/film.schema.json` 与 `filmcheck.py`。`type` 写错会被 filmcheck 报 **ERROR**；嵌套键拼错报 WARN（"是不是想写 X"） |
| **金帧基线** | 已重录（3 帧，人工看过帧再 `--write`，不是盲写）。`assets/golden_frames.json` 的 `env_fp` 与三个 sha256 同时更新 |
| **selfcheck** | 51 → **55** 条：52 场景语法（10 类分流/确定性/段长门/显式 type）、53 视觉语言包（8 套签名齐全且无重复）、54 场景类型单真值（4 处集合相等）、55 剪影可区分（G13） |

### 三、行为变化（升级前先看这几条）

| 变化 | v0.8.1 | v0.9.0 | 影响 |
|---|---|---|---|
| 画面骨架 | 8 套风格共用 3 个绘制器，换风格只换色 | 全新构图 + 结构签名；同一段内容可画成 10 类构图 | **升级后必须重看静帧**：`--stage stills` 的图会明显不同，这是本次的目的 |
| `work/golden_frames.json` 基线 | 3 帧 sha256（v0.8 值） | 已重录为 v0.9 值 | 自己复刻过金帧基线的项目要 `--write` 重录一次 |
| `lines[].type` 写错 | 字段不存在，filmcheck 报未知键 WARN | **ERROR**（枚举校验） | 以前"写了不生效"的脚本会当场失败——这是契约兑现 |
| 场景数据超容量 | 静默截断，无提示 | filmcheck 报 **`scene_capacity` WARN**：`stats` >3 / `series` >6 / `steps` >5 / `sides` 每侧 >3（绘制器只画前几条） | 有 WARN 不阻断，但那些条目**不会出现在画面上**，该拆段就拆段 |
| `--stage style` 输出 | 风格 / 主题 / 配乐 / 旁白 / 节奏 | 多一行"视觉语言: 母题=… 转场=… 字距=… 编号=… 重心=(+x,+y)" | grep 旧输出的脚本不受影响（只多不少） |
| `--stage layout` 输出 | 时间轴统计 | 多"分镜: N 段 / 类型×次数"与每段选型理由 | 同上 |
| 渲染速度 | 模板片 frames 约 75 fps（约 13 ms/帧） | **约 41–50 fps（约 20–24 ms/帧）**；0.9.1 优化后回到 **54–61 fps（约 19 ms/帧）** | 单帧预算 25 ms。如实说：**比 v0.8.1 慢**，慢在 10 类构图各自多了装饰与几何；换机必须重测 |
| `selfcheck` 条数 | 51 | **55** | `grep 'PASS 51'` 的脚本要改 |
| `ci_check` 项数 | 12 | 12（白名单扩展） | 本地模块白名单加 `grammar`；打包 `files` 加 `tools/silhouette_qa.py` |

### 四、已知限制（本版没做，写下来别装看不见）

- **web 引擎不跟随场景语法**：`--engine web` 的页面由用户自己写（`assets/web/film.js`），
  本次不改造。web 路线的画风仍只有主题色，没有 10 类构图；剪影门禁也只覆盖 PIL 渲染器。
- **同一部片仍锁一个明暗族**：段界跨明暗族会整屏换底、精确触发闪烁门禁，观感也像换片。
  主题覆盖（`scene["theme"]`）只允许同族。
- **`lines[].type` 写错只有 filmcheck 拦**：grammar 对非法值**静默回落**（当没写，按内容
  自动选）。不跑 filmcheck 的流水线，写错就只是"没生效"。
- **场景类型有 4 份真值来源**（`filmcheck.SCENE_TYPES` / `grammar.SCENE_TYPES` /
  `scenes._DRAWERS` / schema enum），靠 selfcheck 第 54 条断言"集合相等"兜底。
  **顺序允许不同**：grammar 的那份顺序用于平手取位，另外三份只看成员。
- **版式变体只有 4 类绘制器读**：`variant_of` 对每段都算并写进 storyboard，
  但当前只有 hero / statement / list / stat 消费它，其余六类构图唯一。
  "每类型 3 个变体"目前只在四类上成立。
- **部分视觉语言字段"已声明、已体检、但绘制器没读"**：`motion.enter`、`motion.push`、
  `typography.align`、`typography.ratio`、`layout_cycle`——写了不报错，也不改变画面。
  详见 `references/style-packs.md`。
- **`work/storyboard.json` 是审计产物，不是输入**：改它不改变渲染结果；要固定构图请写
  `film.json` 的 `lines[].type`。

### 五、验证数据（本机 Windows 11 / Python 3.12 / ffmpeg 9.0.2；换机请重测）

| 项 | 实测 |
|---|---|
| 场景语法自检 | `selfcheck` 第 52 条：10 段稿子分出 ≥5 种构图、同一稿两次选型与变体一致、2 秒段里的 chart 被降级且理由写明、显式 `type=hero` 生效 |
| 视觉语言自检 | 第 53 条：8 套风格签名齐全、`visual_lint_all` 全过、无重复签名（母题/转场/重心三元组不撞） |
| 单真值自检 | 第 54 条：grammar / filmcheck / `scenes._DRAWERS` / schema enum 四处集合一致 |
| **剪影 G13（改造后）** | **28 对全部通过**（同明暗族 **12/12**，跨族 16/16）；同族对靠 Cx/Cy/S 等结构指标通过，不是靠明暗差 |
| **剪影 G13（改造前基线，同一工具跑 HEAD 快照）** | 28 对通过 **24**、失败 **4**（`vivid×epic` / `cinema×epic` / `paper×lo-fi` / `vivid×cinema`）；同明暗族只过 **8/12**。这 4 对就是"换风格=换色"的直接证据 |
| 模板片渲染 | 0.9.0 落地时 frames 阶段约 **41–50 fps（约 20–24 ms/帧）**；0.9.1 把装饰烘焙进底版后为 **54–61 fps（约 19 ms/帧）**，v0.8.1 约 **75 fps（约 13 ms/帧）**。见 0.9.1 段 |
| 金帧 | 3 帧（36 / 270 / 369）重录后与当前渲染像素逐位一致；legacy 路径仍走基线 |
| 自检 / 仓库体检 | `selfcheck` **PASS 55 / FAIL 0 / SKIP 0**；`ci_check` **PASS 12 / FAIL 0**；`filmcheck` 模板片 0 error / 0 warning |

### 六、升级须知（v0.8.1 → v0.9.0）

1. **画面一定会变**。这是本次的目的，不是回归。升级后先跑
   `--stage stills --stills "l01:3,l02:5,l03:2.5"` 亲眼看静帧，再决定要不要调 `lines[].type`。
2. **想要稳定的观感，写显式 `style`**（不要 `"auto"`）：`auto` 会按内容换风格，
   而不同风格现在连骨架都不同；锁定 id 才能保证"这一版和上一版看起来是同一部片"。
3. **想沿用老画面**：`film.json` 不写 `style` 字段即可回到 v0.4 三个老绘制器（逐字节不变），
   代价是同时失去 v0.5 的配乐/旁白风格包。
4. **跑一遍 `filmcheck`**：`lines[].type` 拼错会变成 ERROR，`stats/series/steps/sides`
   超容量会给 `scene_capacity` WARN——这两类以前都不会报。
5. **金帧基线变了**：自己维护过基线快照的，要按当前渲染重新 `--write`（先人工看帧）。
6. **新增模块要一起拷**：`scripts/grammar.py`（纯标准库，缺了 `scenes.py` 会退到兼容构图
   并打 WARN）。`tools/silhouette_qa.py` 只在仓库工具链里，不进技能目录。

## 0.8.1 — 全项目深审：修掉 60+ 处「看着没问题、其实没在查」（2026-11-10）

> 本轮没有新功能。起因是一次**全量复核**：把 14 个 Python 模块（约 3.4 万行）、5 个前端文件、
> 12 篇 references、仓库工具链与插件入口全部过一遍，每条结论都要求「改前能复现失败、改后能证明通过」。
> 分八个方向并行做，最后统一回归。结论是 v0.8.0 的能力基本可信（selfcheck 51/51、ci_check 12/12、
> 模板片全链 pass 11 / fail 0 / skipped 1），但仍有几类缺陷被系统性低估：
>
> 1. **门禁判了却没落到退出码**——stdout 打 [FAIL]、账本记 fail，进程仍 exit 0；
> 2. **门禁自己算错**——VMAF 参考帧与成片字幕折行口径不一致，同源好片被判 8.55 分 fail；
> 3. **配置写了却没人读**——`qa.vmaf.sample_step`、`visual.layout` 全链断裂；
> 4. **静默产出错片**——web 引擎 720p 输出的是 1080p 原画的左上角裁切，逐像素相同却不报错。
>
> 版本号走 patch（0.8.0 → 0.8.1）：对外接口与默认出片规格没变，但**若干行为变严格了**，
> 升级前请读下面的「行为变化」表。

### 一、门禁与结论可信度（P0/P1）

| # | 模块 | 问题 | 后果 | 修法 |
|---|---|---|---|---|
| 1 | `qavmaf` | 参考帧渲染不传 `words`，而 `render` 的 frames/stills **总传** → 字幕按词折行 vs 字符折行 | 38 字字幕断点差一个字（2% 像素），近静态画面 **VMAF 99.95 → 8.55**，同源好片被判 **fail** | 新增 `_draw_subtitle_like_render()`，与渲染侧同口径取 `sub_words_at`；实测 46/48 帧有差异 → 0/48 |
| 2 | `render` | `--stage style/stills/frames/mux` 的早退分支丢掉 `_finish_gates()` 返回码 | 门禁 fail、stdout 打 `[FAIL]`，**退出码仍是 0**；CI 把失败当成功 | `rc = _finish_gates(a); return 1 if (rc or _LEDGER.fails()) else 0` |
| 3 | `stagewatch` | `--strict-stale` 的 `source-changed` 不看"本 stage 本轮就要重跑" | 改一句台词后 `--stage style --strict-stale` / `--stage all --strict-stale` **在第一个 stage 直接失败** | 新增 `source-pending`（soft）：planned stage 只报 `[待重算]`，不参与 strict |
| 4 | `render` | 自动补跑的 stage 不在 pending 里 | `--stage tts` 顺带排轴时，刚写出的 tts.json 被判成"输入变了还用旧产物" | `_stage_begin` 把正在跑的 stage 并入 pending |
| 5 | `render` | `visual.theme_overrides` 只作用于 scenes 背景，**不作用于字幕** | overrides 把 ink/bg 调成低对比时，体检报通过、字幕压在同色背景上 | `_theme_for()` 优先返回 `plan["theme"]`（合并后的最终主题） |
| 6 | `qa_frames` | `sync_info.judged` 只数"存在同名 kind 的 cue"，不看事件是否真在 2s 配对窗内 | `judged=1`、`sync=[]`、`errors=[]`，`require_sync=True` 也拦不住（**门禁看着查了 1 条，其实 0 条**） | 用与 `sync_check` 同一个 `_match_cue` 判；judged 只算真的配上 |
| 7 | `webengine` | `textqa` 空扫描（`--from 2 --to 1`）、`--step 0`、`--step ≥0.6` 全部打印"重叠 0…"并 **exit 0** | 门禁是绿的，实际一行没查；`step≥0.6` 时"一闪而过"判据**数学上不可能触发** | 新增 `QA_BRIEF_S=0.6` + `_check_qa_window()`，非法窗口一律 ValueError（CLI exit 2） |
| 8 | `webengine` | 页面 `qaText` 返回 `{}` 或 frames=0 时只判了 `isinstance(dict)` | 四类缺陷全 0 的**假 PASS** | `_check_qa_result()`：四个数组字段齐全、frames>0 才放行 |
| 9 | `webengine` | 页面缺 `getCues/getEvents` 时静默返回空数组 | cues.json 0 条，配乐/音效照跑，**整片悄悄变哑** | 调 `_page_contract` 检查，缺失即 RuntimeError |
| 10 | `webengine` | `style=None` + `--width 1280` 时页面画布仍是 1920x1080，截图=视口 | 输出的是**原画左上角裁切**（与裁切实测逐像素相同，diff_px=0），标题/HUD 全丢，exit 0 | `open_page` 读 canvas `getBoundingClientRect`，可视区未铺满视口即报错 |
| 11 | `webengine` | `engine.js` 无条件把 `window.FPS` 覆盖成注入值 | 文档承诺的"fps 不一致就报错"**从未触发过**（页面声明 60、timeline 30 时静默改 30） | engine.js 先读页面声明值；不一致时不覆盖，交给 `open_page` 报错 |
| 12 | `qaasr` | 抽轨失败直接抛异常 | 破坏 `report()` "总是返回 dict" 契约，render 的 catch-all 记 **G12 fail「门禁自身失败」**，把环境问题说成片子问题 | 抽轨/后备引擎套 try，按三档记 skipped + 原因 |
| 13 | `stylekit` | 对比度门禁只拿 bg/bg2 的**中点**当背景（背景是整屏渐变） | lo-fi 的 muted 对 bg2 只有 **4.42**（AA 要 4.5），中点 4.84 把门槛"平均"过去——门禁 PASS、成片下半屏副标不达标 | 新增 `bg_contrast()` 取两端最差；lo-fi muted 调为 (84,98,94)（5.44/4.56） |
| 14 | `gates` | 账本里 status 不是 pass/fail/skipped 时只加了个没人看的键 | 报告结论写"**通过**：所有门禁本次都执行且通过"；非 dict 条目直接 AttributeError | 未知状态按 fail 计并单列"账本损坏条目" |
| 15 | `filmkit` | `detect_black` / `detect_silence` 在 ffmpeg 失败时返回 0 | qa_report 无告警，G8「首尾之外无长黑帧」直接 **pass**（把"没查"当"通过"） | 失败返回 None，qa_report 转 `[WARN] 本项不能当作通过` |
| 16 | `filmkit` | 降级拼接给无音轨段补静音取 `max(段长, 参照段长)` | 3s 有声 + 1s 无声 → 输出 6.000s（音频比视频长 2s，ffmpeg rc=0 不报错） | 改取该段自身时长 |
| 17 | `filmkit` | 拼接前只比有无音轨，不比采样率/声道 | 48k + 44.1k 段 `-c copy` 放行，按参照段解释 → **整段变调**（440Hz → 478.6Hz） | `_probe_concat_part` 增探 `sample_rate`/`channels`，不一致即重编码 |
| 18 | `filmkit` | `max_dur` 把段时长压到 `lead+语音` 以下 | 场景变短但旁白仍播完整句 → **下一句语音压在本句上**（两条人声重叠） | 硬下限取 `max(min_dur, lead+voice)`，截不到目标时 WARN |
| 19 | `stylekit` | `visual.layout`（文档 + schema 都有）**两条渲染路径都不生效** | 三档版式渲同一帧 sha256 完全相同 | 显式 layout 时补写 `theme["layout"]` 与 `layout_default` |
| 20 | `selfcheck` | 第 51 条把 `golden_frames` 的 **skipped 当 PASS** | 本机 Python 3.12.14 / 基线 3.12.16 → 工具判 skipped，selfcheck 却报"0 帧像素一致"计 PASS：**这条门禁从没真的比过一帧** | 按 `status` 三档判；`golden_frames` 的 env 指纹改成 Python 只比 major.minor（补丁号不影响抗锯齿），实测三帧像素逐位相同 |

### 二、静默产出错片 / 静默失效（P1/P2）

| # | 模块 | 问题 | 修法 |
|---|---|---|---|
| 21 | `filmkit` | `lead<0.12` 时 `write_srt`/`write_ass` 写出 `-1:59:59,880` 非法时间码（播放器整条丢弃） | 落盘前钳 `t0>=0`、`t1>=t0` |
| 22 | `filmkit` | 词边界折行对"单个比 max_w 还宽"的 token 不断开（实测 1761px > 1560），而 `qa_text_safe` 用字符折行量宽度看不见 | 词边界折行结果超宽时回退字符折行 |
| 23 | `filmkit` | `tts` 的 v1 老缓存分支没校验 trim：`trim_silence=false` 仍命中裁剪版音频 | 条件补 `bool(m["trimmed"]) == bool(trim)` |
| 24 | `filmkit` | `FrameSink.close` 先 `wait()` 后读 stderr，编码错误刷满 64KB 管道即死锁；BrokenPipe 会顶掉真实错误 | 先排空 stderr 再 wait；报错带 exitcode |
| 25 | `filmkit` | `CueBus.resolved` 文档写"丢弃并告警"，实际静默 continue | 丢弃时打汇总 WARN（分"超片长/负时刻"计数，实例级去重） |
| 26 | `render` | `--resume` 的分块指纹只覆盖 total/frames/fps/chunk + scenes + script，**不含 timeline 内容** | 指纹加 `timeline` sha1；字幕窗口/词级断点变化不再静默复用旧分块 |
| 27 | `render` | `run_batch` 预检失败分支写台账前没建父目录 | 补 `os.makedirs`；"一条都没跑"的说明与台账不再丢失 |
| 28 | `render` | `qa.vmaf.sample_step` 全链断裂（filmcheck 只认零消费者的 `sample_s`，render 从没透传） | 键名对齐 schema + qavmaf；render 透传 `sample_step` |
| 29 | `scorekit` | 承诺的"指纹破平"是**恒等项**（每行同一常数），同分永远按 STYLE_IDS 顺序 | 把 style id 混进哈希；4 个同分标题从"全落 tech-deep"变成各半 |
| 30 | `scorekit` | 积分响度**多减一次 -0.691**，报告值恒定偏低 0.69 dB | 去掉多余常数（块响度定义里那个保留）；12 s 片 -15.65 → -14.96，与 BS.1770 差 1.19 → 0.49 dB |
| 31 | `scorekit` | 铺底包络释音是覆盖式：bpm>≈223 时包络在 pad_dur 处从爬坡值跳到 1.0（一声"噗"） | 释音改乘法；bpm≤200 全部组合与旧公式逐位一致 |
| 32 | `webengine` | CLI 五个子命令的 `--deterministic` 用 store_true，未传时 False 把 `WEBENGINE_DETERMINISTIC=1` 当"明确关掉" | 改三态（`store_const` + `default=None`） |
| 33 | `webengine` | `new_page`/`new_cdp_session` 失败或 worker 被 cancel 时不关浏览器 | 加 `BaseException` 清理（`CancelledError` 不继承 Exception） |
| 34 | `webengine` | `chunk_s<=0` 被静默夹成"1 帧/块"（360 帧拆 360 块）；duration/from/to 为 NaN 时炸成不可读 TypeError | 开浏览器前校验正数/有限数 |
| 35 | `webengine` | 页面 cue/event 时间为 NaN/Infinity 时写进 cues.json（非法 JSON 字面量） | `_finite_t()` 非有限即报错并点名 |
| 36 | `preflight` | TTS 只比 mp3 **个数**：`l09.mp3` 顶替 `l04`（数量相同）→ pass；0.05s 音频配 8 字台词也 pass | 加"文件名↔行 id 对账"与"时长<0.06s/字"两条 warning |
| 37 | `estimate` | 没有 timeline.json 时帧率**写死 30**（`video.fps` 读了不用） | 24fps 片 484 → 387 帧；60fps 不再低估一半 |
| 38 | `estimate` | `qa.vmaf`/`qa.asr` 不是对象时 `.get()` 直接 AttributeError traceback | isinstance 归一化 + 一行 WARN |
| 39 | `estimate` | 非法 `video` 块被 `except Exception` 静默吞掉，仍打印 `ok:true` | 拆 ImportError / Exception，后者明说 render 会拒片 |
| 40 | `estimate` | VMAF 成本模型是 v0.7.2 拍的 1.0–1.6×，无 v0.8 实测支撑；`mode=sample` 完全不分档 | 改 `0.40–1.00×` + sample 按 step 折算；29 s 片 total 58.8–108.2 → **37.5–83.0 s**（实测 47.3 s） |
| 41 | `estimate` | 分块建议传给 `chunk_plan` 的是封顶 12 的 jobs_eff，而 render 用 `--jobs` 原值 | `--jobs 16` 时预演 120/8 段 vs 实际 60/15 段 → 传 jobs_req |
| 42 | `golden_frames` | `build_timeline` 没传 fps（默认 30），24/25/50/60 fps 片的基线帧号不是真实帧号 | 走 `video_plan`；模板片基线无需重录 |
| 43 | `index.js` | `applyFilesystemProvider(...)` **没 await**：异步 reject 逃出 try/catch → unhandledRejection（Node ≥15 默认杀进程） | 加 `await`；stub 复现改前捕获到 rejection、改后 null |
| 44 | `filmcheck` | 与 `filmkit.video_plan` 是**两套分辨率真理**：`1600x900` filmcheck 放行（0 error 0 warning）而 render 拒片 | video 块改为直接调 `filmkit.video_plan`，只做错误翻译 |
| 45 | `filmcheck` | `qa.vmaf.enabled` / `qa.require_vmaf` / `qa.ssim.enabled` 全仓无消费者却合法通过 | 标 `inert` 并 WARN（"写它不生效，真正生效的是 mode=off"） |
| 46 | `filmkit` | `fit_sub_lines` 定义了两次（旧版是死代码，两版签名不同） | 删旧版；`grep` 确认仅剩一个定义 |
| 47 | `sfxkit` | 无 scipy 时谐振器 IR 被 `cap=20000` 截断：长尾（t60 5–7 s）在 -25 dB 处跳变，听感"咔"一声 | 二阶系统 IR 有闭式解，改向量化解析计算（截断精确、快两三个数量级） |
| 48 | `sfxkit` | `Body.render` 无 scipy 时每个模态一次 FFT（N 模态 N 次卷积） | 合并加权模态 IR 再卷积一次（数学等价；有 scipy 路径逐样本不变） |
| 49 | `sfxkit` | `data_chirp(rate=0)` 抛 ZeroDivisionError；`rate<0` 静默丢脉冲只剩哑音 | 入口校验正数 |
| 50 | `stylekit` | `pick_style` 结构加分选中时文案写"关键词命中："后空一截（报告没发生的事） | 改报"无关键词命中，按题材结构加分选中（…）" |
| 50b | `voicekit` | 书名号 `《` 被当成引语起始符：正文写"今天我们聊《设计模式》"会触发 quote 语气，并把 persona 从 narrator_male 换成 narrator_female | **旁白中途换人**（而这句根本没有人引语）。`_QUOTE_OPEN` 移除 `《` |
| 50c | `voicekit` | `parse_pct("+4.5%")` 正则只认整数 → 静默回落 0 | 与 `"+0%"` **撞同一个 TTS 缓存键**："把语速从 0 调到 4.5%"完全没生效、还复用旧音频，且无任何报错。正则改为接受小数并按四舍五入取整（`parse_hz` 同理） |
| 50d | `filmcheck` | `lines[].persona` 拼错时 voicekit **静默回落**到 kind 默认声线（reason 里有 `unknown_persona` 但不报错） | schema 只校验 `t=str`，拦不住；补上"枚举从 voicekit 单向读取"的 WARN + 拼写建议（tone 已有 enum ERROR，不重复报） |

### 三、仓库门禁自身（`ci_check` / `selfcheck`）

| # | 位置 | 问题 | 修法 |
|---|---|---|---|
| 51 | `ci_check` | 只有 PASS/FAIL 两态，"跳过（没装 node）"被记成 **PASS** | 三档 `PASS/FAIL/SKIP` + `--strict`（SKIP 也算失败）；汇总行与"跳过项"分列 |
| 52 | `ci_check` | 打包卫生的 `must` 是**手写列表**，对新增文件天然免疫（gates/stagewatch/preflight/qavmaf/qaasr/golden_frames 全不在里面） | 改派生式：`skills/ schemas/ locale/ tools/ docs/` 下磁盘文件必须全部进包 |
| 53 | `ci_check` | junk 后缀不含 `.bin`，且派生 must 与 `files` 排除规则互相打架 | junk 增 `.bin/.dll/.exe/.so/.dylib/.pyd/.db/.sqlite` 与"路径含未展开 `%`"；派生 must 跳过 junk |
| 54 | `ci_check` | 第 8 项只查语法：`zip(strict=)` / `bit_count()` / `tomllib` / `match` / `except*` / `dataclass(slots=)` / `X|Y` 注解在 3.9 上都是运行期错误 | 加 AST 运行期 API 扫描（只查确定不兼容的几类，不按 `BinOp|BitOr` 一律报错） |
| 55 | `ci_check` | 版本一致性只查 package.json ↔ README 徽章 | 加 CHANGELOG 顶部版本（"发了新版但 CHANGELOG 没动"历史真发生过） |
| 56 | `selfcheck` | 顶部 docstring 只列到 43 条，CASES 实际 51 条 | 补齐 44–51 并注明"CASES 是唯一真理" |
| 57 | `schemas` | `qa.vmaf.pass/warn` 描述写"默认 88/94"，权威值是 **92/96**，还引用了不存在的 `render.QA_VMAF_PASS` | 改 92/96 + 指向 `qavmaf.py`；惰性键补文档 |

### 四、文档与代码不一致（`docs`）

| # | 位置 | 问题 |
|---|---|---|
| 58 | `references/v08-quality.md` | 给出 `qaasr.py ... --json`，而 qaasr **没有** `--json`（照抄即 exit 2）；且没说 CLI 默认线与 G12 的关系（现已同源 0.85/0.72） |
| 59 | `SKILL.md` §1 | 在 estimate 命令块里写 `--vmaf off|sample`——estimate **没有** `--vmaf`；且 `sample` 不会让 G11 记 skipped |
| 60 | `references/audio.md`、`style-packs.md` | 宣称 `music.seed_from_content: false` 能关掉内容指纹的结构变奏——它只影响 seed 派生，结构变奏照旧 |
| 61 | `references/change-impact.md` | 分块指纹路径写成 `work/_plan.json`（实际 `work/segments/_plan.json`） |
| 62 | `references/audio-variation.md` | 把 scorekit legacy 断言错记成 `selfcheck.py` 第 20 条（那条是 sfxkit；scorekit 侧在 `scorekit.py --selftest`） |
| 63 | `README`/`SKILL`/`v08-quality` | v0.8 门禁计数"三道/四项"三种口径打架（账本实际 G0/G10/G11/G12 四个） |
| 64 | `references/quality-gates.md` | 标题写"G0–G6"而内容已含 G7/G8 与 G0–G12 映射 |
| 65 | `SKILL`/`web-engine` | `webengine --selftest` 写 31 项（现 35 项） |
| 66 | `style-packs.md` | 把 `lines[].rate/pitch/volume/tail` 说成"逐句覆盖（最高优先级）"——voicekit 只读 persona/voice/tone，filmcheck 还会报未知键 |

### 行为变化（升级前先看这几条）

| 变化 | v0.8.0 | v0.8.1 | 影响 |
|---|---|---|---|
| `--stage style/stills/frames/mux` 的退出码 | 恒 0 | 门禁 fail / `--strict-gates` 下有 skipped 时为 **1** | 交付脚本若靠退出码判定，现在会真的拿到失败 |
| `--resume` 的分块计划指纹 | 只含时长/帧数/fps/scenes/script | **新增 timeline 内容** | 老项目的 `work/segments/_plan.json` 会判"计划已变化"，需删一次 `work/segments`（报错信息里已写 `rm -rf`） |
| `qaasr` CLI 默认阈值 | 0.95 / 0.90（比 render 的 G12 严） | **0.85 / 0.72**（与 G12 同源） | 同一部片 render 判 pass、单独跑 CLI 判 fail 的矛盾消失 |
| `qa.vmaf.sample_step` | 合法但**没人读**（永远按默认步长 5） | 真的生效 | 写了它的项目抽样密度会变 |
| `qa.vmaf.enabled` / `qa.require_vmaf` / `qa.ssim.enabled` | 合法、静默无效 | filmcheck 报 **WARN**（惰性键） | `filmcheck --strict` 下会失败；要跳过 G11 请写 `mode=off` |
| `estimate` 的 VMAF 成本模型 | 1.0–1.6× 片长（29 s 片报 31–50 s） | **0.40–1.00×**（12.6 s 起），sample 按 step 折算 | 预演 total 58.8–108.2 → **37.5–83.0 s** |
| `webengine` + `--width/--height` + `style=None` | 静默输出原画左上角裁切 | **报错**（画布未铺满视口） | legacy 项目要 720p 请走 PIL 路线，或给页面加 `__STYLE__` 支持 |
| `webengine` 页面 FPS 与注入值不一致 | 静默改成注入值 | **报错** | 页面声明 60 而 timeline 30 的项目会当场失败（这是承诺兑现） |
| `ci_check` 汇总 | `PASS n / FAIL n` | `PASS n / FAIL n / SKIP n` + 跳过项单列 | 用 `grep 'PASS 12'` 的脚本要改；`--strict` 可让 SKIP 也失败 |
| `selfcheck` 第 51 条 | 金帧 skipped 也记 PASS | 真比像素；skipped 记 SKIP | 本机现在是真比（3 帧像素逐位一致） |
| `stylekit` lo-fi 主题的 muted | (86,100,96)（对 bg2 只有 4.42） | (84,98,94)（5.44/4.56） | 该主题的副标颜色略有变化 |
| `voicekit` 的 `《` | 当引语 → 该句换 quote 声线 | **不再当引语**（书名号是书名） | 写过"《书名》"的片子，那几句会回到正文声线（这才是对的） |
| `voicekit` 的 `parse_pct` | `"+4.5%"` 静默变 0（与 `"+0%"` 同缓存键） | 真的按 4.5% → 取整 5 | 写过小数的项目会重新合成那几句（缓存键变了，这是期望行为） |

### 升级须知（v0.8.0 → v0.8.1）

1. **删一次分块计划**：`rm -rf work/segments`（指纹新增 timeline，旧计划会判"已变化"）。
2. **CI 里别再用 `grep 'PASS 12 / FAIL 0'`**：现在是三档汇总；要"没执行也算失败"就加 `--strict`。
3. **web 引擎 720p 交付**：`style=None` + `--width 1280` 现在会报错（以前静默裁切）。改法二选一——
   走 PIL 路线（`--engine pil` + `video.width/height`），或让页面读 `window.__STYLE__`。
4. **`filmcheck` 变严**：`video` 块走 `filmkit.video_plan`（1600x900 之类会报 error）、
   三个惰性 qa 键会报 WARN。跑一遍 `python scripts/filmcheck.py film.json` 就知道自己的剧本有没有踩。
5. **不需要重录金帧基线**：`assets/golden_frames.json` 与当前渲染像素逐位一致（30 fps 模板片）。

### 实测数据（本机 Windows 11 / Python 3.12.14 / ffmpeg 9.0.2；换机请重测）

| 项 | 实测 |
|---|---|
| 全链 `--stage all --cost`（29.00 s / 870 帧，全新 work） | 总 **55.1 s**：tts 3.1 · score 5.0 · sfx 0.9 · audio 0.6 · frames 14.6 · mux 1.8 · **qa 29.0**（VMAF 14.1 + ASR 9.0） |
| 门禁汇总（1080p 交付） | pass **11** / fail 0 / skipped 1（signoff）——G4 1920x1080@30 · G5 -16.5 LUFS/-1.4 dBTP · G6 锐度比 1.009 · G10 4 句 64 词 · G11 **VMAF 97.26 / SSIM 0.9928 / 870 帧 / 14.1 s** · G12 召回 **0.844** |
| 自检 / 仓库体检 | `selfcheck` **PASS 51 / FAIL 0 / SKIP 0**；`ci_check` **PASS 12 / FAIL 0 / SKIP 0** |
| 模块自检 | sfxkit **16/16** · scorekit **21/21**（36.5 s）· qa_frames **22/22**（5.7 s）· webengine **35/35**（22.4 s）· qavmaf **11/11** · qaasr **13/13**（2.2 s） |
| 无 scipy 兜底 | `tools/selftest_noscipy.py sfxkit scorekit` 全 PASS；sfxkit 的兜底路径现在用**解析参照**验数值（以前只验"没崩"） |
| 金帧 | 3 帧（36/270/369）像素逐位一致，`status=pass`（修复前因补丁号差异被判 skipped 却记 PASS） |

### 已知限制（本版没做，写下来别装看不见）

- **`music.style` 单独出现时并不"只换配乐"**：`resolve_script_style` 的 legacy 判定要求
  `style / visual.theme / music.style` **全部**没写才走 legacy，所以只写 `music.style` 会走 auto，
  画风/旁白/节奏一起换。要修得同时改 stylekit 与 `render.st_score`，本轮未做。
- **`chapter_layout` 的"按章节轮换版式"仍未实现**：scenes.py 只读 `theme["layout"]`（全片一个），
  web 只读 `layout.default`。本轮只让**显式** `visual.layout` 生效。
- **`lines[].rate/pitch/volume/tail` 仍不生效**：voicekit 只读 persona/voice/tone（文档已改正，
  filmcheck 会报未知键）。要逐句改语速/音高请换 persona 或 tone。
- **G9 的"同步查了 0 条"**：PIL 路线需要 cue 带数值 `visual_at` 才会真的配对；`judged=0` 是
  "没有可比对的证据"，不是"查过且通过"（报告里已如实写）。
- **`preflight` 字形检测仍按 sans 族**：serif/kai/round 主题只发一条 `glyph_family_unchecked` warning。
- **`theme_overrides` 的数值键仍接受 NaN/Inf**：未知 `family` 由 filmkit 静默回退 sans（刻意的"拼错不炸渲染"）。
- **12 分钟片峰值内存约 1.25 GB**（scorekit 铺底缓存 × 多路 float64），8 GB 机器能跑但余量不大。
- **`--batch` 是串行**，且不把 `--jobs/--resume/--preview` 传给子进程。

## 0.8.0 — 词级时间轴 / 画幅参数化 / 批量 / 三道新门禁（2026-11-10）

> 本轮的主线是「把已经能看到、但抓不住的东西变成阻断性门禁」：词级时间戳（字幕断行的依据）、
> 全片 VMAF（不是抽 4 帧看锐度）、ASR 回读（音轨里念的到底是不是剧本那几句）。
> 顺带把画幅/帧率参数化、批量生产、预览片与运行版本化补齐。
> 口径仍是三档：`pass` = 这次真的查了且通过 / `fail` = 查了没过 / `skipped` = **这次没执行**（不等于通过）。

### 新增能力

| # | 能力 | 实现 | 怎么用 |
|---|---|---|---|
| 1 | **词级时间轴** | `filmkit.tts(..., words=True)` 用 `edge_tts.Communicate(boundary="WordBoundary")` + `stream()` 同时收音频与词事件；音频先写 `.part` 再 `os.replace`（防断网截断缓存）；meta 增 `words / words_key / words_events / words_shift / words_ok`；新函数 `tts_words_key / trim_silence_bounds / words_normalize / words_summary / sub_words_at / wrap_by_words`；`fit_sub_lines(..., words=)` 与 `draw_subtitle(..., words=)` 按词边界折行；`build_timeline(..., fps=)` 给每段补 `words_abs` | `film.json` 写 `"words": true`（**默认 true**）；`--stage tts` 落 `work/words.json` |
| 2 | **cue 词锚点** | `cue_time` 新增 `"l03:w2"` / `"l03:w2+0.1"`（**从 1 数**）；找不到该词退化为句偏移并 WARN，不让一个写错的锚点炸掉整条 cues | `sfx.cues[].at` 或 `scenes.cues()` 里直接写 |
| 3 | **渲染前资源预检** `scripts/preflight.py` | `check(script, *, work, engine, timeline) -> {ok, errors, warnings, checks}`；检查项 script / glyphs / tts / fonts / web_page；字形按**字段实际字重**逐字检测；纯函数，不打印不落盘 | `render.py` 在 `frames` 之前调用，记门禁 **G0**；`--no-preflight` 可跳过（QC 报告留痕） |
| 4 | **全片 VMAF/SSIM** `scripts/qavmaf.py` | 参考帧由场景函数**实时重渲**（不是成片抽帧），rawvideo 从 stdin 喂 ffmpeg 与成片逐帧比；一次调用同时算 VMAF 与 SSIM；门禁 **G11** | `--vmaf full\|sample\|off`（默认 full）；阈值**只有一份**：权威定义在 `qavmaf.py`（`VMAF_PASS=92.0` / `VMAF_WARN=96.0`，SSIM 0.95/0.98 仅诊断），`render.py` 不设第二套常量；按片覆盖写 `qa.vmaf.pass / warn` |
| 5 | **ASR 回读** `scripts/qaasr.py` | 成片音轨转文字与剧本逐句对账：faster-whisper > ffmpeg whisper 滤镜 > `skipped`；字符级**多重集**召回 + 每句偏移；门禁 **G12** | 默认跑；`"qa": {"asr": {"enabled": false}}` 可关；阈值 `QA_ASR_RECALL_PASS=0.85` / `QA_ASR_RECALL_WARN=0.72` |
| 6 | **画幅/帧率参数化（只做 16:9 横屏）** | `filmkit.video_plan(script)`：`"video": {"width":1280,"height":720,"fps":24}`；内部构图**恒 1920x1080**，编码时 ffmpeg `scale` 下采样（`FrameSink(out_w=, out_h=)`）；timeline 增 `video` 块；`filmkit.qa_report` 新增 `expect_w / expect_h / expect_fps`，G4 改按**交付规格**比对；`encode_quality` 参考帧缩到交付分辨率再比 | 不写 `video` = 1920x1080@30（v0.7 行为）。竖屏 / 非 16:9 / 上采样 / 非法 fps **直接报错**；允许 1920x1080 / 1280x720 / 960x540；fps 24/25/30/50/60 |
| 7 | **批量生产** `render.py --batch films.json` | 清单可为 `["a/film.json"]` 或 `{"films": [{script, scenes, work, out, label}]}`；**上限 100 条 / 1 MiB**；**启动前全量 filmcheck 预检**（任一条不过则一条都不跑）；逐条子进程渲染、单条失败不阻断其余；末尾 `batch-report.json` + 退出码（全成功 0 / 部分失败 1） | `python render.py --batch films.json` |
| 8 | **运行版本化** | `--run-id`（缺省 `rYYYYMMDD-HHMMSS`）；`work/gates.json` 增 `runId` 与 `inputs`（script / scenes / words / filmkit 四个指纹）；收尾时 `gates.archive_run` 把账本快照 + `QC-REPORT.md` + `run.json` 复制到 `work/runs/<runId>/`；QC 报告多「运行编号 / 输入指纹」两行 | `--run-id hero-v3` |
| 9 | **预览片** | `--preview N` 只渲前 N 秒 -> `out/preview/<片名>_preview.mp4` + `.gif`（480 px / 10 fps，两遍调色板）；音轨先截到预览时长再混入；**不落交付账本** | `--preview 15` |
| 10 | **web 引擎跟随风格包** | `webengine.open_page/render_frames/render_full/page_meta/export_cues` 增 `style / width / height / fps`；页面加载前 `add_init_script` 注入 `window.__STYLE__`（色板/版式/材质/画布）；**style=None 时逐字节等于 v0.7**（连 add_init_script 都不调）；注入后断言页面真的读了它（`FK.style` 非空、画布尺寸与注入一致），防止"老页面静默忽略"的假通过 | `--engine web` 自动生效；`webengine.py --selftest` 24 -> **31 项** |
| 11 | **金帧回归** `tools/golden_frames.py` | `--write` / `--check`；渲 3 帧算 SHA-256 与 `skills/video-studio/assets/golden_frames.json` 比对；同环境像素变了 = fail，字体指纹不同 = `skipped: font differs`（跨机不误报） | `python tools/golden_frames.py --check` |

### 行为变化（升级前先看这几条）

| 变化 | v0.7.2 | v0.8.0 | 影响 |
|---|---|---|---|
| `words` 默认值 | 无此字段（无词级） | **默认 `true`** | 老项目第一次 `--stage tts` 会**把每句重新联网合成一次**（老缓存里没有 `words_key`）；不想付就把 `"words": false` 写进 `film.json` |
| `qa` 阶段时长 | 只跑容器/信号/帧级 | 增 G0 / G10 / G11 / G12 | 29 s 片全链从约 28 s 涨到 **47.3 s**，其中 `qa` **23.9 s**（VMAF 14.2 + ASR 8.7）——**qa 已超过渲染本身**；着急出片用 `--vmaf off` |
| 三档门禁行数 | pass 8 左右（`stale / G4–G9 / signoff`） | 多 4 行（`G0 / G10 / G11 / G12`） | 交付报告矩阵变长；`skipped` 项目要逐项说明理由 |
| 输出规格 | 恒 1920x1080@30 | 可配，**但默认仍是 1920x1080@30** | 不写 `video` 块的项目行为不变；720p 交付时 G4 按 1280x720 比对（已实测通过） |
| `--engine web` 画面 | 深蓝橙写死 | 跟随 `style` plan（`window.__STYLE__`） | 旧页面若不读 `__STYLE__` 会**当场报错**（以前是静默画成旧配色）；`style=None` 路径逐字节不变 |
| 自检条数 | 43 | **51** | 新增 44 词级 / 45 画幅 / 46 金帧确定性 / 47 依赖漂移 / 48 批量清单 / 49 TTS 后端 / 50 web 风格载荷 / 51 金帧回归；grep `PASS 43` 的脚本要改 |
| 成本预演 | estimate 只算渲染链路 | 含 preflight / vmaf / asr 三段 | 29 s 模板片预演 total **28.4 s -> 58.8–108.2 s**（vmaf 单阶段 31–50 s 是瓶颈）——**v0.8 之后出片时间不再只是渲染时间** |
| `ci_check` | 12 项 | 12 项（白名单扩展） | 依赖白名单加 `faster_whisper`；本地模块白名单加 `preflight / qavmaf / qaasr`；体积检查忽略 `.team` 与媒体后缀 |

### 升级须知（v0.7.2 -> v0.8.0）

1. **新增必拷文件（在 `<SKILL_DIR>/scripts/` 下）**：`preflight.py`、`qavmaf.py`、`qaasr.py`。
   严格说它们是**可选**的——不拷对应门禁记 `skipped` 并写明原因，不会让渲染失败
   （`render.py` 用 `_try_module` 探测）；但交付时 `skipped` 必须逐项说明，**别把它当"通过"**。
2. **默认行为变化 ①：词级默认开着**。老项目第一次 `--stage tts` 会把每句重合成一次
   （音频本身可以复用，但词级事件必须重新联网采集）。不接受额外网络请求 / 离线交付的，
   在 `film.json` 顶层写 `"words": false`，行为回到 v0.7。
3. **默认行为变化 ②：qa 变慢，而且已比渲染贵**。29 s 模板片实测 VMAF 14.2 s + ASR 8.7 s，
   全链 47.3 s（`qa` 23.9 s vs `frames` 12.5 s）。**输出仍默认 1920x1080@30**——
   不写 `video` 块的项目画面参数完全没变。
4. **旧报告不能直接拿来对新版本**：`work/gates.json` 增 `runId` 与 `inputs`，QC 报告多「运行编号 / 输入指纹」
   两行；`G4` 的 detail 现在写的是**交付规格**（`1280x720@24` 是合法输出），不再恒为 1920x1080。
5. **`G4` 容器门禁改按交付规格比对**：工程里如果有脚本断言"G4 detail 一定是 1920x1080@30"，
   改用 `timeline.json` 的 `video` 块对账（`filmkit.qa_report` 的 `expect_w/expect_h/expect_fps`）。
6. **grep 输出**：`PASS 43` -> `PASS 51`；`webengine --selftest` 24 项 -> **31 项**；
   `references/quality-gates.md` 顶部新增「两套编号别混用」映射表（方法论的 G0–G8 与账本 id 不同义）。
7. **批量/预览是新入口，不是新 stage**：`--batch` / `--preview` 都在 `--stage` 之前分流，
   不改变 `--stage all` 的 10 个阶段顺序。

### 实测数据（本机 Windows 11 / Python 3.12 / ffmpeg 9.0.2；换机请重测）

| 项 | 实测 |
|---|---|
| 全链 `--stage all --cost`（29.011 s / 870 帧，全新 work） | 总 **47.3 s**：style 0.0 · tts 3.4 · layout 0.0 · cues 0.0 · score 3.4 · sfx 0.5 · audio 0.5 · frames 12.5 · mux 1.9 · **qa 23.9**（VMAF 14.2 + ASR 8.7） |
| 门禁汇总（1080p 交付） | pass **10** / fail 0 / skipped 1（signoff）——G4 1920x1080@30 · G6 锐度比 **1.009** · G10 4 句 64 词（末词偏差 0.09 s）· G11 **VMAF 97.22 / SSIM 0.9928 / 870 帧 / 14.2 s** · G12 召回 **0.803 / 8.7 s** |
| 门禁汇总（720p@24 交付，同一剧本） | pass **10** / fail 0 / skipped 1——G4 1280x720@24 / 696 帧 / 29.00 s · G6 锐度比 **1.206** · G11 **VMAF 97.10 / SSIM 0.9925 / 7.2 s** · G12 召回 **0.853 / 6.4 s** |
| 720p 与 1080p 的画质差 | VMAF 只差 **0.12 分**（qavmaf 作者独立素材测得 -0.22）——"内部 1080p 构图 + 编码时 lanczos 下采样"是超采样式降规格，不是糊 |
| 门禁判据标定（qavmaf 作者实测，参考帧含字幕与淡入淡出） | crf17 **97.24**（pass）· crf30 **95.74**（pass + warning）· 二次编码 + 缩放坏片 **29.45**（fail）——阈值由此定为 **92 / 96** |
| 另一份参考（1080p 纯测试图，无字幕，不可直接对比） | crf18 **96.98** / 22 96.32 / 26 94.87 / 30 **92.25** / 36 **83.03** / 46 50.23（crf30 恰好压在 92 失败线上，说明这档会抖） |
| G11 抽样模式 | `--vmaf sample`（每 5 帧取 1）**8.0 s** 跑完 184 帧，与全片差 **0.34** 分 |
| G11 线程无关性 | threads=0/1/4/8/16 与跨进程复跑的逐帧分数 sha1 **全同** |
| G12（small / CPU int8） | 加载 1.99 s + 转写 3.79 s = **6.18 s**（第一轮）；干净成片召回 **0.803–0.853**（4 段全对上、缺句 0） |
| G12 丢句反证 | 把 l03 从音轨剪掉 -> 召回 **0.677**、`missing_lines=["l03"]`、gate=fail、exit 1 |
| G12 后备引擎 | ffmpeg whisper 滤镜（ggml-tiny）：19.87 s 音频 3.75 s，召回 **0.723**（只作后备） |
| 词级（模板片） | 4 句 / **64 词**；末词偏差最大 **0.09 s**；词覆盖率约 0.7；`words_shift` 约 0.1 s |
| 词级退化验证 | 不传 `boundary`（默认 SentenceBoundary）时词事件数 = **0**；30 字句开 WordBoundary 得 **13** 个词事件 |
| 渲前预检 | 干净剧本 **< 1 s**（tts / web_page 视情况 skipped）；故意写 ✓ / 🚀 / ↵ 在**渲第一帧之前** FAIL，报出「字符 + 字段 + 句 id」 |
| 画幅（720p@24 交付） | `--stage all` 出片 EXIT=0，G4 = `1280x720@24`、G11 pass；锐度比 1.206（同片 1080p 为 1.009，**跨分辨率不可比**——参考帧也缩到交付分辨率，笔画占比变了） |
| 批量（2 条 29 s 片） | **67.2 s / 60.1 s**，各 `pass 11 · fail 0 · skipped 1`；清单里故意坏一条 -> 预检阶段 exit **1**、一条都没跑 |
| 预览 | `--preview 15` 出 `out/preview/film_preview.mp4` + `.gif`（480 px / 10 fps），音轨按 15 s 截断后混入 |
| 金帧 | 3 帧（36 / 270 / 369）sha256 全等，`status=pass`；故意改主题色 / 改 1 个像素 -> fail 并列出变化帧；字体指纹不同 -> skipped |
| 成本预演（`tools/estimate.py`） | 29 s 模板片 total **58.8–108.2 s**（v0.7 为 28.4 s）；瓶颈识别为 **vmaf 31–50 s**、asr 6–10 s |
| 自检 / 仓库体检 | `selfcheck` **PASS 51 / FAIL 0 / SKIP 0**；`ci_check` **PASS 12 / FAIL 0**（CI 不接受旧的 43 条断言） |

### 已知限制（本版没做，写下来别装看不见）

- **web 模板页仍是 12 s 独立演示**：`assets/web/film.js` 只有 3 幕 12 秒，换剧本要**重写场景**才能出全片；
  "拷模板 + `--engine web`" 只能得到演示片。web 路线也因此没有可重渲的参考帧，**G11 恒 `skipped`**。
- **qa 时间显著上升，已经超过渲染本身**：29 s 模板片全链 **47.3 s**，其中 `qa` **23.9 s**（VMAF 14.2 + ASR 8.7）、
  `frames` 12.5 s。v0.7 的全链约 28 s。**批量出片的瓶颈从渲染转到质检**；要压回渲染量级
  就 `--vmaf off` 或 `--vmaf sample`。另外**同机负载不同能差近 2×**（另一轮同代码 VMAF 测到 28.8 s），
  文档里的秒数只代表"这一跑"，不是承诺。
- **VMAF 成本口径**：全片门禁 = 参考帧渲染 + 比较。1080p 模板场景约 **0.5× 片长**（29 s 片 14.2 s）；
  创意场景 `draw_frame` 更重（30–50 ms/帧），整体按 **0.4–1× 片长** 估，12 分钟片按 0.4× 外推约 **5 分钟**。
  纯 libvmaf 比较本身约 **1.2× 实时**——这是两个口径，别混着引用（12 分钟片纯比较约 10 分钟）。
  720p 交付便宜得多（696 帧 7.2 s），因为帧数与比较分辨率都降了。
- **720p / 540p 的 VMAF 阈值仍按 1080p 标定**：分数不完全可比（比较分辨率变了），qavmaf 会打 warning
  提醒但**不因此改判**。实测同片 720p 比 1080p 只低 **0.12 分**（独立素材 -0.22），
  说明"内部 1080p 构图 + 编码时 lanczos 下采样"是超采样式降规格，不是糊——但跨分辨率比 VMAF 仍要谨慎。
- **ASR 对中英混排专名会掉召回**（"DeepSeek 4.1 Flash" 被听成 "DIXX4.1flash" / "DeepX Carnacy"），
  干净模板片也只有 **0.803–0.853**。所以默认线定在 **0.85 / 0.72**：0.72 以下才说明"整句丢了或音轨不是这一版"，
  0.72–0.85 只给 warning。这条门禁的定位是**抓整句丢失 / 音轨不是这一版，不是逐字校对**。
- **ASR 偏移检查依赖 `voice_start`**：手搓 timeline 缺这个字段时，每句都会多算一个 `lead`（默认 0.55 s），
  全片报偏移 warning。这是判据的已知限制，不是片子坏了。
- **G11 的 SSIM 只是诊断项**：文本画面 SSIM 对字幕边缘过于敏感，低于阈值只记 warning、不阻断。
- **画幅只做 16:9 横屏**：竖屏 / 非 16:9 直接拒绝；2560x1440 在允许列表里但会被上采样规则拒掉
  （要真高清得改内部构图分辨率，不是放大输出）。
- **`layout` 只改标题/导语锚点与卡片栏位几何**（`title_x / title_y / title_w / kicker_y / sub_w /
  panel_x / panel_y / panel_w / panel_h / panel_gap`），**不重排字号**——字号在主题的 `th["scale"]` 里，
  改 layout 不会让字变大。HUD 也不由 layout 决定（在主题的 `hud` 字段）。想改字号改主题，不是改 layout。
- **preflight 的字形检测按 sans（msyh）口径**，serif / kai / round 主题的实际字族覆盖没有逐一核对
  （它会在 warnings 里提示这一点）；金帧只覆盖 PIL 引擎 + 模板片，web 引擎的像素回归不在范围内。
- **批量是串行**（逐条子进程），没有并行跑多条；每条都要付全片 VMAF 成本，批量不是摊销而是线性叠加。
- **`--preview` 不写交付账本**，它只是给人快速看效果；正式结论仍以 `--stage qa` 的报告为准。
- **`gates.json` 的 `inputs.words` 是"文件存在则记 sha1、不存在记 missing"**，
  所以"没启用词级"和"启用了但还没跑 tts"在报告里都显示 `missing`，要结合 G10 的 detail 判断。

## 0.7.2 — 全项目深审：修掉 30+ 处「看着没问题、其实没在查」

> 本轮没有新功能。起因是一次完整的项目复核：把 14 个 Python 模块（约 2.7 万行）、10 篇 references、
> 插件入口与发布链全部过一遍，用**真实出片**验证每一条承诺，而不是只读代码。
> 结论是 v0.7.1 的数字与功能基本可信（selfcheck 38/38、ci_check 12/12、模板片 PIL 路线出片 PASS），
> 但有一类缺陷被系统性低估了：**门禁声称在查，实际一条都没判**。本版主要修这个。

### 修了什么（按严重度）

| # | 问题 | 后果 | 修法 |
|---|---|---|---|
| 1 | `--strict-stale` 完全不生效 | help 说「上游产物过时算失败」，实测退出码仍 0；CI 里把它当门禁用就等于没有门禁 | `_stage_begin` 接住 `stagewatch.report()` 的 errs 并当场失败；单 stage 路径统一走 `_finish_gates` 并按账本 fail 合成退出码 |
| 2 | `ci_check` 的 Markdown 围栏检查**恒过** | 源码里写的是 `startswith("\\`\\`\\`")`，Python 把反斜杠当普通字符保留，永远匹配不到真的围栏 → 计数恒为 0 → 检查永远通过 | 重写为围栏状态机（闭合须同字符、不短于开口）；由「未闭合围栏」负例验证会 FAIL |
| 3 | `ci_check` 的语法检查只查了半边 | `tools/` 下 4 个脚本从未被编译，于是 ci_check.py 自己的 `invalid escape sequence` 一路带到发布 | `_py_files()` 纳入 `tools/*.py`，并把 **SyntaxWarning 记 FAIL** |
| 4 | `--stage style` 连跑两次必报「产物不见了」 | `STAGE_IO[style].outputs` 把 tts 阶段写的 `voice_plan.json` 算成 style 的产物 → 假 stale；开了 `--strict-stale` 就成了硬失败，而 `--stage style` 正是 SKILL.md 第 3 步、全新项目的第一条生产命令 | `voice_plan.json` 归回 `tts.outputs`；`check()` 只对「上次记录到真实指纹、现在没了」报 output-missing（legacy 永久 missing 不再误报） |
| 5 | `--stage accept` 把 QC 报告写到另一处 | all/qa 用 `dirname(--out)`、accept 用 `dirname(work)/out`；`--out` 指到 `deliver/` 时签核更新的是一份没人会交付的文件，交付那份仍是「未签核」——正好打穿「报告是交付凭据」 | 新增 `_report_path(a)` 单点推导，accept 也走它；accept 时把同指纹的旧账本搬回内存（`_reload_ledger_from_disk`），报告不再退化成空矩阵 |
| 6 | 门禁账本为空时报告写「**通过**」 | `gates.json` 被删/写坏/根本没跑 qa 时，交付凭据会宣称通过 | 改为「**未判定**：这不等于通过——先跑 `--stage qa`」；`gates.py --json` 也改成读磁盘账本（原来现场 new 空账本，机读输出等于撒谎） |
| 7 | `--resume` 会复用被截断的分块 | Ctrl-C 留下的半截 mp4 只要超过 1024 字节就被当「已存在」跳过，拼接后 0.07s vs 预期 29.00s 才报错 | 分块先写 `_part_*` 再 `os.replace`；复用前用 ffprobe 头部 `nb_frames` 校验，不符则重渲 |
| 8 | 音画同步门禁会「零超差通过」却其实没在判 | `events` 里只要有一条缺 `t` 的条目，`judged` 就被算成 1，命名约定守卫也被骗过 | 只认带有限数值 `t` 的事件并计数丢弃；新增 `src` 来源过滤——**跨时间基准的 cue 不参与配对**（web 引擎下 film.json/scenes.py/页面三路 cue 的时间基准不同），改为「跨基准未对照 N 条」显式计数 |
| 9 | 插件入口 `index.js`：provider 抛错会让整行 reject 且零日志 | 与文件头「挂不上技能是降级，不是崩溃」直接矛盾 | `apply` 内 try/catch + 警告日志（stub provider 实测：修复前 REJECTED 且 logs 为空，修复后 RESOLVED 且有一句能照做的提示） |
| 10 | `wav_write` / `sfxkit.save` / `scorekit.save` 把 NaN/Inf 静默写进 WAV | NaN 经 `clip` 再 `astype(int16)` 变成 0，经 `rint` 再 `astype(int32)` 变成 INT_MIN（满幅负样本）——成片里一声爆响或随机哑音，且没有任何报错 | 三个写盘入口统一拒绝非有限值并报错 |
| 11 | 极短音频让混音直接崩 | `voice_env` / `duck` 的 `np.convolve(mode="same")` 在信号短于核时返回核长，再 `np.maximum` 广播失败；`duck_voice` 在「旁白比背景短」时同样崩 | 核长上限收敛到 `len(v)`；末尾不足一块向上取整并补零；包络短于背景时补零（旁白结束后背景原样恢复） |
| 12 | `AudioBus.place` 传负起点崩、`music_bed` 传 `seg=0` 死循环 | 负下标切片让长度变 0 后广播崩；`seg=0` 时 `t` 不前进，6 秒片要跑 2 分钟（靠 100000 次上限才停） | 负起点按「从 0 秒开始、裁掉前面 |i0| 个采样」处理；`seg<=0` 在入口拒绝 |
| 13 | `CueBus` 对音效参数错误静默降级 | 任何 `TypeError` 都退化成「只传 seed」，用户改了 cue 参数却听不出变化，日志一个字都没有 | 只对「配方不认识这个关键字」逐个删参重试，其它错误明确抛错 |
| 14 | `theme_overrides` 写 hex 颜色会在渲染期崩 | schema 与文档的示例都是 `"#ff8a3d"`，而 JSON 没有元组，照着写必然踩；旧实现把字符串原样塞进主题，首次 `int("#")` 就 ValueError | `apply_theme_overrides()` 统一接受 `[r,g,b]` / `#rrggbb` / `#rgb` 并做类型校验，错误提前到 `--stage style` |
| 15 | `--engine web` 的 DURATION 契约难以定位 | 报错只说「页面 DURATION 与 timeline 不一致」，不说 DURATION 写在哪（实际在 `assets/web/film.js`）；默认模板片 29s 而模板写死 12s，等于「拷模板 + --engine web」这条路 100% 走不通 | 报错带上入口 HTML + 同目录脚本清单 + 一条可照抄的 grep + 目标数值；模板改为**从场景表推出 DURATION**（`FK.timelineEnd()`），新增 `window.WINDOW_DURATION` 显式覆盖 |
| 16 | `page_meta` / `export_cues` 缺 `window.DURATION` 时报 float() 收到 NoneType | 看不出是「页面契约缺字段」 | 统一 `page_number()`：缺字段/非数字/非正数各有可操作报错 |
| 17 | `filmcheck` 的时长对账按单一语速 4.2 字/秒 | 模板片实测 5.2 字/秒的句子让估算偏短 5.2 s → 假 WARN「剧本与 timeline 差 5.23 s」；`pacing.lead` 也被当成「没写 lead」 | 改成语速区间 3.6–5.6（取 voicekit 校准与 stylekit 的 cps_band 交集）+ 按 `pacing > 顶层字段 > 默认` 的优先级读取 |
| 18 | `voicekit calibrate --only` 拼错声线名 | 静默变成空集合 → 打出「所有音色都合成失败（多半是没网）」，把配置错误伪装成网络错误 | 先点名校验，给出可照抄的可用清单，退出码 2 |
| 19 | `audio_similarity` 的相关系数对常量信号返回 0 | 「配乐渲染成静音」这种最严重回归反而判 PASS（0 <= 0.85）；D2「侧链让位」在侧链被删掉时也能过（guard 兜底被算作侧链效果） | 常量/静音信号返回 NaN 并在输出打印 n/a；测侧链时传 `guard_snr_db=0` 关掉兜底 |

| 20 | `--strict-stale` 把文档主推的**分步跑法**整个打死 | 修好 strict 之后暴露的：`stagewatch` 的 no-manifest 判据是「本 stage 的输入齐了」就报，而分步跑时这本来就是常态 → `--stage style` 之后再 `--stage tts --strict-stale` 必然 EXIT=1 | 判据改成「**本阶段自己的产物已经在 work/ 里**、而 manifest 没有本阶段记录」（产物在 = 有人跑过却没留痕）；同时把 `qa` 的 outputs 从 `gates.json` 换成 `qa/sheet.png`（gates.json 任何交付阶段都会写，不能当证据）；`--stage stills` 自动补跑 tts/layout 也补落 manifest |
| 21 | `engine=web` 的签核指纹漏了页面脚本 | v0.6 承诺「改了画面代码旧签核自动失效」，但 `content_hash` 只含剧本 + scenes.py + 引擎；web 引擎的画面逻辑在 `web/*.js`，改它旧签核仍然有效 | `content_hash(..., web_entry=)` 在 `engine=web` 时纳入入口页同目录的 `.js/.mjs/.html`（按名排序后逐个 sha1）；**pil 路线不参与**（实测 pil 哈希值与改前逐位一致，老项目签核不会无故失效） |
| 22 | `theme_overrides` 不参与主题体检 | `resolve_style` 体检的是**基础主题**，而 scenes 实际用 `plan[theme]`（基础 + overrides）→ 用 overrides 把 ink/bg 调成低对比度，体检照样 PASS，渲完才发现字幕看不清 | 改写为体检最终主题（实测：基础主题 lint 为空，最终主题报「ink 与背景对比度 1.09 < 7.0」） |
| 23 | `--engine web` 静默合并两套时间基准的 cue | `scenes.py` 的 `cues()` 按 PIL 时间轴生成，页面动画按页面时间轴；合并后音效可能响在错误的幕上 | 不静默：在 `st_cues` 里显式 WARN 并给出两条改法（走 PIL 路线，或把 cue 写进页面/film.json 并删掉钩子）。合并行为不变（它有文档依据） |
| 24 | `webengine` 的 ffmpeg stderr 管道死锁 | `stderr=PIPE` 却只在 `wait()` 之后才读，而 `stdin.write()` 是同步阻塞：编码器刷超 64 KB 就互等，整个渲染永久挂死 | stderr 落 `TemporaryFile()`（无缓冲上限），失败时读回错误文本 |
| 25 | `webengine snap --every` 传非正值会死循环 | `--every -1` 在开完浏览器后点数组无限追加（实测 40 s 不返回）；`--every 0` 被当成没写 | 必须为正数、`--times` 解析失败给中文错误，均退出码 2 |
| 26 | `qa_frames` 在 30 fps 成片上白跑一次全文件 `ffmpeg -i` | 兜底条件是 `fps == FPS_FALLBACK`，而 30 正是本项目最常见值 | 加 `fps_parsed` 标志，只在 ffprobe 没给全信息时回退（实测 `_probe` 子进程调用 2 → 1） |
| 27 | `qa_frames --expect-duration nan` 静默通过 | `abs(duration - nan) > 0.5` 恒为 False，传了预期时长但校验静默失效 | 非有限数字 → WARN「本次不做预期时长校验」 |
| 28 | `loudnorm` 拿 `-inf` 的测量值去编码 | 全静音/极短音频时 ffmpeg 给 `input_i=-inf`，塞进 `measured_*` 会被拒（`Value -inf ... out of range`），mux 直接失败 | 量不到有限测量值就返回 None（mux 打 WARN 跳过标准化，qa 的响度门禁照旧兜底） |
| 29 | 旁白录得太轻时，旁白优先/SNR 兜底**整体静默失效** | 活跃区为空 → `voice_snr` 返 NaN → 背景一点不压，日志也不说 | 检测到「有声音但到不了活跃区」时 WARN 并给出峰值 dBFS 与门限 |
| 30 | `estimate.py` 系统性偏乐观 15–20% | 29 s 片实测总 28.4 s，估算只给 17.5–24.8 s；分阶段错得更明显（qa 估 0.97 vs 实测 6.2–6.8，把 qa 算小 6 倍；并行按 jobs 线性折算，实测 2 进程只有 1.51×） | 用实测回填全部基准常量，新增 `PARALLEL_EFF` / `MUX_BASE_SEC` / `QA_BASE_SEC` / `CALIBRATION`（机型·日期·样本·逐 stage 实测值，人读输出与 `--json` 都带），无实测阶段给 ±30% 区间 |
| 31 | `score` 阶段对**任何** TypeError 都换签名重试 | scorekit 内部真报 TypeError 时，用户会看到「不接受任何一种已知签名」，照着这个方向白找；真实原因（内部参数为 None）被吞掉 | 只对 `unexpected keyword argument` 走签名兼容链；其它 TypeError 立刻报「内部报错」并带上本次签名 |
| 32 | `--stage all` 从不往 manifest 记 `style` | `_nm = "style" if stage == "style" else None`，而 `_stage_end(None)` 是空操作 → 跑完 all 之后 manifest 里**没有** style 这一行（实测两个独立目录都是 `tts,layout,cues,score,sfx,audio,frames,mux,qa`）；于是之后任何一次 `--stage style` 都会被判「产物在但没有记录」 | all 模式也记 style 的 manifest 行 |
| 33 | 分步跑 + `--strict-stale` 的两处误杀 | ① `--stage frames` 之后跑 `--stage mux --strict-stale` 必然 EXIT=1（mux 发现自己的输入 silent.mp4 变了，但这正是**本轮要重算的输入**）→「重跑上游再跑下游」这个标准动作在 strict 下永远走不通；② #32 带来的假 no-manifest | ① 把判定拆成三档：**本轮计划内**的输入变化打 `[待重算]`（不判红，前缀也换掉以免 CI 里 `grep '[stale]'` 误报）；**不在计划内、且产出它的上游阶段自己过硬问题**（递归一层、防环）仍硬报，并说明是哪个上游过时；其余保持原 `input-changed`。② 见 #32 |

### 顺手修掉的小项

- `scenes.py` 的 `_bg` 缓存键与 `--jobs` 子进程主题元组往返：补了「JSON 快照后重解风格」的注释与兜底。
- `voice_env` 的块数向上取整：极短音频的最后一段不再被丢掉。
- CI 注释里写错的 `29 条` 改正；`references/quality-gates.md` 的 `PASS 29` 改正；`references/web-engine.md` 的「24 项 + 一次真实浏览器」措辞歧义改正。
- `README.md` 的版本徽章 `0.6.0 > 0.7.2`；补 v0.7.0 / v0.7.1 版本史；补 `Node.js >= 20` 环境前提（与 `package.json engines` 对齐）。
- `docs/styles/demo-results.json`：README 的三行 demo 表按「v0.5 提交信息 + demo-results.json」两处独立证据校正为 88BPM/G、84BPM/F，并在表前注明**这是 v0.5 历史快照、片源脚本未随仓库分发、不可直接复现**（BPM/调性由内容指纹落桶，换一版正文就可能换桶）。
- `SKILL.md` 的 `<SKILL_DIR>/../../../tools/estimate.py` 是死路径（往上三级跑出包外），改为 `<REPO>/tools/estimate.py` 并新增 `<REPO>` 定义：`tools/` 与 `schemas/` 在包根、不在技能目录，quickstart 拷出来的工作目录也没有它们。
- `SKILL.md §13 升级须知` 补上 v0.5 → v0.7.2 的迁移动作（原来只写到 v0.4）。
- 文档里写了一个不存在的参数 `render.py --stage explain --json`（实测 unrecognized arguments），机读路径改为 `stagewatch.py --work work --json`。
- `package.json files` 补 `tools/selftest_noscipy.py`（CI 在用，用户复现「无 scipy 兜底」也需要它）。

### 自检：38 → 43 条

新增 4 条，每条都做过**可证伪性验证**（把对应修复回退 → 断言必须 FAIL）：

| # | 断言 | 回退后是否 FAIL |
|---|---|---|
| 39 | 音频边界：极短旁白不崩 / NaN·Inf 拒绝写盘 / 负起点与 seg=0 有守卫 | 是（移除 NaN 守卫 → FAIL） |
| 40 | QC 报告落点只有一个来源（--out 的目录） | 是（重新引入第二处拼接 → FAIL） |
| 41 | 空门禁账本不许写成「通过」 | 是（把 `if not items` 改回 false 分支 → FAIL） |
| 42 | 单阶段产物归属：不许把别的 stage 写的文件算成自己的（假 stale） | 是（恢复旧 STAGE_IO → FAIL） |
| 43 | `theme_overrides` 必须参与主题体检（否则能用 overrides 造出低对比度成片） | 是（改回只体检基础主题 → FAIL） |

另外把 `run()` 的 SKIP 判定从「精确等于 skip」改成「以 skip 开头」——
带理由的降级（例如 `skip（缺 render.py）`）以前会被记成 **PASS**，而这正是本版最想消灭的「skipped 冒充通过」。

### 实测（本机 Windows 11 / Python 3.12 / ffmpeg 9.0.2）

- `selfcheck`：**PASS 43 / FAIL 0 / SKIP 0**，7–11 s（跑 5 次，7.0–10.6 s）。
- `ci_check`：**PASS 12 / FAIL 0**。
- `sfxkit --selftest` 16/16；`scorekit --selftest` 21/21；`qa_frames --selftest` 22/22；`webengine --selftest` 24/24（含真实无头浏览器与跨会话字节一致）。
- 模板片 PIL 路线：`--stage all` EXIT=0，29.00 s / 1920x1080 / 30 fps / -16.5 LUFS / 真峰 -1.4 dBTP，门禁 pass 7 / fail 0 / skipped 1（skipped 是人工签核）。
- 中文路径 + 中文成片名（`中文工作区/成品/片子.mp4`）：EXIT=0，报告落在同一个交付目录里。
- `--jobs 2` 分块并行：2 段 29.00 s 成片；删掉一段后 `--resume` 只补那一段。
- `--engine web`：`WINDOW_DURATION` 覆盖到 29.011 后能出片（72 s 渲染）。
- `tools/audio_similarity.py --strict`：A/B/C/D 四组全 PASS（D 组 SNR 12.9 dB、侧链让位 6.0 dB）。

### 已知限制（本版没做，写下来别装看不见）

- `--engine web` 与仓库自带的 `scenes.py` 混用：`scenes.py` 的 `cues()` 按 PIL 时间轴发 cue，而页面按自己的时间轴发事件。
  本版不再把这种跨基准配对算成「同步超差」（会显式报「跨基准未对照 N 条」），但**默认模板片仍不能直接用 --engine web 出片**——
  `assets/web/film.js` 是 12 秒 3 幕的独立演示页，出长片必须自己重写场景。这条是设计边界，不是 bug，但之前文档没写清楚。
- 跨平台换行：仓库没有 `.gitattributes`，`core.autocrlf` 在不同机器上不一样，而 `gates.content_hash()` 与
  `stagewatch._fp()` 都按字节算 SHA-1 → 同一份剧本在 LF/CRLF 两种 checkout 上内容指纹不同（旧签核会「内容已变」、`--stage explain` 会假 stale）。
  修法很简单（`* text=auto eol=lf` + `*.png binary`），但它会让**所有已存在的内容指纹一次性变化**，必须单独提交并告知用户重签，所以不在本版混进来。
- `estimate.py` 的耗时模型仍是经验公式：核心常量来自 29.011 s 模板片 + 58 s 变体、`--jobs 1/2`、PIL 路线；  web 引擎与 `--jobs 8–12` 的并行效率仍是外推（`PARALLEL_EFF` 用线性模型），389 s 只有外推锚点。`--json` 里带 `calibration` 字段说明基准来源，换机请重测后回填。- `stagewatch` 对 >8 MB 的文件走 `size+mtime(秒)` 快路径：同一秒内、同尺寸的外部修改会漏检（模块 docstring 早已写明这个取舍，`audio.wav` / `silent.mp4` 恰在这个区间）。- `manifest.json` 没有 stage 级「完成」标记：跑到一半被 kill 与从未跑过在时间轴上无法区分（靠 `output-missing` 与 `_part_` 半成品前缀兜底）。- 被**外部工具**截断的分块，头部 `nb_frames` 仍写着完整值，只有 `-count_frames` 能查出来（实测 390 帧的块要 3.5 s，长片几十块会让每次 `--resume` 多几分钟）；这类残块仍靠拼接后时长校验兜底。- `gates.content_hash` 的 web 资产指纹只看入口页**同目录**的 `.js/.mjs/.html`，不解析 HTML 里的 `<script src>`、不递归子目录。- `qa_frames` 的 `loudness_curve` / `sample_frames` 会把整段音频/整片帧读进内存（`sample_frames` 的 docstring 已警告 389 s ≈ 670 MB，并给了流式替代路径）。- 单跑 `--stage style` 也会写 `work/gates.json`（它在交付阶段清单里），共享同一个 `--work` 的协作场景要留意。- `--stage explain` 是只读查询，不写账本——所以 `--strict-stale --stage explain` 只改退出码，不在 QC 报告留痕。- `filmcheck` 没有 `--work` 参数：`--work` 指到非常规位置时会静默跳过时长对账（不报错）。
- `--stage all` 时 10 个 stage 全在「本轮计划」里，所以 all 内部「本阶段输入变了」一律是 `[待重算]` 提示（这正是 all 的语义：它本来就会把每一步重算一遍）。想看硬 stale 请用单 stage 跑或 `--stage explain`。
- `stagewatch.py` 单独跑时拿不到「本轮计划」，`pending` 默认为空 —— `python stagewatch.py --json` 会把 all 模式下的「待重算」如实报成 `input-changed`（`kind` 不同、不判红）。CI 若直接消费该 JSON，要留意这个差异。

### 升级须知（v0.7.1 → v0.7.2）

1. **QC 报告的位置变了**：`QC-REPORT.md` 现在只落在 `--out` 的目录下（默认还是 `out/`，行为不变；`--out deliver/film.mp4` 时落在 `deliver/QC-REPORT.md`）。以前 accept 会额外往 work 的兄弟目录 `out/` 写一份，那一份不再产生。
2. **`--strict-stale` 现在真的会让命令失败**（退出码 1）。如果你在脚本里带着它跑、且习惯忽略退出码，会开始看到中断。这是修正，不是回归；要放行请显式加 `--allow-stale`（会记进 QC 报告）。
3. **`--engine web` 需要对齐页面时长**：模板页改成从场景表推导 DURATION，要出不同于场景表总长的片子，请在 `index.html` 里写 `window.WINDOW_DURATION = 秒数`。
4. **`theme_overrides` 的颜色可以写 hex 了**（`"#ff8a3d"`），也可以继续写 `[255,138,61]`；类型不对会在 `--stage style` 阶段就报错，而不是渲染到一半崩。
5. 自检条数 38 → **43**；如果你有脚本 grep 自检输出的「PASS 38」，需要跟着改。
6. **`--strict-gates` 在单 stage 上也生效了**：以前只有 `all`/`qa` 这类收尾路径会按账本判失败，现在任何 stage 收尾都统一判定。所以 `--stage layout --strict-gates` 如果账本里有 skipped 条目，会返回非 0（这是「同一套语义」的必然结果，如果你依赖旧行为需要显式去掉这个参数）。
7. 分步跑（`--stage style` → `tts` → `layout` …）现在可以安全地带 `--strict-stale`：旧版会把「输入齐了」误判成「交接了别人的 work 目录」。
## 0.7.1 — 旁白优先：让观众听清字，BGM 与音效退回"可选层"

> 用户的要求很直接：「视频以旁白为主，BGM 和其它音效可有可无，至少不能影响旁白的清晰度」。
> 0.7.0 解决的是"声音都一样"，这一版解决的是"声音不能抢戏"。

### 修了什么

| 项 | 0.7.0 及以前 | 0.7.1 |
|---|---|---|
| 配乐基线 | `music.gain_db=-1.5`：背景只比台词低 1.5 dB | **-12**：人声明显压住背景 |
| 旁白侧链 | 无 | 有人声处再压配乐 **6 dB**、音效 **3 dB**；间隙自动恢复（包络 10 ms 块，起音快释放慢） |
| 旁白自身 | `voice_gain=1.0` | 不变，且**不被任何侧链影响**（旁白优先 ≠ 压人声） |
| **SNR 兜底** | 无 | 混音后实测人声活跃区的人声/背景差，**不足 12 dB 自动补压背景**（最多 30 dB，只动背景） |
| 失败模式 | "代码不测量也不保证"，靠回听 | selfcheck 35/36/37/38 + `audio_similarity.py --strict` D 组硬断言 |

- 新 API：`filmkit.voice_env()`（台词活跃度包络）、`filmkit.duck_voice()`（旁白侧链压缩）、
  `filmkit.voice_snr()`（人声活跃区 SNR）、`filmkit.guard_snr()`（不足就补压的判定）。
- `mix_film` 新增参数：`voice_duck_db=6.0` / `voice_duck_sfx_db=3.0` / `voice_thresh_db=-42` / `voice_floor_db=-60`。
- `film.json` 的 `music` 新增上述四个键（显式 0 = 关闭），模板已写默认值。
- 模板 `film.json` 的 `music.gain_db` 从 `-1.5` 改为 `-12`，`duck_db` 3 → 4。

### 实测

- **真实素材对照**（edge-tts 真旁白 + scorekit 真配乐，22 s）：
  旧混音（配乐 -1.5、无侧链、无兜底）**人声活跃区 SNR = -4.2 dB**——背景比台词还响，正是"听不清"的根源；
  新混音（-12 + 侧链 6 dB + 12 dB 兜底）**SNR = 12.0 dB**。
- `--strict` D 组（真实配乐素材）：**12.9 dB**，旁白处背景额外让位 6.0 dB，间隙背景回到基线。
- `selfcheck` **34 → 38**：新增「35 旁白优先（让位与恢复）」「36 旁白优先默认值（配乐 -12 + 侧链）」
  「37 旁白清晰度 SNR 兜底」「38 无旁白时混音不受影响」。
- 同时修了 selfcheck 第 24 条：原来断言"外部配乐峰值 > 0.3"（绝对阈值），配乐基线降到 -12 后误报；
  改为"比 off 时明显变大"的相对断言——**绝对阈值会被调参牵着走**，这是本版顺手记下的教训。

### 已知限制

- 侧链是"按人声包络整体压背景"，不是频谱避让。旁白与配乐在 1–3 kHz 重叠时，仍靠 12 dB 电平差与人声专注度；
  想更干净可给配乐加一段 1–3 kHz 的 EQ 挖空（本版没做，属于"可选增强"）。
- `voice_duck_db` 压太深会让音乐在每句之间"一抽一抽"；默认 6 dB 是听感与安全的折中，可按片子调 4–8。
- SNR 兜底是**按整片统一补压背景**，不是逐句动态：如果某一句的背景特别响、别处正常，兜底会整体压一次；
  逐句动态压需要更细的包络（本版没做）。补压超过 18 dB 会打印提示，通常意味着该调低 `music.gain_db`。

## 0.7.0 — 声音也要"一片一样"：配乐变奏、音效内容化、片内分离

> 起因：v0.5 解决了"每部片的风格都差不多"，但同一风格下的**配乐仍像同一首**、
> **音效逐样本相同**、**片内配乐和音效像同一套**。用户的原话是
> 「生成的视频为啥背景音乐和音效都差不多」。
> 本版不动画风与旁白，只把音频三件事做成"随内容变化、片内可分"。

### 实测证据（改造前，`tools/audio_similarity.py`）

| 对照 | corr / 结果 | 含义 |
|---|---|---|
| 同风格·同 bpm·同调，只换内容指纹 | **0.966** | 指纹只改 seed，和弦/动机/鼓型不变 → 听感同一首 |
| 换风格 | 0.534 | 差异几乎全由风格承担 |
| 同 cue 序列跨片渲染 | **逐样本相同** | 音效 seed = `seed_base + i`，与内容无关 |
| 提示音音高 | 硬编码 D 小调五声 | 配乐换到 C/G/Am 后提示音"跑调"或同调过强 |
| duck 默认 | 3.0 dB（2.0–4.5 分风格） | 段落过门时配乐与音效几乎等高 |

### 修法（三路并行，契约见 `references/audio-variation.md`）

| 层 | 改法 |
|---|---|
| 配乐和声 | 内容指纹参与**每两小节一次的稳定和弦替换**（不干扰乐句末/片尾规则） |
| 配乐进行 | 指纹在风格自带的 calm/body/drive 三套进行里选一档（同风格素材，只换功能顺序） |
| 配乐动机 | 指纹对级数做 **±1 稳定偏移**（节奏不变，问句/答句独立派生；跨主音带八度修正） |
| 配乐鼓型 | 指纹加 ghost kick + clap ±0.5 拍 |
| 出片接线 | `render.py` 的 `st_score` 把 `fingerprint.full` 传进 `scorekit.render()`（legacy 不传，四级 TypeError 回退保旧 scorekit 可用） |
| 音效 seed | `content_seed(base, style, content_fp, recipe, t)`（filmkit / sfxkit 两侧逐字同公式） |
| 提示音调性 | `in_key(freq, key)`：key=None 保持 v0.6 逐样本行为；给调名则跟随配乐 |
| 提示点生成 | `assets/scenes.py` 的 `cues()` 按 kind 出不同音效设计（hook/body/quote/outro），条目音高由文本派生 |
| 模板 cue | `assets/film.json` 去固定化 + `sfx.content_seed` 开关 |
| 段落边界 | 边界前 2.2 s 内的 whoosh/riser 自动 -3 dB（不撞配乐过门） |
| duck | 按风格加深（mono 3.0 / lo-fi·magazine 3.5 / 默认 4.0 / cinema·epic 5.5） |

### 验收（改造后，`python tools/audio_similarity.py --strict`）

| 指标 | 改前 | 目标 | 实测 |
|---|---|---|---|
| A 同风格换指纹 corr | 0.966 | ≤ 0.85 | **0.645** |
| A 换风格 corr | 0.534 | ≤ 0.52 | **0.511** |
| B legacy 逐样本相同 | True | True（必须） | True |
| B 不同内容指纹 | 逐样本相同 | 必须可区分 | 已不同（逐样本 False） |
| B 同内容指纹可复现 | True | True | True |
| C duck 默认 | 3.0 | ≥ 3.5 | 4.0 |

**验收命令与结论**（本机实测，2026-11-09）：

```bash
python tools/audio_similarity.py --strict   # STRICT PASS（A/B/C 三组）
python skills/video-studio/scripts/selfcheck.py    # PASS 34 / FAIL 0 / SKIP 0
python skills/video-studio/scripts/scorekit.py --selftest   # 21/21 PASS
python skills/video-studio/scripts/sfxkit.py --selftest     # 16/16 PASS
```

配乐变奏的实测 `variation`（20 s / tech-deep / 110 BPM / content-a）：`{"chord_swaps": 4,
"motif_shifts": 4, "drum_ghosts": 6}`；同一剧本换 content-b 得到 `{"chord_swaps": 3,
"motif_shifts": 4, "drum_ghosts": 7}` —— 三处结构变奏都在随内容变化，不是只改了 seed。
真实出片链路（`--stage score`）里 `work/score.json` 也能看到这个字段，可直接审计。

### 门禁与回归

- `selfcheck` 32 → 34：新增「33 音效跨片可区分（内容派生 seed + legacy 逐样本兼容）」
  「34 配乐/音效片内分离（duck 加深）」。
- `tools/audio_similarity.py` 新增 `--strict`：把 A/B/C 三组阈值变成可执行的验收，失败 exit 1。
- legacy（不写 `style` 字段 / `sfx.content_seed: false`）逐样本不变：selfcheck 20/26/33 三条守着。

### 已知限制

- **同风格不同指纹有约 1/3 概率落到同一和弦进行档位**：此时两段配乐 corr≈0.93（仍有七和弦色彩与鼓型 ghost 的差别，但听感更接近）。想再降需要"每段独立选档"，会削弱片内连贯性，本轮不做。固定验收的 content-a/content-b 落在不同档。
- `fixture` 级别的验收脚本 `tools/audio_similarity.py` 用固定指纹串（content-a/b），真实剧本走的是 sha1 指纹，分布一致但不是同一组样本。
- 音效"跨片可区分"在**同一组 cue 序列**上只保证逐样本不同（corr 仍可能 0.9 量级，因为 cue 时间点相同）；真正的差异来自 `scenes.py` 的 `cues()` 按 kind 与文本生成不同序列。

### 升级须知（v0.6 → v0.7）

1. 老项目照旧能跑：不写新字段 = v0.6 行为（配乐逐样本一致、音效 seed 位置派生）。
2. 想让配乐/音效随内容变化：保持 `style: auto`（已是默认），**不要**写死 `music.seed`/关掉 `seed_from_content`。
3. 想让提示点更丰富：把音效写进 `scenes.py` 的 `cues()`（模板已按 kind 生成），`film.json` 的 `sfx.cues` 只留少量固定重击。
4. 改音频代码后跑 `python tools/audio_similarity.py --strict`（约 1–2 分钟），它比"听起来差不多"可靠。

## 0.6.0 — 交付可信化：让"我查过了 / 我签过了 / 改一句要重跑什么"都能拿证据

> 这一版不加生成能力，加的是**可追责性**。起因是一次对照：把本项目与另一个成熟插件
> （本地 ComfyUI 短剧工作室）逐项对比，发现对方的执行层本机完全跑不动，
> 但它的**编排层**——"人怎么被叫住""改一句要重跑什么""这笔账记在哪"——写得极细，
> 而这三件事在本项目里全是散文承诺。
> 对照后确认了五处"文档承诺了、代码没做"，本版全部补上并补了回归断言。

### 修掉的五处「承诺 vs 实际」

| 现象 | 根因 | 修法 |
|---|---|---|
| `out/QC-REPORT.md` 在 G6 交付清单里被点名，**全仓 grep 只有那一处命中，没有任何代码写它** | 质检结论只活在 stdout | 新增 `gates.py` 门禁账本 + 报告渲染，`--stage qa` / `--stage all` 收尾自动写 |
| "先确认风格""亲眼看 3 张静帧"全是散文，**换会话就无法证明看过哪一版** | 没有签核账本 | `--stage accept --gate style\|stills\|facts\|release` 写 `work/signoff.json`，绑内容指纹 |
| 除了 `frames` 的分块指纹，**其余 stage 全裸奔**：改 `style` 后 audio 仍读旧 `music.wav`、改 `hold` 后 frames 仍用旧 `cues.json`，且都不报错 | 只修过一个点 | 新增 `stagewatch.py`：每 stage 记输入/输出指纹，`--stage explain` 报过时 + 给最小重跑命令 |
| `SUB_MAX_CHARS = 24` 定义后**零引用**（SKILL `4 自己承认"别指望它拦你"） | 常量接了没人调 | 由 `filmcheck` 与报告矩阵接管超长字幕/契约类判据（见下） |
| `film.json` 未知键**静默忽略**：`hold→holds` / `kind` 拼错 / `cues[].at→time` 全都"跑得通但效果没了" | 只有 Python traceback 兜底 | 新增 `schemas/film.schema.json` + `filmcheck.py`（未知键给"是不是想写 X"），接进 `ci_check` 第 12 项 |

### 修掉一个**已发布版本里的功能性 bug**

`--jobs >1` 的并行渲染**一直是崩的**——而 SKILL/README 都在教用户用它。

- **形状**：worker 从 `work/_script_snapshot.json` 读剧本，JSON 把主题色的 `(r,g,b)` 元组变成 `list`；
  `scenes._bg()` 拿 `th["bg"]` 当缓存键 → `TypeError: unhashable type: "list"`。
- **为什么一直没被发现**：默认 `--jobs 1` 在父进程内渲染，不经过快照反序列化，所以单进程永远正常。
- **修法**（根因 + 防御）：worker 反序列化后**按剧本重解风格**（纯函数，拿回元组，也保证父子主题逐字段一致）；
  `scenes.py` 的 `_bg` 缓存键加 `_hashable()` 冻结兜底（它是用户会拷走改的模板）。
- **回归**：`selfcheck` 第 32 条断言"JSON 往返后重解必须回到 tuple"，并验 `_hashable` 可哈希。

### 新增

- **`scripts/gates.py`（约 440 行，纯标准库）**：`GateLedger`（pass / fail / skipped 三档）、
  `out/QC-REPORT.md` 渲染（gate matrix + 实测值 + 签核 + 耗时台账 + 结论话术）、
  `signoff` 读写与内容指纹校验、`append_cost` / `read_cost` 耗时台账、`atomic_write_json`。
- **`scripts/stagewatch.py`（约 260 行）**：`STAGE_IO` 依赖表、指纹（小文件 sha1 / >8 MB 走 size+mtime）、
  快照、`check` / `explain` / `report`。
- **`scripts/filmcheck.py`（656 行）** + **`schemas/film.schema.json`（528 行）**：draft-07 子集手写校验，
  未知键 WARN 带拼写建议、枚举/范围 ERROR、跨字段（cue 引用的 `l0x` 必须存在、hold+语音预估与 timeline 对账）。
  `style` 枚举从 `stylekit.STYLE_IDS` **单向读取，不复制**；stylekit 缺失时降级 WARN 不报错。
- **`tools/estimate.py`（578 行，纯标准库）**：开渲前算帧数 / 各阶段耗时 / 磁盘峰值 / 分块建议，
  `--json` 可机读。基准常量集中在文件顶部，换机请重测回填。
- **`references/change-impact.md`**：改了 `text` / `sub` / `hold` / `kind` / `style` / `scenes.py` / `crf` / `engine`
  分别失效什么、最小重跑什么、成本量级，外加三个真实犯过的错误做法。

### 命令面变化（全部向后兼容，老项目不用改）

| 新增 | 作用 |
|---|---|
| `--stage accept --gate <闸> [--note] [--by]` | 人工签核一道闸（`style / stills / facts / release`） |
| `--stage explain [--json]` | 哪些阶段过时了、为什么、最小重跑命令 |
| `--strict-gates` | 把 `skipped` 升级为失败（对齐 `QA_REQUIRE_SYNC` 语义） |
| `--strict-stale` / `--allow-stale` | 阶段过时：失败 / 显式放行（放行会留痕） |
| `--no-gate` | 跳过签核检查（**会在 QC 报告里留痕，不算通过**） |
| `--cost` | 每个 stage 往 `work/cost.jsonl` 记一行实测墙钟 |

### 门禁与回归

- **selfcheck 29 → 32**：新增「30 门禁账本与签核」「31 阶段新鲜度守卫」「32 并行渲染主题序列化」。
- **三个变异测试确认断言真的会失败**（不是橡皮图章）：
  把 `skipped` 当通过 → 第 30 条红；签核不绑指纹 → 第 30 条红；`stagewatch` 永不报过时 → 第 31 条红。
- **ci_check 11 → 12**：新增「film.json 契约」，直接调 `filmcheck.py`（校验规则只留一份，不在 CI 里重写）。
- 依赖白名单补 `gates / stagewatch / filmcheck`（同目录本地模块，不是三方依赖）。

### 实测（本机，2026-11-09）

- 模板片全链 `--stage all --jobs 2 --cost`：**门禁 pass 7 / fail 0 / skipped 1**（skipped = 人工签核未做），
  `work/manifest.json` 记录 9 个阶段，`--stage explain` 干净，`out/QC-REPORT.md` 正常生成。
- `--jobs 2` 修复前后：修前 `TypeError: unhashable type: "list"` 直接崩；修后 870 帧两段并行正常出片。
- `python selfcheck.py` → PASS 32 / FAIL 0 / SKIP 0；`python tools/ci_check.py` → PASS 12 / FAIL 0。

### 已知限制

1. `estimate.py` 是**预演**不是实测：没有 `work/timeline.json` 时按 4.2 字/秒粗估（±10–20%），
   `layout/cues/audio/sfx` 四行是经验模型；`--jobs` 按 12 核封顶线性折算。
2. `stagewatch` 的大文件（>8 MB）走 size+mtime 快路径，**内容变了但大小与 mtime 都没变**的情况查不出
   （代价考量：1080p 的 `audio.wav` 每 stage 算 sha1 会让 QA 慢一倍）。
3. `filmcheck` 的 cue `type` 不校验 sfxkit 配方名（配方表会演进，硬编码会误杀），
   timeline 对账是启发式，只 WARN。
4. 签核的证据检查是**"产物存在"级别**（如 `stills` 要求 `work/qa/sheet.png`），
   无法证明人真的看了——本会话拿不到会话记录钩子，退化方案到此为止。
5. `ffmpeg -version` 只能证明"能跑"，不能证明有 `xfade` / `drawtext`；
   缓解是把来源写进 `ffmpeg_source()` 并在探测顺序里把便携/WinGet 排到 PATH 之前。

### 升级须知（v0.5 → v0.6）

1. **必需**多拷两个文件：`scripts/gates.py`、`scripts/stagewatch.py`——
   `render.py` 直接 import 它们，少一个就是 `ModuleNotFoundError`。
2. 老项目照旧能跑：不写新字段、不加新开关 = v0.5 行为。首次跑会多出
   `work/manifest.json` / `work/gates.json` / `out/QC-REPORT.md`，都是新增文件。
3. `--stage qa` 的退出码语义不变（仍按 error 判定）；只有显式加 `--strict-gates` 才会因 `skipped` 失败。
4. 想用长片并行：现在可以真的用 `--stage frames --jobs 4` 了（修之前会崩）。

## 0.5.1 — 修 CI：selfcheck 第 29 条把"系统装了几族字体"当成产品缺陷

> 症状：`skill selfcheck (Linux)` 红，Windows 的 repo check 绿。
> 根因：新增的 case 29 断言"四个字体族量出来的宽度必须不同"，但 GitHub 的 ubuntu runner
> 只装 `fonts-noto-cjk`，没有雅黑/宋体/楷体 —— 四个族全部回退到同一个 `NotoSansCJK-Bold.ttc`，
> 宽度当然一样。**断言写死了本机的字体环境**，产品代码本身没问题。

### 修法

把 case 29 拆成两层，各验各的：

| 层 | 验什么 | 依赖环境吗 |
|---|---|---|
| 机制（必过） | 字体缓存键必须按 family 分键（否则 wrap 与绘制会用不同字体，文字溢出安全区） | 不依赖 |
| 语义（有条件） | 仅当系统**真的解析出 ≥2 个字体文件**时，才要求"宽度必须不同" | 依赖 |

单族系统（CI 的 fonts-noto-cjk、极简 Linux 容器）会打印
`4 个字体族 / 单族回退（NotoSansCJK-Bold.ttc）/ 只验缓存分键`，**跳过宽度比较而不是判失败**。

### 验证

- 在本机把 `os.path.exists` 换成"Linux 视角"（Windows 字体 -> False，Noto -> True）复现：
  修前 `FAIL 29`，修后 `29/29 PASS`；本机原生环境同样 `29/29 PASS`。
- **变异测试**（防止新断言变成"永远为真"）：故意把 `font()` 的缓存键改成 `(size, weight)`
  （丢掉 family），case 29 必须失败 —— 实测报 `字体缓存键没带 family（换族会串味）`，说明门禁仍在。
- CI 后续 4 个 selftest（sfxkit 16、scorekit 21、qa_frames 22、无 scipy 兜底）本地逐个复跑全绿。

## 0.5.0 — 风格包：让每部片的画风 / 配乐 / 旁白都不一样

> 这一版解决的是**同质化**，不是加功能。用户的原话：
> 「背景音乐风格都差不多、旁白声音都差不多、整体画风也差不多，希望随内容变化，生动活泼不死板。」
>
> 根因不是"参数不够多"，而是**身份参数全是模块级常量**：画风锁死在 scenes.py 的 THEME、
> 配乐锁死在 scorekit 的 Dm/120、旁白锁死在 render.py 的单个 voice，而 SKILL.md 教的流程
> 是"拷模板 -> 只改 lines[].text" —— 内容换了，气质不可能换。

### 新增：风格包（style packs）

| 层 | 之前 | 现在 |
|---|---|---|
| 画风 | 深蓝底 + 橙，3 个写死的绘制函数 | 8 套主题（8 组配色/材质/字体族/版式/HUD）+ 3 种版式骨架 |
| 配乐 | Dm / 120BPM / 3 套进行 / 固定动机 | 8 套配乐风格（调池/BPM 梯度/进行/动机/配器/鼓型/空间）+ 大调与七和弦 |
| 旁白 | 全片一个 voice/rate/pitch | 9 个 persona × 6 档 tone，按 kind 与文本规则自动切换 |
| 节奏 | 全局 lead/tail/min_dur | 每个风格一套 pacing，逐句可覆盖 |

- **一个 style id 同时决定三者**（不允许各配各的，否则会"极简黑白画面 + 史诗管弦"）。
  内置 `tech-deep / magazine / paper / vivid / mono / cinema / lo-fi / epic`。
- `"style": "auto"` 按内容自动选：关键词命中即锁定，零命中按指纹稳定落桶。
- **三级内容指纹**（full/tempo/style）：改一个错别字不会换整首曲子，换一部片三个都变。
- 片内也有变化：版式按章节轮换、镜头缓推、入场位移、配器渐入渐出、主奏轮换、语气档切换。

### 新增文件

- `scripts/stylekit.py`（909 行，纯标准库）：8 套主题 + 8 套风格包 + 内容指纹 + 对比度门禁 + CLI。
- `scripts/voicekit.py`（872 行）：9 个 persona + 6 档 tone + 逐句 voice_plan + 实测响度校准。
- `references/style-packs.md`：字段表、指纹规则、加第 9 套风格、常见误用。

### 修掉的两个静默 bug

| 现象 | 根因 | 修法 |
|---|---|---|
| 把 `trim_silence` 改成 false，旁白仍是被裁过的 | 缓存 key `sha1(text|voice|rate|pitch)` 不含 trim，旧 meta 的 trimmed=True 仍命中 | 升 v2 key（含 volume/trim/trim_db）；v1 key 保留给老缓存 |
| `voice_gain` 一调就偏（1.2 变成 1.44） | 在 `_voice_track` 和 `mix_film` 里**各乘一次** | 全局增益只在 mix_film 乘一次；逐句 gain 只在 voice bus 乘一次 |

### 门禁与回归

- **新增对比度门禁**：`stylekit.theme_lint()` 在 `--stage style` 就拦
  （ink≥7 / muted≥4.5 / dim≥3 / accent≥4.5 / bg-bg2 亮度差≥0.002）。
  8 套主题全部实测通过；v0.4 全仓 `grep 对比度` 是 0 处命中。
- **selfcheck 24 → 29 条**：新增风格层一致性、legacy 精确兼容、指纹确定性、声线计划、
  字体族与主题字幕 5 条。
- **legacy 逐字节兼容**（实测，不是承诺）：`scorekit.render(style=None)` 的 PCM sha256
  与 v0.4 完全一致（两个用例：12s 无锚点 + 10s 带锚点）；`assets/scenes.py` 的 hook/body/outro
  三帧 sha256 与 v0.4 一致；没问题就是没问题，不是"应该一样"。
- **性能**：styled 配乐比 legacy 慢 2–3 倍（24s 约 5.2s、60s 约 15–25s），仍在原预算内。

### 升级须知（v0.4 -> v0.5）

1. **老项目不用改**：film.json 里没有 `style` 字段就自动走 legacy，配乐逐样本一致。
2. 想用新特性：加 `"style": "auto"`（或一个风格 id）；模板 film.json 已经是 auto。
3. 新工作目录要多拷两个文件：`scripts/stylekit.py`（必需）、`scripts/voicekit.py`（可选）。
4. `--stage` 多了一个 `style`，且 `all` 的顺序变成 `style -> tts -> ...`（style 必须先跑，它决定声线）。
5. 改过 `trim_silence`/`voice_gain` 的项目，第一次跑会重新合成 TTS（key 变了），属预期。

## 0.4.0

> 这一版不是加功能，而是**修"门禁看着干净、其实没在查"**。上一版（0.3.0）把四条质检腿都补齐了，
> 但复盘时发现其中三处在**默认配置下根本不会失败**——这比没有门禁更危险。
> 同时把仓库从"一个人本机能跑"提升到"任何人都能验"：仓库级体检、CI、依赖清单、打包卫生。

### 修掉的静默失效（每一条都先复现、再修、再写回归断言）

| 现象 | 根因 | 修法 |
|---|---|---|
| 改完台词 `--resume` 拼出的片子**画面和旁白对不上**，且时长门禁照样通过 | 分块只按"文件存在"skip，不校验时间轴/场景/编码参数是否变过 | `work/segments/_plan.json` 存计划指纹（timeline+scenes.py sha1+剧本 sha1+crf/preset/chunk）；指纹不符时**拒绝拼接**并提示删目录。实测旧代码在 t=10 s 处有 27305 个像素差异却 EXIT=0 |
| 把 `music.mode` 改成 `off`，**旧配乐还在响** | `st_audio` 无条件读 `work/music.wav`；`st_score` 甚至打印"沿用已有 music.wav" | 新增 `K.music_plan()` 统一判定 8 种来源（score/file/bed/off/none/missing-file/missing-bpm/unknown-mode），score 与 audio 共用；off 时既不读也不删 |
| PIL 路线 `sync: []` 看着"零音画差"，其实**一条都没在查** | 同步门禁依赖页面导出的 `events`，PIL 路线没有 | cue 支持 `visual_at` 锚点自校验 + `QA_REQUIRE_SYNC=1` 把"没查"升级为 error + `sync_info.judged` 暴露查了几条 |
| 自带模板片报 4 段"亮度长时间无变化"，画面其实一直在动 | 判据用全帧平均亮度，对局部动画天生不敏感 | 换成"最活跃 8×8 块在 0.5 s 内的漂移"，阈值 1.5 级灰度；模板片 4 段 → 0 段，合成"真冻结 6 s"仍能报出 |
| Windows 管道里 `render.py \| tee` 的中文全是乱码 | stdout 在非 tty 时是 GBK，且 render/selfcheck 没有像其它模块那样切 UTF-8 | `_setup_stdio()`：仅非 tty 时 reconfigure 到 UTF-8，真控制台保持系统编码 |
| 跑一次 `--stage qa` 就在仓库里留一个 250 KB 的 `_dec_tmp.png` | 锐度比的解码帧写到了 cwd | 写进系统临时目录 |
| 锐度比偶尔把"不同时刻的参考帧"拿来比 | 部分参考帧渲染失败时仍用 `refs[0]` | 参考帧按时刻配对，并补 `_total` 与 frames 阶段一致 |

### 新增的"会失败"（不是新玩具）

- **浏览器确定性开关**：`--deterministic` / `WEBENGINE_DETERMINISTIC=1`。实测默认参数下
  **同一会话内**字节一致、**跨会话**有 ≤4 像素 ±1 级抗锯齿差异；开关打开后 8/8 会话字节一致，
  代价约 +22%/帧。文档相应从"同帧两次截图 byte 完全一致"改成带条件的说法。
- **`open_page` 契约检查**：缺 `window.ready` / `renderFrame` 时不再是裸 Playwright 超时，
  而是带上页面异常、控制台错误与契约快照的可操作报错。
- **`qa_text` 契约修正**：`frames` 字段改为真实采样帧数（原来放的是累计文本实例数），
  另加 `texts`；CLI `textqa` 对"重叠/出画/一闪而过/从没露面"四类都 `exit 1`（原来只看前两类）。
- **sfxkit 参数健壮性**：枚举值写错从"一个硬崩、一个静默"统一成明确报错（`_enum()`：
  `wind(env)` / `ui_blip(kind)` / `text_stream(voice)` / `whoosh(direction)` / `riser(end)` /
  `knock_wood(door)` / `metal_hit(obj)` 全部收口）；`sub_drop(start_hz=0)`、负/非整数 seed、
  `relay_click(gap<0)`、`typing_at(times` 含负数/NaN`)`、未知 `room` 都从裸
  `ZeroDivisionError`/`KeyError`/静默哑音改成可读 `ValueError`。
- **无 scipy 的性能门禁分档**（`PERF_BUDGET_SCIPY=1.5` / `PERF_BUDGET_FALLBACK=3.5`）：
  两条实现路径**不共用同一个数字**，自检输出里明写"当前无 scipy，按 fallback 预算判定；
  装 scipy 可加速并回到 1.5 s"——修掉"没装可选件 = 自检失败"这个假红灯，但没有偷偷放宽。
- **`film.json` 前置校验**：坏 JSON / `lines` 为空 / id 重复 / bpm 非法 / music·sfx·web 类型错，
  全部给一句能照着改的错，而不是 Python traceback；顶层加 `except` 兜底（`RENDER_DEBUG=1` 看栈）。

### 仓库工程化

- **`tools/ci_check.py`（9 项，不需要 ffmpeg/字体/网络）**：py 语法、`node --check`、JSON、
  SKILL.md frontmatter、三方依赖白名单、`npm pack` 打包卫生、文档路径引用、Python 3.9 语法、单文件体积。
- **`.github/workflows/ci.yml`**：`repo` job 在 ubuntu+windows 跑 ci_check；`skill` job 在 ubuntu
  装 ffmpeg + `fonts-noto-cjk` 后跑 `selfcheck.py` 与可选模块 selftest。
- **`skills/video-studio/requirements.txt`**：依赖分"必需 / 强烈建议 / 可选"三档并写明缺了会怎样。
- **打包卫生**：`files` 增加 `!skills/**/__pycache__` 与 `!skills/**/*.pyc`；补 `publishConfig.access`、
  `engines.node`、`homepage`、`bugs`。实测修前包内混入 `__pycache__`（922 KB），修后 431 KB / 35 文件。
- **插件入口不再用静态 `import` 引可选 peer**：改成 `apply` 内按需 `import`，缺依赖时只降级
  （不挂载 bundled 技能根）并打一条能照着做的日志，不让整行插件加载失败。

### selfcheck：23 条 → 24 条

新增第 24 条「配乐来源判定」：11 条 `music_plan` 断言 + 真跑一次 `st_audio` 验峰值
（`mode=off` 混音峰值 0.000 / `mode=file` 0.673）。模块自测同步扩到：
sfxkit 16、scorekit 17、qa_frames 22、webengine 24。

### 文档

- SKILL.md：`--stage qa`"零写入"改为"只读输入、只写 `work/qa/`"；分块目录 `work/segments/` 与
  默认 chunk 计算方式写清；新增"PIL 路线的同步门禁怎么真的生效"与"画面长时间静止"判据；
  硬约束补确定性模式；实测表修正"同帧 byte 完全一致"的适用条件。
- references/frame-qa.md 新增 §2.4b（静止判据实测分布与调阈值方法）、§2.5 补 v0.4 两条补救；
  web-engine.md 修掉指向不存在的 `assets/web/fonts/`；pitfalls.md 的分块命名改为真实格式。
- THIRD_PARTY_NOTICES：edge-tts 许可 GPL-3.0 → **LGPLv3**（实测元数据分类器）；Python 版本声明
  补"语法已验证、运行期未实测"的边界。

### 文档"真话化"（对账 100+ 条承诺 vs 实现）

这一轮专门做了一次**文档逐条对账**（读代码 + 跑命令，不是读文档推断），修掉三类问题：

**A. 抄了就失败（最高优先级）**

| 位置 | 问题 | 修法 |
|---|---|---|
| quickstart 第 4 步 `--stage stills` | 干净目录必失败（`没有 work/timeline.json`，实测 EXIT=1） | 代码侧让 `stills` **自动补 tts/layout**（会打印在补哪一步）；文档同步说明 |
| `audio.md` `trim_tail(...)` | 本仓库没有这个函数（实际 `trim_silence`） | 换成正确签名 |
| `SKILL.md` 的 `find ~ -path '*/skills/video-studio/filmkit.py'` | 少 `/scripts/`，永远匹配不到 | 补全路径 |
| `pipeline.md` 字幕公式 `min(..., voice_start + 5.2)` | 5.2 s 硬钳 v0.2 就移除了 | 更新公式，删"上限 5.2 s" |
| `pipeline.md` 注释写"只渲 3 帧"却给 `--stage frames --jobs 4` | 照抄会白渲全片 | 改成 `--stage stills` |

**B. 门禁口径不符（影响"能不能交付"的判断）**

- SKILL §7 那张表 8 行里 **5 行实际只出 warning**（48 kHz、长黑帧、字幕安全区、缺字形、画面静止），
  标题却写"交付前必过"。已拆成 **error 档 / warning 档两张表**，逐行标注级别。
- 响度「-16 ± 1 LUFS」既不是 error 带（< -19 / > -12）也不是 warn 带（< -17.2 / > -14.5），
  改成写真实两级阈值，并说明"±1 是目标区，不是判定阈值"。
- 音画同步行补上"**默认可能根本没在查**"，并指向新增的 `visual_at` / `QA_REQUIRE_SYNC` 两条补救。

**C. 描述了不存在的实现 / 内部矛盾**

- §5.5「母带压缩 threshold -9 / ratio 1.5」：**代码里没有压缩器**，链路只有 求和→duck→峰值保护→loudnorm。已改为如实描述。
- §5.5「旁白比配乐高 12 dB」「配乐比音效低 1.5 dB」：代码不测量也不保证，改标"人耳经验值 / 默认增益"。
- `audio.md` §3「各轨目标 LUFS + 前瞻限幅器」：本仓库没实现，标为"设计建议"并给出实际链路。
- `quality-gates.md` G3 音频判据与 `sfxkit.qc()` 实际判据不同 → 改为以 `qc()` 为准。
- `SUB_MAX_CHARS=24` 定义后**无任何代码引用** → 文档改为"像素宽度折行为准，24 字是大致上限"。
- `scene-authoring` 的「正文安全区到 974，天然满足 880 规则」**是错的**（SAFE 974 与字幕带 872 重叠 102 px）→ 改为"正文实际止于 ~860；SAFE 是硬边界不是排版建议"。
- 「120 BPM 的八分音符 = 0.5 s」算术错（八分是 0.25 s）→ 改为四分音符。
- `CHANGELOG` v0.3「新增 5 个 stage」实为 3 个；qa_frames「18 项」实为 19 项；§11/§12 编号漂移 → 全部校正。
- `README`「SKILL.md 第 10 节是实测基准」→ 实为 §12；stage 列表补全为 10 个。
- `web-engine.md`「可以按段混用两条路线」→ render.py 的 engine 是全局单选项，不支持按段切。
- `scene-authoring` 的 JPEG q94 数据属**上游经验**，本仓库固定 PNG → 标注来源与适用范围。
- `sfx-design.md` 引用不存在的 `MODAL` 符号 → 改为可指认的"模块顶部模态区"。

**D. 同一事实多套数字 → 统一成一个权威值 + 条件**

浏览器引擎速度曾有 4 处互斥数字（190–400 ms/帧 / 3–6 fps / 82 ms/帧 / 70–120 ms/帧），
统一为「80–130 ms/帧（核显、PNG、空载）」并注明条件；PIL 统一「40–80 fps（模板片 68–82）」；
scorekit 389 s 统一「4–8 s（随负载）」；sfxkit 389 s 环境声标出区间与"rain 余量最小"。
**原则：区间可以宽，但同一个事实只能有一套。**

### selfcheck / ci_check 之外新增的两道静态检查

- `ci_check` 从 9 项扩到 **10 项**：新增 **Markdown 围栏平衡**（这次真的发现
  `quality-gates.md` 里有个被反斜杠转义、从未闭合的围栏，会让后面整篇渲染成代码块）。
- `cv2` 从"文档说可选、代码硬 import"改成**真的可选**：缺 cv2 时 `sharpness`/`encode_quality`
  返回 `None`，`qa_report` 记一条**明确说明"锐度比门禁本次未执行"**的 warning，
  而不是抛 `ImportError` 崩掉整个 `--stage qa`。

### 已知局限（写在这里，因为门禁干净时最容易忽略）

1. **锐度比在 web 引擎路线仍是跳过**（没有参考帧），只打 WARN。web 路线要查锐度，得先让
   `webengine` 提供一个"渲染单帧并截图"的参考帧接口。
2. **`_peak_bound_ratio` 不是严格上界**（实测 300 组输入有 14 例超出，最坏 0.8 dB），
   当前靠 `finalize` 的第二道真峰校验兜底；36 次真实渲染未发现越限。
3. **`sync` 依赖命名约定或 `visual_at` 锚点**：两者都不写时，`sync` 恒空但会有 warning 明示
   "没在查"。看到 `sync: []` 请先看 `sync_info.judged`。
4. **跨机器性能数字只是参考**：所有 fps / 耗时都标注了机型，换机须重测（SKILL.md §12）。

## 0.3.0

> 这一版来自一次"把两个项目放在一起看"的复盘：本项目擅长**旁白驱动的图文科普片**（TTS 实测时间轴、
> 字幕安全区、质检门禁），而 `ai-history-film` 擅长**画面与声音本身**（浏览器逐帧渲染、程序化配乐、
> 物理建模音效、帧级质检）。v0.3.0 把后者**已经用一部 6 分半成片验证过**的能力，按本项目的约束重写进来。

### 新增能力（四个新模块 + 提示点系统）

- **`scripts/sfxkit.py` —— 物理建模程序化音效库**（v0.2 只有 `tone/pad_chord/noise_burst/whoosh` 四个函数）。
  24 个配方：界面/文字/转场/环境/撞击五类；模态合成（材质 × 形状 × 敲击位置 × 接触时间）、
  泊松/Cox 纹理事件、镜像声源混响、BS.1770 响度、块级压缩限幅。
  **scipy 可选**：没装 scipy 时所有 IIR 走"短冲激响应 + FFT 卷积"，长片不会卡死。
- **`scripts/scorekit.py` —— 程序化配乐**。一张贯穿全片的和弦表 + 分段能量（pad/bass/kick/hat/arp）+
  段落边界对齐小节线 + 重击前"吸气"（`suck`）。同 seed 逐采样一致。
- **`scripts/webengine.py` + `assets/web/` —— 浏览器逐帧渲染引擎**。
  v0.2 的 SKILL.md §3 推荐"浏览器 Canvas/WebGL 路线"但**没有任何代码资产**（文档承诺、代码没做到），
  v0.3 补上：Edge/Chromium + CDP 截图 → image2pipe、分块并行、断点续跑、缺口检测、崩溃重开、cue 导出、页面文字质检。
  页面用**原生 Canvas2D + 自写补间**，不 vendor GSAP / three.js（许可干净、体积为零）。
- **`scripts/qa_frames.py` —— 帧级/时间轴级深检**（当时 19 项自测，v0.4 扩到 22）。单帧闪烁
  （二阶差分 + 孤立性 + **"回到原位"双闸**，见下文集成期修正）、画面空窗（≥0.8 s 近黑）、
  音画同步（cue vs 视觉事件 dt）、亮度/响度曲线，出 JSON + PNG（PIL 手绘，不引 matplotlib）。
  这些是**唯一靠"看完全片"才能发现**的问题，单看任何一帧都正常。
- **提示点系统（cue）**：画面在"该响一下"的地方登记提示点（`film.json` 的 `sfx.cues`、
  `scenes.py` 的 `cues()` 钩子，或 web 页面的 `S.cue()`），音频阶段按 `work/cues.json` 合成音效轨。
  **改台词/加停留窗口/换 BPM 之后，音效不用重新对时间。**
- **三总线混音**：`filmkit.mix_film` 把旁白/配乐/音效分开处理，音效响起时配乐自动让位（`duck`），
  最后才交给 ffmpeg loudnorm。v0.2 是"配乐床 + 旁白"两路简单叠加。

### filmkit 的变化（向后兼容）

| 变化 | v0.2 | v0.3 |
|---|---|---|
| `AudioBus` | 只有单声道 | `channels=2` 立体声 + `pan_pos`；默认仍单声道 |
| `wav_write / decode_pcm` | 只支持单声道 | 自动识别单/双声道，新增 `wav_read` |
| `build_timeline` | 只有 lead/tail/min_dur | 新增 `hold`/`hold_map`/`bar`（停留窗口 + 小节对齐） |
| 混音 | `AudioBus.mix()` 一条总线 | `mix_film(voice, music, sfx)` 三总线 + `duck()` |
| 提示点 | 无 | `CueBus` / `cue_time` / `CUE_ALIASES` / `CUE_BASE_DB` |

**旧调用全部还能用**（selfcheck 第 13 条守着 v0.1 签名兼容）。

### render.py 的变化

新增 3 个 stage（`cues` / `score` / `sfx`，v0.2 的 7 个变 10 个），`--stage all` 顺序变为：

```
tts -> layout -> cues -> score -> sfx -> audio -> frames -> mux -> qa
```

新增 `--engine pil|web`（默认 `pil`，老项目行为不变）、`--force-music`。
`qa` 阶段会顺手跑帧级深检（没有 `qa_frames.py` 就跳过，不阻断）。

### selfcheck：14 条 → 23 条

新增 9 条，全部对应"v0.3 新代码可能出的错"（另外每个新模块还自带 `--selftest`：
sfxkit 16 项、scorekit 17 项、webengine 18 项、qa_frames 19 项，全部不联网、合计约 40 秒）：
立体声总线形状与声像、`cue_time` 三种写法、`CueBus` 落盘/读回/别名展开/越界丢弃、
`duck` 真的压低了配乐、`mix_film` 时长与峰值、以及四个新模块的存在性/确定性/契约检查
（模块缺失标 SKIP，不影响只用 PIL 路线的人）。

### 文档

- SKILL.md：§5 新增"声音：配乐 + 音效 + 提示点"、§6 新增"停留窗口"、
  §3 引擎表补上浏览器路线的真实用法与取舍、§7 质检门禁分四层（容器/信号/**时间**/页面）并给出 `deep.json` 判读表。
- 新增 references：`sfx-design.md`（音效设计流程 + 诊断表）、`web-engine.md`（页面契约 + 渲染器实现要点）、
  `frame-qa.md`（闪烁/空窗/同步判据）。
- `references/audio.md` 新增 §10 提示点系统、§11 程序化配乐、§12 程序化音效。
- `assets/film.json` 增加 `engine/bpm/hold/music.mode/sfx.cues/web` 字段；
  `assets/scenes.py` 增加 `cues()` 提示点钩子示范。

### 集成期发现并修掉的问题（都用实测定位，不是猜的）

| 现象 | 根因 | 修法 |
|---|---|---|
| `--stage cues` 在"剧本改短、cue 还指向老场景 id"时整个炸掉 | `CueBus.extend` 直接抛 `ValueError` | 逐条跳过并汇总 WARN；**全部落位失败才报错**（"少了两个提示点"和"一个都没落上"不是一回事） |
| 浏览器引擎的片子被帧级质检报 3 处"单帧闪烁" | 二阶差分分不清"单帧闪烁"和"加速运动最快的一帧"（3.7/7.7 s 是交界黑场起坡，0.733 s 是最后一个字入场） | `qa_frames` 加"回到原位"闸门：`|F[t+1]-F[t-1]|` 必须 ≤ 1.6×中间帧偏离量；两个演示片 flicker 归零，注入白闪的用例仍 PASS |
| 音画同步报 -0.40 s / -0.32 s 超差 | 跨 kind 的"最近事件"被当成"预期事件"（闪白去配 data_chirp） | 异 kind 只在 `|dt| ≤ tol` 时作佐证，不再据此判超差 |
| `scorekit` 写的 24-bit WAV 读不进来 | `filmkit.wav_read` 只支持 16-bit | 支持 16/24/32-bit PCM（含 24-bit 符号扩展） |
| 场景分块改了画面却不重渲 | 分块只按"文件存在"跳过 | 这是**已知语义**：改画面必须清掉分块目录（0.3.0 时靠人记；**0.4.0 起改为计划指纹 + 显式拒绝拼接**，见上） |

### 帧级质检的两条已知盲区（**故意的取舍，不是遗漏**）

写进这里是因为它们都会让"门禁看起来很干净"，而这正是最危险的失败模式。

1. **"回到原位"闸会放过快速运动段里的真实单帧闪**：如果前后两帧因**运动**产生的差异
   大于 1.6×闪烁幅度，真实闪烁会被判成运动。缓解：分数 ≥10 却被拒的候选**不进 error、但进 warning**
   （`高分异常被判为运动（建议人工复核）`），命令行摘要也会打"高分异常待复核 N"。
   **原则：门禁可以保守，但不能静默。**
2. **音画同步检查依赖命名约定**：`events.kind` 与 cue 配方名不同名时，同 kind 配不上、
   跨 kind 只做确认，`sync` 会恒为空（看起来"零超差"，其实一条都没在查）。
   约定与自查方法写在 `references/frame-qa.md` §2.5：**故意把 cue 改错 0.3 s，必须能报出来**。

### 许可

新增 `THIRD_PARTY_NOTICES.md`：sfxkit / scorekit / webengine / qa_frames 中**算法思想**改编自
`ai-history-film`（MIT）；本项目未复制其文件、未引入其三方依赖（three.js / GSAP 均未使用）。

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
  新增「升级须知」「换机 60 秒自检」两节（v0.2 时编号为 §11/§12，v0.3 起后移为 §13/§14）。
- references：quality-gates 的锐度比示例改用 `ref_img`；pipeline 更新 stage/mux；audio 补 TTS 缓存与裁剪。

### 兼容性

`filmkit` 公共 API 保持向后兼容（`tts`/`build_timeline`/`sub_window`/`mux`/`qa_report` 旧参数仍可用），
但默认行为有变，迁移项见 SKILL.md §13。