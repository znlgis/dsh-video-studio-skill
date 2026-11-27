# @dsh-gis/dsh-video-studio-skill

![version](https://img.shields.io/badge/version-2.10.0-blue) ![license](https://img.shields.io/badge/license-MIT-green)

**[中文](README.md) | [English](README.en.md)**（英文版是入口摘要，细节以中文版为准）

**DeepSeek Harness 技能插件：两条生产线做中文视频——有素材就剪素材（footage 模式），没素材才用代码画。**

写剧本 → edge-tts 旁白 → **实测时长**排时间轴 → **提示点**驱动配乐与音效 → PIL 或浏览器逐帧渲染 →
ffmpeg 编码 → 采样级混音 → **四层质检**（容器 / 信号 / 时间 / 页面）+ **五道新闸**
（渲前资源预检 `G0` / 词级时间戳 `G10` / 全片 VMAF `G11` / ASR 回读 `G12` / 版式文字冲突 `G13`）。
零素材、可复现、可断点续跑、可脚本化验收；装成插件后，整片生产流程随技能一起进入每个会话。

## 这个技能解决什么

| 你的痛点 | 它的做法 |
|---|---|
| 音画不同步 | 时间轴由 TTS **实测时长**反推，绝不按字数估算 |
| 文字发虚 | PIL 帧走 `rawvideo` 直通 libx264，不用 cv2 中间文件二次压缩 |
| 后半段静音 | numpy 采样级混音，不用一条 ffmpeg 命令喂上百个输入 |
| 该有的淡入/字幕静默消失 | 质检门禁查"必须有"和"不能有"两类断言 |
| 成片"像草稿" | -16 LUFS / 真峰 ≤ -1 dBTP / 编码锐度比 ≥ 0.85 |
| 做完了才发现画面类型选错（该用实拍却全画成图表，或反过来） | 问诊第 2 题先定**制作方式**：自己画 / 网上素材剪辑 / 两者结合；按题材给推荐（纪录片→素材剪辑、数据科普→自己画、两边都占→结合），理由列出命中的题材词，`--recommend` 可单独查 |
| 声音和画面对不上 | **提示点系统**：画面登记 cue → 音效按 cue 合成，改节奏不用重新对时间 |
| 声音单薄、只有"叮叮咚咚" | `sfxkit` 物理建模音效 + `musicgen` 按参数现场编排配乐（没有现成曲风可选） |
| 一帧白闪/两秒空窗没人发现 | `qa_frames` 帧级深检：单帧闪烁、画面空窗、音画同步、画面静止 |
| "我查过了"无法复现 | `out/QC-REPORT.md` + `work/gates.json`：三档 gate matrix，"没执行"独立成 `skipped` |
| "我签过了"变成空头支票 | 签核绑内容指纹：改剧本 / `scenes.py` / 引擎，旧签核自动失效 |
| 改一句台词不知道要重跑什么 | `work/manifest.json` 记每阶段输入/输出指纹，`--stage explain` 直接给最小重跑命令 |
| `holds` 写成拼写错误却静默失效 | `filmcheck.py` 执行 film.json 契约（接进 `ci_check` 第 12 项）；`schemas/film.schema.json` 供人和编辑器读字段（不做校验，只有两个测试读它核对枚举） |
| 机器替你选风格、你从头到尾没参与 | **提案优先**：`propose.py` 先出 N 个候选（各带静帧联系表 + 设计距离），人 `--confirm` 后才冻结 `design.json` |
| 每个视频都差不多 | 设计 DSL 现场采样（每次新 nonce）+ 候选两两 ≥2 维不同 + 底色色相分叉 + **片间去重台账** |
| 外观只能从几套模板里挑 | 没有内置风格表：配色是连续色相空间、母题/转场/缓动/版式/字幕全字段开放，且候选要过 `theme_lint` 对比度体检（剪影可区分是仓库离线门禁 `S13`，CI 与 selfcheck 第 55 条守着判据） |

## 效果：同一个 brief，两次就是两部不同的片（可复现）

向同一个 brief 连续提案两次，配色、转场、配乐、版式轮换、字幕样式都会不同（母题与声线默认锁定为素净 + 单声线，点名才会变）：

```bash
# 第一轮：看 3 个候选并排（每个候选 = 一份完整设计 + 静帧联系表 + 与其它候选的设计距离）
python <SKILL_DIR>/scripts/propose.py --brief "AI 大模型的三年，30 秒科普，深蓝" --script film.json --work work
#   候选 A  深色 h201·四角括线     3 帧  0.9s  -> work/proposals/rev1/A/_sheet.png
#   候选 B  深色 h220·细线页眉     3 帧  0.6s  -> work/proposals/rev1/B/_sheet.png
#   候选 C  深色 h213·极简留白     3 帧  0.6s  -> work/proposals/rev1/C/_sheet.png
#   联系表（并排对比）: work/proposals/rev1/_compare.png

# 换一个 work 目录再跑一次同一句 brief —— 会得到另一组设计（不传 --seed 就是每次新 nonce）
python <SKILL_DIR>/scripts/propose.py --brief "AI 大模型的三年，30 秒科普，深蓝" --script film.json --work work2
```

> 实测（作者机器）：同一 brief 连跑 3 次，9 个候选的 `design_fingerprint` **两两不同**；
> 每批 3 个候选的 `layout_cycle` 都是 2–3 种版式，`subtitle` 的字号/对齐/背板透明度/圆角/字族也各不相同。
> 带「深蓝」这类配色约束时，实测 **10/10 成功**。

**三个可复现的卖点**：

| 卖点 | 怎么自己验证 | 看什么 |
|---|---|---|
| 同一个 brief 两次结果不同 | 同一句 `--brief` 换 `--work` 目录跑两次 | 候选表的「立意」与配色色相都不一样；`design_fingerprint` 不同 |
| 三个候选并排对比 | `propose.py` 末尾会打印联系表路径 | `work/proposals/rev1/_compare.png`：一行一个候选，带中文立意标签 |
| 版式与字幕可变 | `render.py --stage layout` / `--stage style` | `storyboard.json` 每段的 `layout` 字段；`--stage style` 打印的「版式轮换」与「字幕」行 |

**画面长什么样，由设计决定而不是由"第几套模板"决定**：10 类构图（hero / statement / list / stat /
compare / process / quote / chapter / chart / outro）× 9 种装饰母题（默认素净、不带细线）× 6 种转场 × 连续色相配色 ×
字幕样式 × 按章节轮换的版式——而且每个候选都要过 `theme_lint` 对比度体检；剪影可区分是仓库离线门禁 `S13`（`tools/silhouette_qa.py`，selfcheck 第 55 条守着判据）。

> 证据是**命令 + 你机器上的实测输出**，不是截图：这份仓库里没有"参考成片"，
> 因为每次采样的外观都不一样，任何一张图都只代表某一次跑的结果。

## 安装

桌面版：插件管理器 → 填 `@dsh-gis/dsh-video-studio-skill`（或你的本地 checkout 目录，如 `D:/path/to/dsh-video-studio-skill`）

命令行：

```bash
dsh plugin --profile desktop add "@dsh-gis/dsh-video-studio-skill"
# 本地调试（不发布也能装）：
dsh plugin --profile desktop add "D:/path/to/dsh-video-studio-skill"
```

安装后在「插件」页启用 `video-studio` 这一行，新开会话即可在技能目录看到 **video-studio**。

### 与手工技能目录的关系

技能按 rank 解析：`~/.dsh/skills`（rank 400）**优先于**插件提供的 bundled 技能根（rank 600）。
如果你之前手工装过 `~/.dsh/skills/video-studio`，它会把插件版本遮住。装好插件后**把旧目录移出 `~/.dsh/skills` 根目录**（只改名不够——扫描认的是 `<root>/<name>/SKILL.md`，`.bak` 后缀的目录照样会被扫到）：

```bash
mkdir -p ~/.dsh/backup
mv ~/.dsh/skills/video-studio ~/.dsh/backup/video-studio.bak-$(date +%Y%m%d)
```

需要回滚时把目录移回 `~/.dsh/skills/video-studio` 即可。

## 用法（零拷贝：不拷脚本、不拷模板）

触发词：做一个视频 / 生成宣传片 / 把报告变成视频 / 做动画 / 配音配字幕 / 出成片 /
修视频音画不同步 / 视频画质糊，或提到 ffmpeg、edge-tts、逐帧渲染、字幕、分镜、配乐、混音。

**最短路径**（[SKILL.md §1](skills/video-studio/SKILL.md) 有带注释的完整版）：

```bash
mkdir -p myfilm && cd myfilm
# 1) 只写一个 film.json（工程目录里唯一需要你手写的文件；最小骨架见 SKILL.md §1）

# 2) 环境自检（可选，108 条断言，不联网）
python <SKILL_DIR>/scripts/selfcheck.py

# 3) 先问诊：固定 8 题一次问完（题材/制作方式/时长/气质/画幅/声线/配乐/补充），气质默认「明亮活泼」
#    「制作方式」（自己画 / 网上素材剪辑 / 两者结合）按题材给推荐：--topic 随问诊单带出，或单独 --recommend
python <SKILL_DIR>/scripts/intake.py --work work --questions --json --topic "AI 大模型的三年"
python <SKILL_DIR>/scripts/intake.py --work work --recommend "中国高铁十年，纪录片"
python <SKILL_DIR>/scripts/intake.py --work work --set "topic:AI 大模型的三年" --set mode:auto \
       --set duration:30s --set tone:bright-lively --set voice:female-bright --set music:upbeat
python <SKILL_DIR>/scripts/intake.py --work work --finalize     # -> work/intake.brief.txt + 下一步命令

# 4) 再出方案：3 个候选，各带静帧联系表；2 秒级、全程离线（问诊没答完会被拒；不传 --brief 时读 intake.brief.txt）
python <SKILL_DIR>/scripts/propose.py --script film.json --work work

# 5) 确认候选，冻结设计 -> work/design.json（此后渲染只认这份设计）
python <SKILL_DIR>/scripts/propose.py --confirm --candidate A --work work

# 6) 先看静帧，再出全片。--scenes 不写 = 剧本同目录 scenes.py -> 技能内置 assets/scenes.py
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage stills --stills "l01:3,l02:5"
python <SKILL_DIR>/scripts/render.py --script film.json --work work --out out/film.mp4 --stage all

# 7) 交付凭据 + 人工签核
cat out/QC-REPORT.md
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage accept --gate release
```

`<SKILL_DIR>` 是 `skill` 工具加载时回报的 resourceBase 目录（`.../skills/video-studio/`）；
`<REPO>` 是包根（仓库根，或装好后 profile 里的 `node_modules/@dsh-gis/dsh-video-studio-skill/`），
即 `<SKILL_DIR>` 往上两级——`tools/`、`schemas/` 在 `<REPO>` 下。

**分阶段**（`--stage` 每个只做自己的事，落盘后可单独重跑）：
`style | tts | layout | beats | cues | score | sfx | audio | frames | mux | qa | stills | review | editlist | jianying | accept | explain | all`。

> `--stage all` 的顺序是 `style → tts → layout → beats → cues → score → sfx → audio → frames →
> mux → qa → editlist`；`review` / `jianying` 刻意不在 all 里（独立终检是交付前的独立动作，
> 剪映导出要 `--no-subs` 出来的干净画面）。

**你想控制什么，就改哪里**：

| 想控制 | 改哪里 | 怎么验证 |
|---|---|---|
| 画面配色/字体/装饰/转场/版式 | `work/design.json` 的 `visual.*`（或重新提案 `--revise`） | `render.py --stage style`；`designkit.py --lint work/design.json` |
| 字幕字号/位置/对齐/背板/圆角/字族/颜色/行数 | `visual.subtitle` | `--stage style` 打印的「字幕」行；改前后抽帧对比 |
| 片内版式轮换（防全片一张版式） | `visual.layout_cycle`，不写则自动派生 2–3 种 | `work/storyboard.json` 每段的 `layout` |
| 这段画面的构图类型 | `film.json` 的 `lines[].type`（10 类之一），不写由 grammar 选 | `--stage layout` 打印的分镜表 |
| 配乐调式/BPM/调性/情绪 | `work/design.json` 的 `audio.music.*`（`film.json` 的 `music.*` 可覆盖） | `--stage score` 的打印 + 听 |
| 旁白声线与语气 | `audio.voice.*`（裸 edge voice id + tone）；`voicekit.py --list` 查音色 | `--stage tts`；`work/voice_plan.json` |
| 音效密度与提示点 | `audio.sfx.*`；提示点写 `film.json` 的 `sfx.cues` 或 `scenes.py` 的 `cues()` | `--stage cues` / `--stage sfx` |
| 节奏（停顿/最小段长/条目步进） | `pacing.*`；单句停留写 `film.json` 的 `lines[].hold` | `--stage layout` 的总时长 |
| 目标时长（如「2 分钟」） | `--brief` 里写出来 → `design.structure.target_sec` | `propose` 打印目标时长；渲染时对账，差 >15% 告警。**成片长度仍由实测旁白决定，不会截断** |
| 画面本身（绘制代码） | 剧本同目录放自己的 `scenes.py`（实现 `draw_frame`，可选 `cues()`） | `--stage stills` 抽帧看 |

**常用开关**：

| 想要 | 怎么做 |
|---|---|
| 交付 720p / 换帧率 | `film.json` 写 `"video": {"width":1280,"height":720,"fps":24}`（横屏 16:9 / 竖屏 9:16 都支持：竖屏写 1080x1920 并加 `--layout portrait`；只能同比例下采样） |
| 批量出片 | `python <SKILL_DIR>/scripts/render.py --batch films.json`（≤100 条、开跑前全量预检、单条失败不阻断） |
| 只渲前 N 秒给人看 | `--preview 15`（出 `out/preview/*.mp4` + `.gif`，不写交付账本） |
| 让 QA 快一点 | `--vmaf sample`（每 5 帧取 1）或 `--vmaf off`（G11 记 skipped） |
| 交付前把「没执行」也算失败 | `--strict-gates` |
| 改完东西不知道要重跑什么 | `--stage explain`（机器判断过时阶段 + 给最小重跑命令） |
| 换一个候选/方向重出方案 | `propose.py --revise "再冷一点，少装饰，换女声"`；旧 rev 留着眼 diff |
| 复现某一次提案 | 首次提案加 `--seed 42`（不传 seed = 每次新 nonce） |
| 忽略/查看片间去重台账 | `--no-ledger` / `--show-ledger`；路径可用 `VIDEO_STUDIO_LEDGER` 覆盖 |
| 浏览器渲染要跨会话字节级可复现 | `--deterministic`（或 `WEBENGINE_DETERMINISTIC=1`），代价约 +22%/帧 |

长片：`--stage frames --jobs 4`（按绝对帧号分块并行）+ `--resume`（中断只补缺块）。

## 文件结构

```
index.js                     插件入口：把 skills/ 挂成 bundled 技能根
cordis.patch.yml             组合包 patch（insert: video-studio）
icon.svg / locale/*.json     插件页展示元数据
skills/video-studio/
  SKILL.md                   主线：零拷贝上手 / 提案 / 设计 DSL / 八阶段 / 引擎 / 画质 / 声音 / 提示点 / 质检 / 升级
  references/                （25 篇，按需加载）proposal · scene-grammar · pipeline · scene-authoring · appearance
                             footage · hybrid-overlay · audio · audio-variation · sfx-design
                             web-engine · frame-qa · quality-gates · delivery-gates · word-timeline · change-impact
                             portrait-and-motion · machine-facts · pitfalls · script-index
                             aesthetic-rules（判例式审美准则）· final-review（独立终检回执）
                             beat-sync（节拍网格 / beat 锚点 / 动效预设 / 参考片逆向）
                             shot-cards（10 张镜头配方卡）· export-handoff（剪辑清单 / 剪映草稿 / 素材授权）
  scripts/intake.py          出片前问诊（固定 8 题 -> work/intake.json -> brief；含制作方式推荐与产线路由）
  scripts/propose.py         提案器（--brief/--revise/--confirm/--diff + 片间去重台账）
  scripts/designkit.py       设计 DSL（采样/校验/转计划/设计距离/字幕参数）
  scripts/previewkit.py      离线预览（合成时间轴 + 联系表，不联网）
  scripts/grammar.py         场景语法（10 类构图 -> storyboard.json）
  scripts/voicekit.py        声线/语气计划（裸 edge voice id + 响度补偿）
  scripts/filmkit.py         生产内核（字体/排版/字幕/音频总线/CueBus/TTS/时间轴/编码/质检）
  scripts/gates.py           门禁账本 / QC 报告渲染 / 人工签核（绑内容指纹）
  scripts/stagewatch.py      阶段新鲜度（work/manifest.json + --stage explain）
  scripts/filmcheck.py       film.json 契约校验（未知键/枚举/跨字段；style 键为 inert 警告）
  scripts/render.py          编排器（17 个 stage、pil|web 双引擎、分块并行、断点续跑、母版预览、门禁账本；
                             --scenes 缺省回落到技能内置 scenes.py）
  scripts/preflight.py       渲染前资源预检（字形 / TTS 音频 / 字体 / web 入口页，门禁 G0）
  scripts/qavmaf.py          全片 VMAF/SSIM（参考帧由场景函数实时重渲，门禁 G11）
  scripts/qaasr.py           ASR 回读（成片音轨转文字对账，门禁 G12）
  scripts/selfcheck.py       回归自检（108 条针对真实缺陷的断言）
  scripts/reviewkit.py       验收帧（每段 enter/settle 写进 timeline）+ 音轨偏移补偿（纯函数，可单测）
  scripts/reviewpacket.py    独立终检评审包拼装（检查清单七组 / packet.json / PACKET.md，不抽帧不写盘）
  scripts/beatgrid.py        节拍网格（三频段 STFT + 等距最小二乘定 BPM/相位，纯 numpy；产出 beat_data.json）
  scripts/shotcards.py       镜头配方卡（10 张：构图/时长/字幕位/动效/音效；查卡、按意图找卡、只填空值套用）
  scripts/editlist.py        剪辑交接清单（逐镜 in/out + 帧号 + 旁白 + 音效提示 -> editlist.json / EDITLIST.md）
  scripts/jianying.py        剪映草稿导出（µs 整数 + 字号按画布高百分比；缺 pyJianYingDraft 降级为 draft_plan.json）
  scripts/critic.py          审美质检（离线启发式 0-100 分 + 可选 VLM 复评；只用于排序候选，不阻断交付）
  scripts/matchstyle.py      hybrid 风格对齐（素材画像 -> 图文段配色/画幅；--apply 前过 theme_lint，失败零写入）
  scripts/layoutkit.py       画布/安全区单一真源（横竖屏 profile；scenes/filmkit/designkit 共用）
  scripts/studio.py          薄门面（studio.py <子命令> 转调现成脚本，未知参数原样透传）
  scripts/sfxkit.py          物理建模音效库（24 配方，scipy 可选）
  scripts/scorekit.py        配乐合成原语（和声 / 律动 / 混音 / 响度 / 24-bit 落盘）
  scripts/musicgen.py        参数化配乐编排器（没有风格表：和弦进行 / 动机 / 鼓型 / 音色全可传参）
  scripts/webengine.py       浏览器逐帧渲染（CDP 截图、分块并行、崩溃重开、cue 导出、文字质检）
  scripts/qa_frames.py       帧级深检（单帧闪烁 / 画面空窗 / 画面静止 / 音画同步 / 亮度响度曲线）
  scripts/stylekit.py        视觉/音频原语库（颜色数学、版式几何、主题体检；纯数据 + 纯函数，不含任何外观成品）
  scripts/compose.py         hybrid 形态 3 合成入口（素材打底 + 小面积叠加；overlay.json 契约）
  scripts/qatext.py          版式文字冲突门禁 G13（文本框相交 / 越出安全区 / 压字幕带；PIL 路线的采集与判定）
  scripts/footage.py         素材剪辑一条龙（10 个 stage；契约是 footage.json）
  scripts/footagekit.py      素材线取材层（load_footage / probe / fetch / transcribe / locate / 画面治理滤镜）
  scripts/footageedit.py     素材线剪辑层（build_edl / check_edl / cut_shots / mix_tracks / write_ass / assemble）
  scripts/footageqa.py       素材线专属门禁 F1–F9（越界 / 烧录字幕残留 / 水印残留 / 音画等长 / 署名 / 旁白主导…）
  requirements.txt           Python 依赖（必需 / 强烈建议 / 可选三档，带注释）
  assets/film.json           测试夹具（含 bpm / music / sfx.cues / 逐句 hold；不是给用户抄的模板）
  assets/scenes.py           技能内置渲染器（10 类构图 + draw_frame + cues() 钩子；--scenes 的默认回退）
  assets/design_fixture.json 金帧基准设计（像素回归绑它，不绑任何内置风格）
  assets/voice_loudness.json 音色响度补偿实测表
  assets/golden_frames.json  金帧基线（3 帧 sha256 + 字体/环境指纹，改绘制代码后重录）
  assets/golden_frames_portrait.json  竖屏金帧基线（--layout portrait --write 重录；横屏基线不受影响）
  assets/golden_frames_compose.json   构图层基线（10 类构图各一帧；--compose --write 重录）
  assets/golden_frames_compose_portrait.json  竖屏构图层基线（同上，--layout portrait --compose）
  assets/scenes_hybrid_template.py    在内置渲染器上做最小改动的正确写法（形态 1 图文段包装）
  assets/footage/footage.template.json 素材线契约模板（先把 sources 落地，再给每句旁白指定 shot.at）
  assets/footage/regression/ 素材线回归样例（footage.json + 联系表；tools/footage_selftest.py 用它）
  assets/web/                浏览器页面示例（--engine web 需要你自己的页面；不是出片模板）
schemas/design.schema.json   设计 DSL 契约（含 visual.subtitle / layout_cycle）
schemas/proposal.schema.json 提案契约
schemas/film.schema.json     film.json 字段说明（给人/IDE 补全；执行者是 scripts/filmcheck.py）
schemas/footage.schema.json  素材线契约（footage.json；执行者是 scripts/footagekit.py）
tools/audio_offset.py        音轨偏移实测（成片音轨 vs 混音母带做互相关，输出 mux.audio_offset_ms 建议值）
tools/refscan.py             参考片逆向（scene 切点 + 逐镜时长/动幅/亮度 + 三列拆解模板 REVERSE.md）
tools/asset_license.py       外部音频素材授权台账（来源/授权/商用/署名；缺项不可交付，可生成 CREDITS 与台账）
tools/ci_check.py            仓库级体检（18 项；语法/JSON/依赖白名单/打包卫生/文档引用/film+footage 契约/断言条数/素材预算与形态路由/G13 注册/包面清单，不需 ffmpeg）
tools/estimate.py            开渲前成本预演（帧数/耗时/磁盘/分块建议，--json 可机读；含 preflight/vmaf/asr）
tools/golden_frames.py       金帧像素回归（--write 重录 / --check 校验；同环境像素变了 = fail）
tools/silhouette_qa.py       剪影可区分门禁 S13（离线检查，不进账本；换设计不能只是换色）
tools/audio_similarity.py    音频差异/相似度验收（--strict 阈值判定，改音频代码后必跑）
tools/selftest_noscipy.py    无 scipy 兜底路径自检（CI 用；屏蔽 scipy 后跑 sfxkit/musicgen 自检）
tools/footage_selftest.py    素材线自检（自己造带烧录字幕/水印的素材，跑 probe→qa 全链 + F1–F9）
tools/matchstyle_selftest.py hybrid 风格对齐自检（素材画像 -> 图文段配色，含左右黑带回归）
tools/scenes_hybrid_selftest.py 包装内置渲染器的真回归（留原件 / 返回副本 / 缓存键三坑）
tools/plugin_entry_check.mjs 插件入口冒烟（apply() 必须真的注册 provider / 配置面真的注入 / 非法配置真的报错，防 DSH 换接口后静默降级）
tools/diagnose.mjs           一行诊断入口（npm run diagnose：技能数 / 可见数 / 降级原因 / 悬空引用 / 目录 token 估算，healthy=false 退 1）
tools/diagnose_selftest.mjs  诊断入口的坏包回归（好包全绿 / 删除 references 报 danglingRefs / frontmatter 非法与 name 不一致报 degraded / 缺失与 IO 降级）
tools/compat_report.mjs      兼容性报告生成器（npm run compat:report -> docs/compatibility.md；真跑入口冒烟 + 仓库体检，跑不了写 not-run）
docs/compatibility.md        兼容性证据报告（生成物：DSH 版本 + 证据脚本 + 本轮真实执行结果）
.github/workflows/ci.yml     CI：repo 体检（ubuntu+windows）+ 技能自检（ubuntu，含 VMAF/素材线/hybrid/web）+ py39 + Windows advisory
```

## 环境前提

**插件运行**：Node.js ≥ 20（`package.json` 的 `engines`；这是 DSH 加载插件用的运行时，不必另装）。
**必需**：ffmpeg + Python 的 numpy / Pillow / edge-tts。
**可选**：cv2（编码锐度比）、scipy（音效滤波加速，缺了走 FFT 卷积）、
playwright + Chromium/Edge（只在 `--engine web` 时需要）、
faster-whisper（ASR 回读门禁 G12；没装会降级到 ffmpeg whisper 滤镜，仍不可用则记 skipped）。
G11（全片 VMAF）不需要新依赖，但要求 ffmpeg 构建**带 libvmaf**（9.0.2 full_build 有）。
不需要 moviepy / pydub / soundfile / librosa / manim——本技能一行都不 import 它们。

依赖清单有机器可读的一份，别再手抄：

```bash
pip install -r skills/video-studio/requirements.txt   # 必需 + 强烈建议；可选项目已注释，按需打开
```

参考机器（Windows 11 + Ryzen AI 9 HX 370）的实测基准在 [`references/machine-facts.md`](skills/video-studio/references/machine-facts.md)（正文瘦身后不再常驻），**换机请先重测**：
`ffmpeg -version`、`python -c "import numpy,PIL,cv2"`、中文字体文件、`edge-tts --list-voices`。
ffmpeg 路径可用环境变量 `FILMKIT_FFMPEG` 覆盖，字体候选在 `filmkit.FONT_CANDIDATES`。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 兼容性

**判据是 `package.json` 的 `engines.dsh`：`>=0.1.0-rc.6 <0.3.0-0`（含预发布版）。**
DSH 0.2.x 的加载器读 `engines.dsh` 判插件能否跑；写这个字段时**只按它判**、忽略 `peerDependencies`，
所以 0.2.2-rc.5 这类预发布版也在范围内（DSH 内部用 `semver.satisfies(..., {includePrerelease:true})`）。

**已实测**：DSH Desktop **0.2.2-rc.5**（nightly 通道）上技能正常挂载——本会话里 `skill` 工具能列出
`video-studio` 就是证据；`node tools/plugin_entry_check.mjs` 断言 `apply()` 注册了 1 个 provider、
`list()` 返回 `rank=600 / source=bundled / resourceBase=技能目录` 的候选、`get()` 能读出 SKILL.md。
**上界为什么钉在 0.3.0**：0.3 若改 `SkillProvider` 契约（`list/get` 签名或候选字段），插件会静默降级；
升级 DSH 大版本前先跑一次 `plugin_entry_check.mjs`，它绿了再改 `engines.dsh` 上界。

**机器可读的证据侧**在 `package.json` 的 `dsh.compatibility`：`verified[]` 里的每个版本都指向一个
**真实存在的校验脚本**（`evidence`），`assumed[]` 写明未逐一实测的范围与原因。它与 `engines.dsh` 分工不同、
不合并：`engines.dsh` 是 npm 装包时的判据，`dsh.compatibility` 是"我们实测过什么"的证据。
重新生成证据报告：

```bash
npm run compat:report     # -> docs/compatibility.md（真跑 plugin_entry_check + ci_check，跑不了写 not-run）
```

**装好之后技能为什么没出现？** 跑一行诊断，不用翻日志：

```bash
npm run diagnose                    # JSON 到 stdout；healthy=false 时退出码 1
npm run diagnose -- --human         # 人读版
```

它只读、不联网、不写盘：返回 `skills` / `visible` / `missing` / `degraded`（frontmatter 非法、name 与目录名不一致）/
`danglingRefs`（正文与 references 里指向不存在的文件）/ `catalogTokens` / `hostVersionHint` / `healthy`。

技能注册接口在 0.2.x 换过：

| DSH | 技能注册接口 | 本插件怎么走 |
|---|---|---|
| 0.2.x | `ctx.skills.registerProvider(create)` | **主路径**：插件自带 provider（只读 SKILL.md 的 frontmatter，`rank=600`，`resourceBase`=技能目录），**不 import 任何 `@deepseek-ai/*` 包**，所以装到哪种 DSH 里都能自己跑起来 |
| 0.1.x | `@deepseek-ai/dsh-skill-filesystem` 的 `apply(ctx, config)` | 回退路径：仅当 `ctx.skills.registerProvider` 不存在时才用；此时那个包仍是可选 peer（`optional`），缺了只打日志降级 |

> 为什么不再只用 peer：v2.1.1 及更早只走 0.1.x 那条路，在 DSH **0.2.2-rc.5** 上实测——插件目录解析不到
> 运行时自带的 `dsh-skill-filesystem`（它在 `app.asar` 里），`import` 抛 `ERR_MODULE_NOT_FOUND`，
> 于是 `apply` 只打一条「缺少可选依赖」的日志就返回：**技能一个都没挂上，而插件行、语法检查、自检全绿**。
> 这类静默降级现在由 `tools/plugin_entry_check.mjs` 在 `ci_check` 第 2 项里真跑一遍入口来守。

## 仓库自检（给贡献者与 CI 用，不是给用户用的）

```bash
python tools/ci_check.py -v     # 18 项：py 语法（含 tools/，SyntaxWarning 记失败）/ node --check + 插件入口冒烟 + 诊断坏包自检 /
                                #         JSON + 版本号一致性（package.json vs README 徽章 vs CHANGELOG）/
                                #         技能清单 / 依赖白名单 / npm pack 打包卫生 /
                                #         文档路径 + 正文相对链接 / Python 3.9 语法 /
                                #         Markdown 围栏（状态机）/ CI workflow /
                                #         单文件体积 / film.json 契约（调 filmcheck.py，规则只留一份）/
                                #         footage.json 契约（调 footagekit.load_footage；缺 numpy/Pillow 记 SKIP）/
                                #         素材预算与 hybrid 形态路由（source_budget 常量、F8 注册、--hybrid-form）/
                                #         版式文字冲突门禁（G13/F9 注册 + 采集钩子）
```

它**不需要 ffmpeg、字体或网络**，任何机器都能跑；它检查的是"这个仓库作为可分发包是否完整"。
**它不能替代** `skills/video-studio/scripts/selfcheck.py`——那个要 ffmpeg + 中文字体，真的调编码器，
查的是"技能本身还能不能出片"（**108 条**断言）。

三条产线级自检（都要 ffmpeg，在 `ci.yml` 的 skill job 里跑）：

```bash
python tools/footage_selftest.py      # 素材线：自己造带烧录字幕/水印的片子，跑 F1–F9 全链路
python tools/matchstyle_selftest.py   # hybrid：素材画像 -> 图文段配色，含"左右黑带"回归
node tools/plugin_entry_check.mjs   # 插件入口：apply 必须真的注册 provider（0.2.x 契约）
node tools/diagnose_selftest.mjs    # 诊断入口：好包全绿 + 五类坏包必须报出来（不写工作树）
node tools/diagnose.mjs             # 一行诊断：真包必须 healthy=true
npm run compat:report               # 兼容性报告：真跑入口冒烟 + 仓库体检，生成 docs/compatibility.md
```

`plugin_entry_check.mjs` 是**DSH 兼容性的哨兵**：DSH 换技能注册接口时，插件会静默降级成"一行空插件"，
语法检查、selfcheck 全绿，只有真的把 `apply()` 跑一遍才发现技能没挂上。

## License

MIT。新增的四个模块（sfxkit / scorekit / webengine / qa_frames）的**算法思想**改编自
[ai-history-film](https://github.com/Re-Summer75/ai-history-film)（MIT），未复制其文件、未引入其三方依赖；
详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
