# -*- coding: utf-8 -*-
"""stagewatch.py — 阶段新鲜度（stale）守卫（v0.6.0）。

解决的是"改了一句台词，旧产物还在用，但不报错"这一类事故。

已经在 `_frames_plan_stamp` 里为一个点做过（改台词 -> 旧分块复用 -> 画面旁白对不上），
但其余 stage 全是裸奔：
  * 改 style 后 `--stage audio` 仍读上一轮的 work/music.wav（配乐是旧的）；
  * 改 hold 后 `--stage frames` 仍用旧 cues.json（音效落位错）；
  * 单独跑 `--stage audio` 会无条件混入已存在的 work/sfx.wav。

机制：每个 stage 结束时往 `work/manifest.json` 记一行
    {stage, at, inputs: {name: 指纹}, outputs: {name: 指纹}}
下次该 stage 开始前，重算**上游 inputs** 与**自己 outputs** 的指纹：
  * 上游变了 -> 本 stage 的输入过时（会拿旧输入算出新产物）；
  * 自己的 outputs 变了 -> 下游全部过时。
两者都只**警告 + 给出最小重跑命令**，默认不阻断（可用 --strict-stale 升级为失败，
--allow-stale 显式放行并留痕）。这是"门禁可以保守，但不能静默"的同一条原则。

指纹策略：小文件用 sha1（准），大文件用 size+mtime（快）。
100 MB 的 audio.wav 每 stage 都算 sha1 会让"跑一次 qa"变慢一倍，而它的目的只是
"发现它被人动过"，size+mtime 足够。
"""
from __future__ import annotations

import hashlib
import json
import os
import time

SCHEMA = 1
BIG_BYTES = 8 << 20          # > 8 MB 走 size+mtime 快路径

# 每个 stage 的输入与输出（相对 work/ 或绝对路径）。
# 顺序即依赖顺序：后面的 stage 用前面 stage 的产物。
# `sources` 是**源头输入**：剧本（film.json）与画面代码（scenes.py）。
# 早期版本只跟踪"stage 之间的产物"，于是"改了一句台词但没重跑 tts"查不出任何过时——
# 而那正是最需要它报警的场景（旧 tts 配新剧本 = 配音和字幕全错位）。
STAGE_IO = {
    "style":   dict(sources=["script"], inputs=[], outputs=["style.json", "voice_plan.json"]),
    "tts":     dict(sources=["script"], inputs=["style.json"], outputs=["tts.json"]),
    "layout":  dict(sources=["script"],
                    inputs=["tts.json", "style.json", "voice_plan.json"],
                    outputs=["timeline.json", "sub.srt", "sub.ass"]),
    "cues":    dict(sources=["script", "scenes"], inputs=["timeline.json"],
                    outputs=["cues.json"]),
    "score":   dict(sources=["script"], inputs=["timeline.json", "style.json"],
                    outputs=["music.wav", "score.json"]),
    "sfx":     dict(sources=["script"], inputs=["cues.json"], outputs=["sfx.wav"]),
    "audio":   dict(sources=["script"],
                    inputs=["tts.json", "timeline.json", "music.wav", "sfx.wav"],
                    outputs=["audio.wav"]),
    "frames":  dict(sources=["script", "scenes"],
                    inputs=["timeline.json", "audio.wav", "cues.json"],
                    outputs=["silent.mp4"]),
    "mux":     dict(sources=["script"], inputs=["silent.mp4", "audio.wav"], outputs=[]),
    "qa":      dict(sources=["script", "scenes"], inputs=[],
                    outputs=["gates.json", "qa/sheet.png"]),
    "stills":  dict(sources=["script", "scenes"], inputs=["timeline.json", "style.json"],
                    outputs=[]),
}
STAGE_ORDER = tuple(STAGE_IO)

# 每个 stage 过时后的"最小重跑命令"提示（只提示本 stage + 必要的下游）
RERUN_HINT = {
    "style":   "python render.py --stage style tts layout cues score sfx audio frames mux qa",
    "tts":     "python render.py --stage tts layout cues sfx audio frames mux qa",
    "layout":  "python render.py --stage layout cues score sfx audio frames mux qa",
    "cues":    "python render.py --stage cues sfx audio frames mux qa",
    "score":   "python render.py --stage score audio mux qa",
    "sfx":     "python render.py --stage sfx audio mux qa",
    "audio":   "python render.py --stage audio mux qa",
    "frames":  "python render.py --stage frames mux qa  （分块缓存会被指纹拒绝，需 rm -rf work/segments）",
    "mux":     "python render.py --stage mux qa",
    "qa":      "python render.py --stage qa",
    "stills":  "python render.py --stage stills",
}


def _fp(path, work):
    """文件指纹：缺失 -> 'missing'；>8MB 用 size+mtime（快），否则 sha1（准）。"""
    p = path if os.path.isabs(str(path)) else os.path.join(str(work), str(path))
    try:
        st = os.stat(p)
    except OSError:
        return "missing"
    if st.st_size > BIG_BYTES:
        return "sm:%d:%d" % (st.st_size, int(st.st_mtime))
    try:
        h = hashlib.sha1()
        with open(p, "rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        return "sha1:" + h.hexdigest()[:16]
    except OSError:
        return "missing"


def manifest_path(work):
    return os.path.join(str(work), "manifest.json")


def load(work):
    p = manifest_path(work)
    if not os.path.exists(p):
        return {"schema": SCHEMA, "stages": {}}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema": SCHEMA, "stages": {}, "broken": p}
    if not isinstance(d, dict) or not isinstance(d.get("stages"), dict):
        return {"schema": SCHEMA, "stages": {}, "broken": p}
    return d


def save(work, d):
    os.makedirs(str(work), exist_ok=True)
    p = manifest_path(work)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def _src_fp(kind, sources):
    """源头输入指纹：script=剧本（film.json）、scenes=场景代码（scenes.py）。"""
    p = (sources or {}).get(kind)
    return _fp(p, ".") if p else "missing"


def snapshot(work, stage, sources=None):
    """记下某个 stage 的输入/输出指纹（stage 结束时调用）。"""
    io = STAGE_IO.get(str(stage))
    if io is None:
        return None
    d = load(work)
    d.setdefault("stages", {})[str(stage)] = dict(
        at=time.time(),
        atText=time.strftime("%Y-%m-%d %H:%M:%S"),
        src={k: _src_fp(k, sources) for k in (io.get("sources") or [])},
        inputs={k: _fp(k, work) for k in io["inputs"]},
        outputs={k: _fp(k, work) for k in io["outputs"]},
    )
    save(work, d)
    return d["stages"][str(stage)]


def check(work, stage, sources=None):
    """stage 开始前查新鲜度：返回问题列表（每项含 stage / reason / rerun）。

    只报"这一跑真的会读到旧东西"的情况：
      1) 本 stage 的上游输入与上次记录不同（上游被重跑过，或人手改过文件）；
      2) 本 stage 自己的产物比它的输入还旧（上次跑到一半被中断）。
    另一个 stage 的产物变了但**不**是本周 stage 的输入时，不报（跟我们无关）。
    """
    io = STAGE_IO.get(str(stage))
    if io is None:
        return []
    d = load(work)
    stages = d.get("stages") or {}
    rec = stages.get(str(stage))
    issues = []
    if not isinstance(rec, dict):
        # 从没跑过这个 stage：只有当"别的 stage 有记录"（说明是接手别人的 work 目录，
        # 或这套产物是旧版本跑出来的）时才提示；全新目录第一次跑不刷噪声。
        present = [k for k in io["inputs"]
                   if _fp(k, work) != "missing"]
        # 只有"有声明产物"的阶段才谈得上"跑过但没记录"。stills / mux 的产物不在 work 里
        # （mux 写 --out，stills 写 work/stills/），无从证明跑过，就别冤枉它们过时。
        if stages and io["outputs"] and len(present) == len(io["inputs"]) and io["inputs"]:
            issues.append(dict(stage=str(stage), kind="no-manifest",
                               reason="work 里有 %s 但 manifest.json 没有本阶段的记录"
                                      "（旧版本跑过 / 手改过）" % "、".join(io["inputs"]),
                               rerun=RERUN_HINT.get(str(stage), "")))
        return issues
    # 源头先查：剧本 / scenes.py 改了但本 stage 没重跑 -> 产物一定过时
    old_src = rec.get("src") or {}
    for kind in (io.get("sources") or []):
        now = _src_fp(kind, sources)
        was = old_src.get(kind, "missing")
        if now != was:
            fname = {"script": "剧本 film.json", "scenes": "画面代码 scenes.py"}.get(kind, kind)
            issues.append(dict(
                stage=str(stage), kind="source-changed", file=kind,
                reason="%s 在本阶段上次跑完之后改过（%s -> %s），但本阶段没有重跑；"
                       "它现在装的还是旧内容" % (fname, was, now),
                rerun=RERUN_HINT.get(str(stage), ""),
            ))
    old_inputs = rec.get("inputs") or {}
    for name in io["inputs"]:
        now = _fp(name, work)
        was = old_inputs.get(name, "missing")
        if now != was:
            issues.append(dict(
                stage=str(stage), kind="input-changed", file=name,
                reason="输入 %s 在上次跑本阶段之后变了（%s -> %s），本次会拿**新输入**算，"
                       "但它下游的产物仍是旧的" % (name, was, now),
                rerun=RERUN_HINT.get(str(stage), ""),
            ))
    # 自己的产物丢了：上次跑到一半被中断 / 被删 / 被挪走（与"输入变了"是两件事，都要报）
    out_fps = {k: _fp(k, work) for k in (rec.get("outputs") or {})}
    missing_out = [k for k, v in out_fps.items() if v == "missing"]
    if missing_out:
        issues.append(dict(stage=str(stage), kind="output-missing",
                           reason="上次跑过本阶段，但产物 %s 不见了（被删 / 被挪 / 磁盘满）"
                                  % "、".join(missing_out),
                           rerun=RERUN_HINT.get(str(stage), "")))
    return issues


def explain(work, stages=None, sources=None):
    """--explain-stale：把"现在哪些阶段是旧的、为什么、该跑什么"一次说清。"""
    d = load(work)
    rows = []
    for st in (stages or STAGE_ORDER):
        io = STAGE_IO.get(st)
        if io is None:
            continue
        rec = (d.get("stages") or {}).get(st)
        issues = check(work, st, sources)
        rows.append(dict(stage=st, recorded=bool(rec),
                         at=(rec or {}).get("atText", ""),
                         issues=issues))
    return rows


def report(work, stages=None, strict=False, allow_stale=False, ledger=None, write=None,
           sources=None):
    """查 + 打印 + 判定。返回 (errors, warnings)；errors 在 strict 下非空。

    ledger: 传 gates.GateLedger 时把结论记进账本（一条汇总 gate）。
    write:  传 callable(str, str) 时按行输出（方便测试捕获）。
    """
    out = write or (lambda level, msg: print(msg))
    rows = explain(work, stages, sources)
    problems = [(r["stage"], i) for r in rows for i in r["issues"]]
    warns, errs = [], []
    for st, it in problems:
        msg = ("[stale] %s: %s" % (st, it["reason"]))
        if allow_stale:
            warns.append(msg + "（--allow-stale 已放行，本次结果可能掺旧数据）")
        elif strict:
            errs.append(msg)
        else:
            warns.append(msg)
        if it.get("rerun"):
            out("warn", "   最小重跑: %s" % it["rerun"])
    if problems:
        out("warn", "")
        out("warn", "   ↑ 共 %d 处过时。默认只告警；--strict-stale 让它失败，"
                    "--allow-stale 显式放行（会记进 QC 报告）。" % len(problems))
    if ledger is not None:
        try:
            if not problems:
                ledger.ok("stale", "阶段新鲜度", "上游输入与上次记录一致")
            elif errs:
                ledger.fail("stale", "阶段新鲜度", "%d 处过时（--strict-stale）" % len(problems))
            else:
                ledger.skip("stale", "阶段新鲜度",
                            "%d 处过时，已告警未阻断%s"
                            % (len(problems), "（--allow-stale）" if allow_stale else ""))
        except Exception:                                   # noqa: BLE001
            pass
    return errs, warns


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="阶段新鲜度检查：现在哪些产物是旧的")
    ap.add_argument("--work", default="work")
    ap.add_argument("--stage", default="", help="只看某个 stage（缺省 = 全部）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    stages = [a.stage] if a.stage else None
    rows = explain(a.work, stages)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        sys.exit(0)
    bad = 0
    for r in rows:
        mark = "OK " if not r["issues"] else "!! "
        if r["issues"]:
            bad += 1
        print("%s%-8s %s" % (mark, r["stage"], r["at"] or "（无记录）"))
        for it in r["issues"]:
            print("     -", it["reason"])
            if it.get("rerun"):
                print("       最小重跑:", it["rerun"])
    print()
    if bad:
        print("%d 个阶段过时。只想看某个：--stage <名字>；打印机读：--json" % bad)
        sys.exit(1)
    print("没有发现过时的阶段（按 manifest.json 记录判断）。")
