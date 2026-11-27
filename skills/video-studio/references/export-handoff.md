# 交接与导出：剪辑清单、剪映草稿、素材授权

> 何时读：片子已经出来了，但活还没完——要交给人在剪映/PR 里改一轮、要发到平台、
> 或者片子里用了外部素材（音效/BGM）需要交代出处。

本技能默认产出**最终成片**。但真实工作流里成片常常只是中间物：要加花字、换音乐、
调色、发多平台。这一页讲三种交接物，以及它们各自的诚实边界。

## 1. 剪辑交接清单（`--stage editlist`）

```bash
python <SKILL_DIR>/scripts/render.py --script film.json --work work --out out/film.mp4 --stage editlist
```

产物 `work/editlist.json` + `work/EDITLIST.md`：逐镜的 in/out、帧号、时长、旁白原文、
落在该段内的音效提示点（相对段首）。

**为什么值得单独出一份**：只给 mp4，剪辑师得自己听一遍重新对齐；给一句口头说明，
他照样得对一遍。而这张表的时间是真源：**剪辑师按表剪出来的顺序与成片逐帧一致**。
如果对不上，是表错了而不是成片错了——所以这张表也进交付证据。

口径：

- 时间是成片**绝对秒**；`in` 含、`out` 不含，长度 = out − in；
- 不在剪映里重排顺序（重排会让字幕与旁白错位）；
- 改完任一段长度，字幕与音效都要按表重新对，不能只拖画面；
- 响度已按 −16 LUFS / 真峰 ≤ −1 dBTP 调过，剪辑软件里**别再加**音量标准化。

## 2. 剪映草稿（`--stage jianying`）

```bash
# 1) 先出"干净画面"（帧上不烧字幕），成片照旧可用
python <SKILL_DIR>/scripts/render.py --script film.json --work work --no-subs --out out/plate.mp4

# 2) 再导草稿计划，把 plate 指给它（有 pyJianYingDraft 时写真草稿）
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage jianying --plate out/plate.mp4
```

`--plate` 必须显式给：它和 `--out` 是两盘不同的东西——`--out` 是交付成片（带字幕），
`--plate` 是给剪辑软件当素材盘的干净画面。不传就只出 `draft_plan.json` 并明确降级。

`--no-subs` 只影响**帧上烧不烧字幕**：`sub.srt` / `sub.ass` 照旧写，所以这一版成片
仍然"有字幕但不烧进画面"——它正是剪映需要的 plate。

产物 `work/jianying/draft_plan.json`（与依赖无关的中间层）；装了 `pyJianYingDraft`
（0.3.x）时额外写真草稿目录。

三个设计决定，看懂了能省一次返工：

| 决定 | 为什么 |
| --- | --- |
| 只写**干净画面**（plate），字幕/音效另挂轨 | 烧进画面的字幕在剪映里抠不掉，而人第一件事往往就是改字幕 |
| 时间换成**微秒整数**（timebase 1e6） | 剪映内部就是这个单位；各处自己乘 1e6 再取整必然对不齐 |
| 字号换成**画布高度百分比** | 剪映字号不是像素。直接抄 46px 会让 9:16 与 16:9 观感差一倍 |

换算都在 `jianying.us()` / `us_range()` / `subtitle_size_ratio()` 三个函数里，**单一换算口**。

### 缺依赖时会怎样（诚实边界）

`pyJianYingDraft` 是**可选**依赖（`pip install pyJianYingDraft`）。没装时：

- 仍然输出 `draft_plan.json`（时间轴 / 字幕 / 时长全在里面，可照它手搭）；
- 打印 `[降级] 没写出剪映草稿：<原因>`，并给安装命令；
- **不会**假装成功。stage 记账仍记 pass（计划产出了），但降级信息必须让人看见。

`--stage jianying` 也不在 `--stage all` 的关键路径上：它是出口，不是出片必经环节。

## 3. 外部素材授权（`tools/asset_license.py`）

`sfxkit` 是现场合成的，天然没有授权问题；但真实项目常还要用外部音效/BGM。风险不在
"好不好听"，而在**交付之后才被发现**：不知道出处、不知道能不能商用、不知道该不该署名。

登记写在 `film.json` 的 `assets.audio`：

```json
"assets": {"audio": [{"path": "<你的音效文件>.wav", "title": "Rain", "author": "someone",
  "source": "https://freesound.org/s/123456/", "license": "CC-BY-4.0"}]}
```

```bash
python tools/asset_license.py --film film.json --check                      # 不可交付时 exit 1
python tools/asset_license.py --film film.json --credits out/CREDITS.txt   # 片尾署名
python tools/asset_license.py --film film.json --ledger work/LICENSE.md     # 台账
```

判据：

| 情况 | 结论 |
| --- | --- |
| 缺 `path`/`title`/`author`/`source`/`license` 任一 | **fail**（没出处不交付） |
| 不认识的授权名 | **fail**（不默认放行） |
| `CC-BY-NC-*` 却写 `commercial: true` | **fail**（自相矛盾） |
| `other:<说明>` 但没写 `commercial` | **fail** |
| 登记了但文件不存在 | **WARN**（交付前补齐） |
| 全部合规 | pass，并按授权算出要署名哪几条 |

> 与 F5（素材线溯源）同一口径：**没出处就不能交付**。区别是 F5 查画面素材，这里查音频，
> 两处都查才完整。CC 条款会变、站点会下架，台账每年至少复核一次。

## 4. 三者与「不做什么」

| 想要 | 用 | 说明 |
| --- | --- | --- |
| 人接手继续改 | `--stage editlist` | 逐镜表 + 帧号，照表剪 |
| 在剪映里继续做 | `--stage jianying` | 干净画面 + 文本轨；缺依赖降级为计划 |
| 用了外部素材要交代 | `tools/asset_license.py` | 检查 + 署名 + 台账 |
| 想让片子像某个参考片 | `tools/refscan.py` | 见 [beat-sync.md](beat-sync.md) §6 |

**不做**：不替换 `sfxkit`（现场合成仍是默认）、不做剪辑软件 UI 集成、不保证第三方依赖
的内部 API 稳定（版本不匹配时降级并说明，而不是硬写半个坏草稿）。
