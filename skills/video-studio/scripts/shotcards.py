# -*- coding: utf-8 -*-
"""shotcards.py — 镜头配方卡：把「这段想干什么」翻成一组能直接落到 film.json / scenes.py 的参数。

为什么需要这张表：场景语法（grammar.py）解决的是"10 类构图怎么选"，但真实制作里
卡住人的不是"选哪类构图"，而是"这个具体镜头该多长、字幕放哪、动效要不要、配什么声音"。
这类知识现在散在四个文件里（scene-grammar 的时长门槛、designkit 的动效、audio 的音效、
aesthetic-rules 的判例），每做一段都得翻一遍。

所以这里只做一件事：给**有名字的镜头意图**配一套默认参数，让"我要一个开场钩子"
变成可以照抄的一段配置，而不是每次重新推理。

三条纪律：
  1. **卡是候选池，不是模板**：每张卡给的是一组合理默认，不是唯一答案。
  2. **不新增外观**：卡只引用已有的 10 类构图 + 已有 SFX 配方 + 已有动效预设，
     不引入任何新的绘制器或音色。抄这张表不会让片子"变得像某套模板"。
  3. **卡要能被机器检查**：每张卡的 kind 必须在 grammar.SCENE_TYPES 里、sfx 必须在
     sfxkit 的配方里、ease 必须在 designkit.EASES 里——自检第 108 条盯着，
     防止改了枚举之后卡库悄悄指向不存在的东西。
"""
from __future__ import annotations

# 卡片字段说明：
#   id      卡号（稳定，只追加不重排）
#   name    人话名字
#   intent  什么时候用这张卡（这是**唯一**的选卡依据）
#   kind    grammar.SCENE_TYPES 里的构图类型（"auto" = 交给语法自动选）
#   dur     [最短, 建议, 最长] 秒；最短不得低于 grammar 的段长门槛
#   sub     字幕位置建议："bottom" / "none"（画面上已有大字时不要再压字幕）
#   ease    动效曲线（designkit.EASES 之一）
#   motion  动作幅度档："still" 几乎不动 / "soft" 轻 / "strong" 明显
#   sfx     [(相对段首秒数, sfxkit 配方名)]；空表示这段不配音效
#   avoid   这张卡的常见误用（写清楚才叫判例）

CARDS = [
    dict(
        id="C01",
        name="开场钩子",
        intent="第一段，要让人在 3 秒内知道这片子讲什么、并且愿意看下去",
        kind="hero",
        dur=[2.4, 3.2, 4.5],
        sub="none",
        ease="out5",
        motion="soft",
        sfx=[(0.0, "whoosh"), (0.35, "impact")],
        avoid="不要在这张卡上叠字幕：封面已经有标题与副标，第三行字会挤掉留白。",
    ),
    dict(
        id="C02",
        name="数字定格",
        intent="有一个能代表全片量级的数字，要让它单独占一屏",
        kind="stat",
        dur=[3.2, 3.8, 5.0],
        sub="bottom",
        ease="out3",
        motion="still",
        sfx=[(0.0, "ui_blip")],
        avoid="数字与单位必须来自 FACTS.md 实测值；凑数用的数字宁可换成 list。",
    ),
    dict(
        id="C03",
        name="要点清单",
        intent="3–6 条并列信息，每条要说清但不需要展开",
        kind="list",
        dur=[3.0, 4.2, 6.0],
        sub="bottom",
        ease="out3",
        motion="soft",
        sfx=[],
        avoid="超过 6 条会被截断；真要 8 条就拆成两段，不要指望观众记住 8 行。",
    ),
    dict(
        id="C04",
        name="左右对撞",
        intent="两种做法/两个方案的正面对比，观众需要在同一屏上同时看到",
        kind="compare",
        dur=[4.0, 5.0, 7.0],
        sub="bottom",
        ease="inOut3",
        motion="soft",
        sfx=[(0.0, "whoosh")],
        avoid="不适合「我们 vs 他们」式贬低：对比要同维度，两边条目数尽量相等。",
    ),
    dict(
        id="C05",
        name="顺序推进",
        intent="有明确先后的 3–5 步，观众要看到「走到第几步了」",
        kind="process",
        dur=[4.0, 5.5, 8.0],
        sub="bottom",
        ease="out3",
        motion="soft",
        sfx=[(0.0, "whoosh")],
        avoid="步骤超过 5 个会被截断；长流程拆成多段，每段只推进几步。",
    ),
    dict(
        id="C06",
        name="金句停顿",
        intent="一句能被单独记住的话，说完之后要给观众留白",
        kind="statement",
        dur=[2.6, 3.4, 5.5],
        sub="none",
        ease="out5",
        motion="still",
        sfx=[],
        avoid="金句一多就不叫金句；全片不超过 2 张，且不要连着放。",
    ),
    dict(
        id="C07",
        name="引语出处",
        intent="要借第三方的嘴说话，且必须让人看到是谁说的",
        kind="quote",
        dur=[4.0, 5.0, 7.5],
        sub="bottom",
        ease="out3",
        motion="soft",
        sfx=[],
        avoid="出处不能省：没有出处的引语在本技能口径里等同于编造。",
    ),
    dict(
        id="C08",
        name="数据条",
        intent="3–6 项可比的量，要让人一眼看出谁高谁低",
        kind="chart",
        dur=[4.5, 5.5, 8.0],
        sub="bottom",
        ease="out3",
        motion="soft",
        sfx=[],
        avoid="量纲要一致（别把 % 和 万 放同一组）；序列 < 2 条会被绘制器退成 stat。",
    ),
    dict(
        id="C09",
        name="章节换气",
        intent="换章节时的一次呼吸，让观众知道「进入下一部分了」",
        kind="chapter",
        dur=[2.2, 2.8, 3.6],
        sub="none",
        ease="inOut3",
        motion="soft",
        sfx=[(0.0, "impact")],
        avoid="章节卡不要每段都放：连续两张会让片子变成 PPT 目录。",
    ),
    dict(
        id="C10",
        name="收尾落点",
        intent="最后一段，把全片收住，并留出署名/出处的位置",
        kind="outro",
        dur=[2.4, 3.0, 4.5],
        sub="none",
        ease="out5",
        motion="soft",
        sfx=[(0.0, "impact")],
        avoid="收尾字与片尾出处卡别抢同一个位置；有 credit 时先给 credit 留时间。",
    ),
]

_BY_ID = {c["id"]: c for c in CARDS}


def ids():
    """全部卡号（稳定顺序）。"""
    return [c["id"] for c in CARDS]


def get(cid):
    """按卡号取卡；没有返回 None（调用方决定报错还是跳过）。"""
    return _BY_ID.get(str(cid))


def for_intent(words):
    """按意图关键词粗筛候选卡（不选型，只缩小范围）。

    为什么不自动选：选卡是创作判断，机器只能帮人回忆"有哪几张卡"。
    这里只做关键词命中，返回按命中数排序的卡号列表。
    """
    if isinstance(words, str):
        words = [words]
    ws = [str(w).strip() for w in (words or []) if str(w).strip()]
    if not ws:
        return ids()
    scored = []
    for c in CARDS:
        hay = (c["name"] + " " + c["intent"] + " " + c["avoid"])
        hits = sum(1 for w in ws if w in hay)
        if hits:
            scored.append((-hits, c["id"]))
    return [cid for _n, cid in sorted(scored)]


def apply(cid, target):
    """把一张卡的建议写进 scene dict（只填**没写过**的键，不覆盖手写值）。

    为什么只填空的：卡是默认值，人是最终决定。覆盖手写的 type/dur 会让"我明明改了"
    变成玄学。返回实际写入了哪些键。
    """
    c = get(cid)
    if not c or not isinstance(target, dict):
        return dict(wrote=[], advice={})
    wrote = []
    if c["kind"] != "auto" and not target.get("type"):
        target["type"] = c["kind"]
        wrote.append("type")
    # 只写 film.json 真正接受的字段（lines[] 白名单见 filmcheck.LINE_SPEC）。
    # 卡里的时长是**建议值**，不回写：min_dur 属于 design.pacing.*（全片一个值），
    # 不在 lines[] 里——早先版本把它写进 scene dict，filmcheck 会当场报未知键。
    # 逐句想加镜头停留就写 hold（lines[].hold 是合法字段）。
    return dict(wrote=wrote, advice=dict(
        dur_suggest=c["dur"][1],
        dur_range=list(c["dur"]),
        subtitle=c["sub"],
        ease=c["ease"],
        motion=c["motion"],
        sfx=[dict(t=t, recipe=n) for t, n in c["sfx"]],
    ))


def table_markdown():
    """渲染成 Markdown 表（文档与 `--list` 共用一份真源，避免抄两遍抄飘）。"""
    out = ["| 卡号 | 名字 | 什么时候用 | 构图 | 长度（短/建议/长） | 字幕 | 动效 | 音效 |",
           "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for c in CARDS:
        sfx = "、".join("%.2fs %s" % (t, n) for t, n in c["sfx"]) or "无"
        out.append("| %s | %s | %s | `%s` | %.1f / %.1f / %.1f s | %s | %s / %s | %s |"
                   % (c["id"], c["name"], c["intent"], c["kind"],
                      c["dur"][0], c["dur"][1], c["dur"][2],
                      c["sub"], c["ease"], c["motion"], sfx))
    return "\n".join(out)


def pitfalls_markdown():
    """每张卡的误用清单（判例式：写清楚"这么做会怎样"）。"""
    out = []
    for c in CARDS:
        out.append("- **%s %s**：%s" % (c["id"], c["name"], c["avoid"]))
    return "\n".join(out)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="镜头配方卡：查卡 / 按意图找卡 / 套用到 scene")
    ap.add_argument("--list", action="store_true", help="打印全部卡")
    ap.add_argument("--intent", default="", help="按关键词找卡（逗号分隔）")
    ap.add_argument("--id", default="", help="单张卡的完整配置（JSON）")
    a = ap.parse_args(argv)
    if a.id:
        import json
        c = get(a.id)
        if not c:
            print("[没有这张卡] %s（可用：%s）" % (a.id, ", ".join(ids())))
            return 2
        print(json.dumps(c, ensure_ascii=False, indent=1))
        return 0
    if a.intent:
        words = [w.strip() for w in a.intent.split(",") if w.strip()]
        hits = for_intent(words)
        if not hits:
            print("[没有命中] 关键词 %r；用 --list 看全部 %d 张卡" % (words, len(CARDS)))
            return 2
        for cid in hits:
            c = get(cid)
            print("%s %s — %s" % (c["id"], c["name"], c["intent"]))
        return 0
    print(table_markdown())
    print("")
    print("常见误用：")
    print(pitfalls_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
