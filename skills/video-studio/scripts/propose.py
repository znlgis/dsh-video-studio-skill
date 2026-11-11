# -*- coding: utf-8 -*-
"""propose.py — 提案器（v1.0）：先出 N 个方案，用户确认后才允许出片。

为什么单独一个入口而不是塞进 render.py
--------------------------------------
render.py 的 13 个 stage 是一条"内容 → 成片"的生产线；提案是**生产之前**的决策环节，
它的产物（proposal.json）要能被 frames 阶段当成前置门禁来查。混进 STAGES 会把
"这条 stage 的输入输出是什么"搅乱（stagewatch 的依赖表是按生产链写的）。
所以：独立入口 + 独立产物 + 由 render.py 在 frames 前**只读校验**。

流程
----
    propose  ->  work/proposal.json (rev1, status=draft, N 个候选 + 静帧 + 成本)
    confirm  ->  status=confirmed, chosen="B"，并落 work/design.json（冻结）
    revise   ->  rev2（不覆盖 rev1，带 feedback 原文）
    diff     ->  两个 rev 的设计向量差异

确定性：同一 brief + 同一 seed 永远采样出同一组候选（designkit 内部 sha1 派生）。
离线：预览只走合成时间轴（previewkit），**不联网、不合成旁白**；--with-audio 才多生成
      12 秒样音（仍全程离线，musicgen/scorekit 任选其一）。

确认要绑两道指纹
----------------
confirm 除了 designHash（设计指纹），还写 scriptHash（剧本规范化 JSON 的 sha1 前 16 位）。
只绑设计不够：设计没变而剧本大改，字幕/断句/时长全变，那次确认等于作废。
proposal_state 因此多一档 stale——剧本对不上就要求重新提案；剧本读不出来（文件搬走、
不是合法 JSON）时**不判**，因为"算不了"不该变成一条假警报。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SCHEMA = 1
PROPOSAL_FILE = "proposal.json"
PROPOSALS_DIR = "proposals"
CANDIDATE_IDS = "ABCDEFGH"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _setup_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise SystemExit("[proposal] %s 不是合法 JSON：%s" % (path, e)) from e


def _atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(obj, n=16):
    return hashlib.sha1(_canonical(obj).encode("utf-8")).hexdigest()[:n]


def proposal_path(work):
    return os.path.join(str(work), PROPOSAL_FILE)


def rev_dir(work, rev):
    return os.path.join(str(work), PROPOSALS_DIR, "rev%d" % int(rev))


def _designkit():
    try:
        import designkit
    except ImportError as e:
        raise SystemExit(
            "[proposal] 缺少 designkit.py（%s）——它是设计 DSL 与采样器，必须与 propose.py 同目录"
            % e) from e
    return designkit


def _previewkit():
    try:
        import previewkit
    except ImportError as e:
        raise SystemExit(
            "[proposal] 缺少 previewkit.py（%s）——离线预览要它，必须与 propose.py 同目录"
            % e) from e
    return previewkit


def _script_hash_of(path):
    """剧本指纹：规范化 JSON 的 sha1 前 16 位；读不出（没有文件/不是 JSON/没有 lines）返回 None。

    "算不了就不判"是刻意的：剧本被搬走或临时写坏时，把 confirmed 判成 stale 会逼用户
    重新确认一件其实没变的事，久了就没人信这条门禁了。
    """
    if not path or not os.path.isfile(str(path)):
        return None
    try:
        script = _load_json(path, default=None)
    except SystemExit:
        return None
    if not isinstance(script, dict) or not isinstance(script.get("lines"), list):
        return None
    return _sha(script)


def _load_script(path):
    script = _load_json(path)
    if not isinstance(script, dict):
        raise SystemExit("[proposal] 剧本 %s 不是 JSON 对象" % path)
    if not script.get("lines"):
        raise SystemExit("[proposal] 剧本 %s 里没有 lines[]（提案至少需要一段内容）" % path)
    return script


# ---------------------------------------------------------------- 提案生成
def _brief_of(text, script):
    """把用户一句话变成 brief dict。剧本的标题是隐含 topic（避免空 brief）。"""
    text = str(text or "").strip()
    topic = text or str(script.get("title") or "").strip()
    if not topic:
        raise SystemExit("[proposal] 需要 --brief 或剧本里至少有 title（否则不知道在给什么题材做方案）")
    return {"topic": topic, "duration_target": None, "must_have": [], "avoid": []}


def _candidate_row(design, script, outdir, n_frames, previewkit, designkit,
                   with_audio=False):
    """跑一个候选的离线预览，返回 proposal.json 里的候选行。"""
    t0 = time.time()
    pv = previewkit.preview_candidate(design, script, outdir, n_frames=n_frames,
                                      with_audio=with_audio)
    secs = round(time.time() - t0, 2)
    pv["secs"] = secs
    preview = {"sheet": pv.get("sheet"), "frames": pv.get("frames"), "secs": secs}
    # 预览会按合成时长排轴，段太短时 grammar 会降级构图（chart -> list 等）；
    # 把降级信息带进提案，人和机器都能看出"哪一段预览里画的和成片不一样"。
    if pv.get("downgrades"):
        preview["downgrades"] = pv["downgrades"]
    if with_audio:
        # preview.audio 只放路径（契约字段）；引擎/耗时/失败原因分开写，
        # 是为了让人一眼看出"这份样音是谁出的、等了多久"，而不用去翻日志。
        preview["audio"] = pv.get("audio") or ""
        preview["audio_engine"] = pv.get("audio_engine") or ""
        preview["audio_secs"] = pv.get("audio_secs")      # 生成耗时
        preview["audio_dur"] = 12.0 if pv.get("audio") else None   # 音频长度（秒）
        if pv.get("audio_error"):
            preview["audio_error"] = pv["audio_error"]
    return {
        "id": pv.get("id"),
        "label": design.get("label") or design.get("id") or "",
        "pitch": design.get("pitch") or "",
        "design": design,
        "design_fp": designkit.design_fingerprint(design),
        "preview": preview,
        "cost": {"preview_s": secs},
        "diversity": None,   # 生成完之后统一算（需要两两比较）
    }


def _fill_diversity(rows, designkit):
    """两两算设计距离，把"跟谁像/不像"写进每个候选（伪多方案门禁的输入）。"""
    for i, a in enumerate(rows):
        vs = []
        for j, b in enumerate(rows):
            if i == j:
                continue
            d = designkit.design_distance(a["design"], b["design"])
            vs.append({"id": b["id"], "n": d.get("n", 0), "axes": d.get("axes") or []})
        rows[i]["diversity"] = {"vs": vs,
                                "min_n": min([v["n"] for v in vs]) if vs else 0}


def _designs_from_preset(preset_id, n=3, overrides=None):
    """以预设为起点生成 n 个候选：第 1 个就是预设本身，其余在它基础上扰动。"""
    import presetkit
    pr = presetkit.load_preset(preset_id)
    if pr is None:
        raise SystemExit("[proposal] 没有预设 %r；可用：%s"
                         % (preset_id, " / ".join(x["id"] for x in presetkit.list_presets())))
    base = presetkit.preset_to_design(pr, overrides=overrides)
    out = [base]
    import designkit
    for i in range(1, max(1, n)):
        d = json.loads(json.dumps(base))
        d["id"] = "%s-%d" % (base["id"], i + 1)
        d["label"] = "%s（变体 %d）" % (base["label"], i + 1)
        # 只扰动"结构签名"，配色保持预设的精确色值不变——用户选预设就是要那套配色
        cur = d["visual"]
        cur["motif"]["kind"] = designkit.MOTIFS[(designkit.MOTIFS.index(cur["motif"]["kind"]) + 3 * i) % len(designkit.MOTIFS)]
        cur["transition"]["kind"] = designkit.TRANSITIONS[(designkit.TRANSITIONS.index(cur["transition"]["kind"]) + 2 * i) % len(designkit.TRANSITIONS)]
        cur["layout"] = designkit.LAYOUTS[(designkit.LAYOUTS.index(cur["layout"]) + i) % len(designkit.LAYOUTS)]
        cur["typography"]["number"] = designkit.NUMBERS[(designkit.NUMBERS.index(cur["typography"]["number"]) + 2 * i) % len(designkit.NUMBERS)]
        out.append(d)
    return out


def make_proposal(brief_text, script, work, n=3, seed=None, n_frames=4, json_out=False,
                  preset="", with_audio=False):
    """生成一版提案（rev = 已有最大 rev + 1），落盘并打印人读摘要。"""
    designkit = _designkit()
    previewkit = _previewkit()
    brief = _brief_of(brief_text, script)

    prev = _load_json(proposal_path(work), default=None) or {}
    rev = int(prev.get("rev") or 0) + 1
    parent = prev.get("rev") if prev else None
    feedback = str(brief_text or "").strip() if rev > 1 else ""

    designs = (_designs_from_preset(preset, n=n)
               if str(preset or "").strip()
               else designkit.sample_designs(brief, n=n, seed=seed))
    # 认不出来的要求：采样器不会替你"悄悄放宽"，但也不会因此失败，而是挂在候选上。
    # 这里汇总一次，人读摘要里必须说出来——用户提的要求没生效，他得知道。
    unmatched = []
    for d in designs:
        for word in (d.get("_unmatched_constraints") or []):
            if word not in unmatched:
                unmatched.append(word)
    if len(designs) < 2:
        raise SystemExit("[proposal] 采样器只给了 %d 个候选（至少要 2 个才能算'多方案'）"
                         % len(designs))

    outdir = rev_dir(work, rev)
    os.makedirs(outdir, exist_ok=True)

    rows, t_all0 = [], time.time()
    for i, design in enumerate(designs):
        cid = CANDIDATE_IDS[i] if i < len(CANDIDATE_IDS) else "C%d" % (i + 1)
        design = dict(design)
        design.setdefault("id", "design-%s" % cid.lower())
        design["id"] = "%s-%s" % (cid.lower(), design["id"].split("-")[-1]) if "-" in design["id"] else "%s-%s" % (cid.lower(), design["id"])
        cdir = os.path.join(outdir, cid)
        os.makedirs(cdir, exist_ok=True)
        row = _candidate_row(design, script, cdir, n_frames, previewkit, designkit,
                             with_audio=with_audio)
        row["id"] = cid
        rows.append(row)
        if not json_out:
            print("   候选 %s  %-22s  %d 帧  %.2fs  -> %s"
                  % (cid, row["label"][:22], len(row["preview"]["frames"] or []),
                     row["preview"]["secs"], row["preview"]["sheet"]))

    _fill_diversity(rows, designkit)
    wall = round(time.time() - t_all0, 2)
    # 并排对比图必须**无条件**产出：之前它写在 `if not json_out` 分支里，于是
    # `--json` 调用（机读、被 CI 或上层脚本用）永远拿不到 _compare.png，
    # 而"人眼并排看 N 个方案"正是这一步最重要的产物。
    compare = _write_compare_sheet(rows, outdir)

    payload = {
        "schema": SCHEMA,
        "rev": rev,
        "parentRev": parent,
        "at": _now(),
        "status": "draft",
        "brief": brief,
        "feedback": feedback,
        "chosen": None,
        "seed": seed,
        "wall_s": wall,
        "candidates": rows,
    }
    path = proposal_path(work)
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=1))
    # 每版留一份快照（rev 不覆盖）
    _atomic_write(os.path.join(outdir, "proposal.json"),
                  json.dumps(payload, ensure_ascii=False, indent=1))

    if not json_out:
        print()
        print("== 提案 rev%d：%d 个候选，总耗时 %.2fs" % (rev, len(rows), wall))
        print("   %-3s %-22s %-9s %-8s %s" % ("id", "立意", "静帧", "距离", "状态"))
        for r in rows:
            print("   %-3s %-22s %8.2fs %-8d %s"
                  % (r["id"], (r["pitch"] or r["label"])[:22], r["preview"]["secs"],
                     r["diversity"]["min_n"],
                     "OK" if r["diversity"]["min_n"] >= 2 else "!! 与某候选太像"))
        if with_audio:
            print()
            print("   样音（12 秒 / 48 kHz 立体声）:")
            for r in rows:
                pv = r["preview"]
                if pv.get("audio"):
                    print("     %s  %-9s 生成 %4.2fs  %s"
                          % (r["id"], pv.get("audio_engine") or "?",
                             pv.get("audio_secs") or 0.0, pv["audio"]))
                else:
                    print("     %s  没生成：%s" % (r["id"], pv.get("audio_error") or "未知原因"))
        if unmatched:
            print()
            print("   ! 这些要求没生效（词表里没有，采样器没有照做）：%s"
                  % "、".join("「%s」" % w for w in unmatched))
        print()
        print("   联系表（并排对比）: %s" % compare)
        print("   确认其中一个: python propose.py --confirm --candidate %s" % rows[0]["id"])
        print("   都不满意:     python propose.py --revise \"你的要求\"")
    else:
        # 机读调用（CI / 上层脚本）拿不到任何东西的话，--json 就只是"关掉人读输出"，
        # 这跟参数名承诺的不是一回事；payload 本身就是这一版提案的全部事实。
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return payload


def _write_compare_sheet(rows, outdir):
    """把 N 个候选的联系表纵向拼成一张对比图（人一眼能看全）。"""
    try:
        from PIL import Image
    except ImportError:
        return "（没装 Pillow，跳过并排对比图）"
    paths = [r["preview"]["sheet"] for r in rows if r.get("preview", {}).get("sheet")]
    paths = [p for p in paths if p and os.path.exists(p)]
    if len(paths) < 2:
        return "（候选联系表不足 2 张，跳过并排对比图）"
    from PIL import ImageDraw
    # 中文标签必须用项目自己的字体链（PIL 默认位图字体没有中文字形，会渲成方块）。
    try:
        import filmkit as _K
        label_font = _K.font(19, "reg", "sans")
    except Exception:                                       # noqa: BLE001
        label_font = None
    ims = [Image.open(p).convert("RGB") for p in paths]
    tagged = [r for r in rows if r.get("preview", {}).get("sheet") in paths]
    w = max(i.size[0] for i in ims)
    band = 34   # 每行顶部加一条标签带：不标出来就看不出哪一行是哪个候选
    h = sum(i.size[1] for i in ims) + band * len(ims)
    sheet = Image.new("RGB", (w, h), (12, 12, 14))
    draw = ImageDraw.Draw(sheet)
    y = 0
    for im, r in zip(ims, tagged):
        draw.text((16, y + 7), "%s  %s  —  %s"
                  % (r.get("id"), r.get("label") or "", (r.get("pitch") or "")[:60]),
                  fill=(226, 232, 240), font=label_font)
        y += band
        sheet.paste(im, (0, y))
        y += im.size[1]
    out = os.path.join(outdir, "_compare.png")
    sheet.save(out)
    return out


# ---------------------------------------------------------------- 确认 / 修订
def confirm(work, candidate, by="user", note="", json_out=False,
            script=None, script_path=""):
    prev = _load_json(proposal_path(work), default=None)
    if not prev:
        raise SystemExit("[proposal] 没有 %s——先跑一次提案" % proposal_path(work))
    cands = {str(c.get("id")): c for c in (prev.get("candidates") or [])}
    cid = str(candidate or "").strip().upper()
    if cid not in cands:
        raise SystemExit("[proposal] 没有候选 %r；本版有：%s"
                         % (candidate, " / ".join(sorted(cands))))
    design = cands[cid]["design"]
    designkit = _designkit()
    bad = designkit.validate_design(design)
    if bad:
        raise SystemExit("[proposal] 候选 %s 的设计没过校验，不能确认：\n  - %s"
                         % (cid, "\n  - ".join(bad)))
    prev["status"] = "confirmed"
    prev["chosen"] = cid
    prev["confirmedAt"] = _now()
    prev["confirmedBy"] = str(by or "user")
    prev["confirmNote"] = str(note or "")
    prev["designHash"] = designkit.design_fingerprint(design)
    # 只绑设计不够：设计没变而剧本大改，断句/时长/字幕全变，那次确认等于作废。
    # 只有调用方**显式**把剧本递进来才绑；没剧本可绑时必须清掉旧值，
    # 否则旧版本留下的指纹会在下次提案后误判成 stale。
    if isinstance(script, dict) and script.get("lines"):
        prev["scriptHash"] = _sha(script)
        prev["scriptPath"] = os.path.abspath(str(script_path)) if script_path else ""
    else:
        prev.pop("scriptHash", None)
        prev.pop("scriptPath", None)
    _atomic_write(proposal_path(work), json.dumps(prev, ensure_ascii=False, indent=1))
    _atomic_write(os.path.join(str(work), "design.json"),
                  json.dumps(design, ensure_ascii=False, indent=1))
    if not json_out:
        print("== 已确认候选 %s（%s）" % (cid, cands[cid].get("label")))
        if prev.get("scriptHash"):
            print("   剧本指纹: %s（剧本改了要重新提案）" % prev["scriptHash"])
        print("   冻结设计: %s" % os.path.join(str(work), "design.json"))
        print("   现在可以: python render.py --script <film.json> --stage all")
    return prev

def revise(work, feedback, script_path, n=3, seed=None, n_frames=4, json_out=False,
           preset="", with_audio=False):
    """不满意就重出：新 rev 不覆盖旧 rev，feedback 原文留存（这就是"迭代"的审计）。"""
    if not str(feedback or "").strip():
        raise SystemExit("[proposal] --revise 需要一段要求（例如：更冷一点、女声、节奏快些）")
    prev = _load_json(proposal_path(work), default=None)
    if not prev:
        raise SystemExit("[proposal] 没有提案可改——先跑一次提案")
    script = _load_script(script_path)
    return make_proposal(str(feedback), script, work, n=n, seed=seed,
                         n_frames=n_frames, json_out=json_out,
                         preset=preset, with_audio=with_audio)


def diff(work, rev_a, rev_b, json_out=False):
    dfk = _designkit()
    pa = _load_json(os.path.join(str(work), PROPOSALS_DIR, "rev%d" % int(rev_a), "proposal.json"),
                    default=None)
    pb = _load_json(os.path.join(str(work), PROPOSALS_DIR, "rev%d" % int(rev_b), "proposal.json"),
                    default=None)
    if not pa or not pb:
        raise SystemExit("[proposal] 找不到 rev%d 或 rev%d 的快照（work/%s/rev*/proposal.json）"
                         % (rev_a, rev_b, PROPOSALS_DIR))
    out = {"revA": int(rev_a), "revB": int(rev_b), "pairs": []}
    for ca in pa.get("candidates") or []:
        row = {"id": ca.get("id"), "label": ca.get("label"), "vs": []}
        for cb in pb.get("candidates") or []:
            d = dfk.design_distance(ca["design"], cb["design"])
            row["vs"].append({"id": cb.get("id"), "n": d.get("n", 0),
                              "axes": d.get("axes") or []})
        out["pairs"].append(row)
    if json_out:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return out
    print("== rev%d vs rev%d 的设计差异（n = 不同的设计维度数）" % (int(rev_a), int(rev_b)))
    for row in out["pairs"]:
        cells = "  ".join("%s:n=%d" % (v["id"], v["n"]) for v in row["vs"])
        print("   %s %-20s %s" % (row["id"], (row["label"] or "")[:20], cells))
    return out


def show(work, which="", json_out=False):
    prev = _load_json(proposal_path(work), default=None)
    if not prev:
        raise SystemExit("[proposal] 没有提案：%s" % proposal_path(work))
    if json_out:
        print(json.dumps(prev, ensure_ascii=False, indent=1))
        return prev
    print("== 提案 rev%d  status=%s  chosen=%s  时间=%s"
          % (prev.get("rev"), prev.get("status"), prev.get("chosen"), prev.get("at")))
    print("   brief: %s" % (prev.get("brief") or {}).get("topic"))
    if prev.get("scriptHash"):
        print("   剧本指纹: %s" % prev["scriptHash"])
    if prev.get("feedback"):
        print("   feedback: %s" % prev["feedback"])
    print()
    for r in prev.get("candidates") or []:
        if which and str(r.get("id")).upper() != str(which).upper():
            continue
        print("   [%s] %s" % (r.get("id"), r.get("pitch") or r.get("label")))
        print("       设计: %s" % (r.get("design_fp") or ""))
        print("       静帧: %s (%s 帧, %.2fs)"
              % ((r.get("preview") or {}).get("sheet"),
                 len((r.get("preview") or {}).get("frames") or []),
                 (r.get("preview") or {}).get("secs") or 0))
        pv = r.get("preview") or {}
        if pv.get("audio"):
            print("       样音: %s (%s, 生成 %.2fs)"
                  % (pv["audio"], pv.get("audio_engine") or "?", pv.get("audio_secs") or 0))
        elif pv.get("audio_error"):
            print("       样音: 没生成（%s）" % pv["audio_error"])
        div = r.get("diversity") or {}
        if div.get("vs"):
            print("       与其它候选: %s"
                  % ", ".join("%s(n=%d)" % (v["id"], v["n"]) for v in div["vs"]))
    return prev


# ---------------------------------------------------------------- 门禁接口（给 render.py 用）
def proposal_state(work, script_path=None):
    """给 render.py 的一行判定：返回 (status, detail)。

    status ∈ {"absent", "draft", "confirmed", "stale", "broken"}
      absent    —— 没提案（未启用，老项目就是这个）
      draft     —— 有提案但还没确认
      confirmed —— 已确认，且 design.json 与确认时的 designHash 一致、剧本也没换
      stale     —— 确认过，但 design.json 被改过（指纹对不上），或剧本改过了
      broken    —— 文件在但坏了

    script_path 可省：省略时按 confirm 时记下的 scriptPath 找，再退到 work/film.json。
    找不到/读不出剧本就**不判**剧本这一档（见 _script_hash_of），只判设计指纹。
    """
    p = proposal_path(work)
    data = _load_json(p, default=None)
    if data is None:
        return "absent", "没有 %s" % p
    if not isinstance(data, dict):
        return "broken", "%s 不是 JSON 对象" % p
    if data.get("status") != "confirmed" or not data.get("chosen"):
        return "draft", "rev%s 还没确认（chosen=%r）" % (data.get("rev"), data.get("chosen"))
    design = _load_json(os.path.join(str(work), "design.json"), default=None)
    if design is None:
        return "stale", "confirmed 但 design.json 不存在（重新 --confirm）"
    try:
        dfk = _designkit()
        got = dfk.design_fingerprint(design)
    except SystemExit:
        raise
    if str(data.get("designHash") or "") != str(got):
        return "stale", "design.json 的指纹 %s 与确认时 %s 不一致" % (
            got, data.get("designHash"))

    # 剧本这一档只在确认时显式传过 script 才存在（scriptHash 有值才判）。
    sh = str(data.get("scriptHash") or "")
    if sh:
        cand_path = str(script_path or data.get("scriptPath") or "")
        if not cand_path:
            fallback = os.path.join(str(work), "film.json")
            cand_path = fallback if os.path.isfile(fallback) else ""
        now = _script_hash_of(cand_path)
        if now is not None and now != sh:
            return "stale", ("剧本改过了（%s 的指纹 %s -> %s），需重新提案"
                             % (cand_path, sh, now))
    return "confirmed", "候选 %s（rev%d）" % (data.get("chosen"), data.get("rev"))


# ---------------------------------------------------------------- CLI
def main(argv=None):
    _setup_stdio()
    ap = argparse.ArgumentParser(description="video-studio 提案器：先出方案，确认后再出片")
    ap.add_argument("--work", default="work")
    ap.add_argument("--script", default="film.json")
    ap.add_argument("--brief", default="", help="一句话题材/要求（首次提案用）")
    ap.add_argument("--n", type=int, default=3, help="候选数（默认 3，最少 2）")
    ap.add_argument("--seed", default=None, help="采样种子（同一 seed 结果可复现）")
    ap.add_argument("--n-frames", type=int, default=4, help="每个候选渲几张静帧")
    ap.add_argument("--list", action="store_true", help="列出当前提案")
    ap.add_argument("--show", nargs="?", const="", default=None, help="看单个候选（给候选 id）")
    ap.add_argument("--confirm", action="store_true", help="确认某个候选（配 --candidate）")
    ap.add_argument("--candidate", default="", help="要确认的候选 id（A/B/C…）")
    ap.add_argument("--by", default="user")
    ap.add_argument("--note", default="")
    ap.add_argument("--revise", default=None, help="带一段要求重出方案（新 rev，不覆盖旧的）")
    ap.add_argument("--preset", default="",
                    help="以某套预设为起点（presets/<id>.json，见 python presetkit.py）："
                         "预设只提供结构签名，不参与自动选型")
    ap.add_argument("--diff", nargs=2, type=int, metavar=("REV_A", "REV_B"),
                    help="看两版设计的差异")
    ap.add_argument("--with-audio", action="store_true",
                    help="给每个候选附一段 12 秒样音（离线；配乐由 musicgen 生成）")
    ap.add_argument("--json", action="store_true", help="stdout 只输出 JSON")
    a = ap.parse_args(argv)

    try:
        if a.diff:
            diff(a.work, a.diff[0], a.diff[1], json_out=a.json)
            return 0
        if a.confirm:
            if not a.candidate:
                raise SystemExit("[proposal] --confirm 需要 --candidate A/B/C")
            # 剧本存在才绑指纹：--confirm 不该因为 film.json 写坏或不在就失败，
            # 但能绑就必须绑，否则"设计没变、剧本改了"会被当成已确认。
            script, spath = None, ""
            try:
                if os.path.isfile(a.script):
                    script, spath = _load_script(a.script), a.script
            except SystemExit:
                script, spath = None, ""
            confirm(a.work, a.candidate, by=a.by, note=a.note, json_out=a.json,
                    script=script, script_path=spath)
            return 0
        if a.show is not None:
            show(a.work, a.show, json_out=a.json)
            return 0
        if a.list:
            show(a.work, "", json_out=a.json)
            return 0
        if a.revise is not None:
            revise(a.work, a.revise, a.script, n=a.n, seed=a.seed,
                   n_frames=a.n_frames, json_out=a.json,
                   preset=a.preset, with_audio=a.with_audio)
            return 0
        if a.brief or os.path.exists(a.script):
            script = _load_script(a.script)
            make_proposal(a.brief, script, a.work, n=a.n, seed=a.seed,
                          n_frames=a.n_frames, json_out=a.json,
                          preset=a.preset, with_audio=a.with_audio)
            return 0
        ap.print_help()
        return 0
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit("[proposal] %s: %s" % (type(e).__name__, e)) from e


if __name__ == "__main__":
    sys.exit(main())
