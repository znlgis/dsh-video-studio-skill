# -*- coding: utf-8 -*-
"""render.py — 一条命令从剧本到成片（v0.2.0）。

    python render.py --script film.json --scenes scenes.py --out out/film.mp4 [--stage all]

阶段（v0.2 起真正互相独立——只做该阶段的事，不顺手重渲全片）：
    tts     逐句合成旁白 + 实测时长（带输入校验的缓存）
    layout  由实测时长排时间轴 -> timeline.json / sub.srt / sub.ass
    audio   采样级混音 -> audio.wav
    frames  逐帧渲染 -> silent.mp4（--jobs N 分块并行、--resume 断点续跑）
    mux     合成（视频流 copy，两遍 loudnorm）
    qa      质检 + 抽帧 + 联系表
    stills  只渲染若干关键帧做母版预览（不生成视频）

依赖顺序：tts -> layout -> audio -> frames -> mux -> qa。
--stage all 按顺序跑完；单跑某个 stage 时，若前置产物缺失会明确报错并提示先跑哪个。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import filmkit as K

STAGES = ("tts", "layout", "audio", "frames", "mux", "qa", "stills", "all")


# ---------------------------------------------------------------- 装载
def load_module(path: str, name="film_scenes"):
    """每次都用独立模块名 + 全新 module 对象，避免跨片污染（历史坑：渲染出上一部片）。"""
    path = str(Path(path).resolve())
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_script(p) -> dict:
    p = Path(p).resolve()
    s = json.load(open(p, encoding="utf-8"))
    for req in ("title", "lines"):
        if req not in s:
            raise SystemExit("film.json 缺少字段: " + req)
    for i, ln in enumerate(s["lines"]):
        ln.setdefault("id", "l%02d" % (i + 1))
        ln.setdefault("sub", ln["text"])
    s["_path"] = str(p)
    s["_dir"] = str(p.parent)
    return s


def _need(cond, msg):
    if not cond:
        raise SystemExit("[前置产物缺失] " + msg)


# ---------------------------------------------------------------- TTS
def st_tts(script, work, force=False):
    lines = script["lines"]
    print("== TTS (%d 句)" % len(lines))
    if force:
        shutil.rmtree(os.path.join(work, "tts"), ignore_errors=True)
    done = K.tts_batch(lines, work,
                       voice=script.get("voice", "zh-CN-YunxiNeural"),
                       rate=script.get("rate", "+0%"),
                       pitch=script.get("pitch", "+0Hz"),
                       workers=int(script.get("tts_workers", 4)),
                       trim=bool(script.get("trim_silence", True)))
    json.dump(done, open(os.path.join(work, "tts.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    total = sum(float(x["dur"]) for x in done)
    print("   语音总时长 %.2fs / %d 句" % (total, len(done)))
    return done


# ---------------------------------------------------------------- 排版
def st_layout(script, work, lines=None):
    lines = lines or json.load(open(os.path.join(work, "tts.json"), encoding="utf-8"))
    print("== Layout")
    tl = K.build_timeline(lines,
                          lead=script.get("lead", 0.55),
                          tail=script.get("tail", 0.85),
                          min_dur=script.get("min_dur", 3.0),
                          tail_map=script.get("tail_map"),
                          max_dur=script.get("max_dur"))
    tl["title"] = script["title"]
    tl["voice"] = script.get("voice", "zh-CN-YunxiNeural")
    tl["crf"] = script.get("crf", 18)
    tl["preset"] = script.get("preset", "medium")
    json.dump(tl, open(os.path.join(work, "timeline.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    n_srt = K.write_srt(tl["scenes"], os.path.join(work, "sub.srt"))
    n_ass = K.write_ass(tl["scenes"], os.path.join(work, "sub.ass"))
    print("   总时长 %.2fs / %d 帧 / %d 段 / srt %d 条 / ass %d 条"
          % (tl["total"], tl["frames"], len(tl["scenes"]), n_srt, n_ass))
    # 安全区自查：字幕一旦越界，mux 前就能发现
    safe_errs = K.qa_text_safe(tl["scenes"])
    if safe_errs:
        print("   [WARN] 字幕安全区:", safe_errs[:3])
    # 字形自查：缺字符会被画成方块
    glyph_errs = K.check_glyphs(script["lines"] + tl["scenes"])
    if glyph_errs:
        print("   [WARN] 缺字形（会显示为方块）:")
        for lid, fld, chars in glyph_errs[:8]:
            print("     %-6s %-7s %r" % (lid, fld, chars))
    for s in tl["scenes"]:
        print("   %-8s %7.2f-%7.2f  %s" % (s["id"], s["start"], s["end"], s["text"][:40]))
    return tl


# ---------------------------------------------------------------- 混音
def st_audio(tl, work, script):
    print("== Audio")
    total = tl["total"]
    bus = K.AudioBus(total)
    # 1) 旁白
    for s in tl["scenes"]:
        pcm = K.decode_pcm(s["path"])
        bus.place(pcm, s["voice_start"], gain=script.get("voice_gain", 1.0),
                  fade_in=0.02, fade_out=0.06)
    # 2) 配乐：连续床（交叉淡化），不用逐段硬拼
    mus = script.get("music") or {}
    if mus.get("enabled", True) and mus.get("chords"):
        bus.music_bed(mus["chords"],
                      seg=float(mus.get("seg", 4.0)),
                      gain=float(mus.get("gain", 0.9)),
                      amp=float(mus.get("amp", 0.055)),
                      xfade=float(mus.get("xfade", 1.2)))
    # 3) 转场音效
    if script.get("whoosh", True):
        for s in tl["scenes"][1:]:
            if float(s["start"]) < 0.2:
                continue
            bus.place(K.whoosh(0.45, amp=float(script.get("whoosh_amp", 0.06))),
                      max(0.0, s["start"] - 0.12), gain=0.7)
    out = os.path.join(work, "audio.wav")
    samples = bus.mix()
    peak = K.wav_write(out, samples)
    print("   audio.wav %.2fs  峰值 %.3f" % (K.wav_dur(out), peak))
    return out


# ---------------------------------------------------------------- 渲染
def _render_range(tl, mod, script, f0, f1, out_path, crf, preset, progress=True):
    """渲染 [f0, f1) 帧区间到 out_path。纯函数：同一帧号永远画出同一张图。"""
    scenes = tl["scenes"]
    total = tl["total"]
    sink = K.FrameSink(out_path, crf=crf, preset=preset)
    idx = 0
    t_start = time.time()
    n = max(1, f1 - f0)
    last_rep = 0
    for fi in range(f0, f1):
        t = fi / float(tl["fps"])
        while idx + 1 < len(scenes) and t >= scenes[idx + 1]["start"]:
            idx += 1
        s = scenes[idx]
        lt = t - s["start"]
        frame = mod.draw_frame(s, lt, s["dur"], t, script, fi)
        if frame is None:
            raise RuntimeError("场景 %s 在 t=%.2fs 返回 None" % (s.get("id"), t))
        frame = K.draw_subtitle(frame, K.sub_at(scenes, t, script.get("sub_max_hold")))
        fade = 1.0
        if t < 0.5:
            fade *= K.ease_out(t / 0.5)
        if t > total - 0.8:
            fade *= max(0.0, (total - t) / 0.8)
        if fade < 0.999:
            frame = K.Image.blend(K.Image.new("RGB", (K.W, K.H), (0, 0, 0)), frame, fade)
        sink.write(frame)
        if progress and fi - last_rep >= 300:
            last_rep = fi
            el = max(1e-6, time.time() - t_start)
            fps = (fi - f0 + 1) / el
            print("   %d/%d  %.1f fps  eta %.0fs" % (fi + 1, tl["frames"], fps,
                                                     (f1 - fi - 1) / max(fps, 1e-6)), flush=True)
    return sink.close()


def _render_chunk_worker(args):
    """分块渲染工作单元（供 multiprocessing 调用；函数必须模块级可 pickle）。"""
    (tl_path, scenes_path, script_path, f0, f1, out_path, crf, preset) = args
    tl = json.load(open(tl_path, encoding="utf-8"))
    script = json.load(open(script_path, encoding="utf-8"))
    script["_total"] = tl["total"]
    mod = load_module(scenes_path, "film_scenes_chunk_%d" % f0)
    return _render_range(tl, mod, script, f0, f1, out_path, crf, preset, progress=False)


def st_frames(tl, work, scenes_mod, script, jobs=1, resume=True):
    print("== Frames")
    script = dict(script)
    script["_total"] = tl["total"]           # 场景算全局进度条要用
    crf = int(script.get("crf", 18))
    preset = script.get("preset", "medium")
    total_frames = tl["frames"]

    if int(jobs) <= 1:
        silent = os.path.join(work, "silent.mp4")
        # 断点续跑：顶层 single 渲染不做帧级续跑（用分块模式才有意义）
        _render_range(tl, scenes_mod, script, 0, total_frames, silent, crf, preset)
        return silent

    # ---- 分块并行：按绝对起始帧命名，便于缺口检测 ----
    seg_dir = os.path.join(work, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    jobs = max(1, int(jobs))
    chunk = int(script.get("chunk_frames") or 0) or \
        int(round(total_frames / float(jobs) / float(tl["fps"]) / 4.0)) * 4 * int(tl["fps"])
    chunk = max(int(tl["fps"]) * 2, int(round(chunk / int(tl["fps"]))) * int(tl["fps"]))
    plan = []
    f = 0
    while f < total_frames:
        f1 = min(total_frames, f + chunk)
        plan.append((f, f1))
        f = f1
    print("   分块: %d 段 x 约 %d 帧，并行 %d" % (len(plan), chunk, jobs))

    script_path = os.path.join(work, "_script_snapshot.json")
    json.dump(script, open(script_path, "w", encoding="utf-8"), ensure_ascii=False)
    tl_path = os.path.join(work, "timeline.json")

    todo = []
    for (f0, f1) in plan:
        seg = os.path.join(seg_dir, "seg_%06d_%06d.mp4" % (f0, f1))
        if resume and os.path.exists(seg) and os.path.getsize(seg) > 1024:
            print("   skip 段 %d-%d（已存在）" % (f0, f1))
            continue
        todo.append((tl_path, str(Path(script["_dir"]) / script.get("_scenes", "scenes.py")),
                     script_path, f0, f1, seg, crf, preset))

    if todo:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        ctx = mp.get_context("spawn")
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as ex:
            futs = {ex.submit(_render_chunk_worker, a): a for a in todo}
            done = 0
            for fu in futs:
                fu.result()
                done += 1
                print("   段完成 %d/%d  %.0fs" % (done, len(todo), time.time() - t0), flush=True)

    # ---- 缺口检测 + 拼接 ----
    parts = []
    missing = []
    for (f0, f1) in plan:
        seg = os.path.join(seg_dir, "seg_%06d_%06d.mp4" % (f0, f1))
        if os.path.exists(seg) and os.path.getsize(seg) > 1024:
            parts.append(seg)
        else:
            missing.append((f0, f1))
    if missing:
        raise RuntimeError("分块缺口，不许拼接：%s" % missing[:5])
    silent = os.path.join(work, "silent.mp4")
    K.concat_segments(parts, silent, workdir=work)
    got = K.media_dur(silent)
    want = total_frames / float(tl["fps"])
    if abs(got - want) > 0.5:
        raise RuntimeError("拼接后时长 %.2fs != 预期 %.2fs" % (got, want))
    print("   silent.mp4 %.2fs（%.2f 帧当量）" % (got, got * float(tl["fps"])))
    return silent


# ---------------------------------------------------------------- 合成 / 质检
def st_mux(silent, audio, out, script):
    print("== Mux")
    K.mux(silent, audio, out, crf=int(script.get("crf", 18)),
          preset=script.get("preset", "medium"),
          reencode_video=bool(script.get("reencode_video", False)))
    print("   ->", out)
    return out


def st_qa(film, tl, work, scenes_mod=None, script=None):
    print("== QA")
    shots, refs = [], []
    fracs = (0.08, 0.3, 0.55, 0.78)
    for frac in fracs:
        sec = tl["total"] * frac
        p = os.path.join(work, "qa", "snap_%02d.png" % int(frac * 100))
        K.snap(film, sec, p)
        shots.append(p)
        # 参考帧：用场景函数重新渲染同一时刻，而不是拿成片自己抽的帧
        if scenes_mod is not None and script is not None:
            idx = 0
            for i, s in enumerate(tl["scenes"]):
                if sec >= s["start"]:
                    idx = i
            s = tl["scenes"][idx]
            try:
                fr = scenes_mod.draw_frame(s, sec - s["start"], s["dur"], sec, script,
                                           int(round(sec * tl["fps"])))
                fr = K.draw_subtitle(fr, K.sub_at(tl["scenes"], sec, script.get("sub_max_hold")))
                refs.append(fr)
            except Exception as e:      # noqa: BLE001
                print("   [WARN] 参考帧渲染失败:", e)

    ref_img = refs[len(refs) // 2] if len(refs) >= 3 else (refs[0] if refs else None)
    ref_sec = tl["total"] * fracs[len(fracs) // 2]
    rep = K.qa_report(film, expect_frames=tl["frames"], ref_img=ref_img,
                      ref_sec=ref_sec, expected_subtitles=len(tl["scenes"]),
                      notes=os.path.join(work, "sub.srt"))
    pr = rep["probe"]
    f = pr.get("format", {})
    print("   时长 %.2fs  大小 %.1f MB  码率 %.2f Mbps" % (
        float(f.get("duration", 0)), int(f.get("size", 0)) / 1048576,
        int(f.get("bit_rate", 0)) / 1e6))
    print("   响度", rep["loudness"])
    safe = K.qa_text_safe(tl["scenes"])
    print("   字幕安全区:", "OK" if not safe else safe[:2])
    sheet = K.contact_sheet(shots, os.path.join(work, "qa", "sheet.png"))
    if sheet:
        print("   联系表:", sheet, "  <- 必须亲眼看")
    for w in rep["warnings"]:
        print("   [WARN]", w)
    if rep["errors"]:
        print("   [FAIL]")
        for e in rep["errors"]:
            print("     -", e)
    else:
        print("   [PASS] 质检通过")
    return rep["errors"]


# ---------------------------------------------------------------- 母版预览
def st_stills(tl, work, scenes_mod, script, specs=None):
    """只渲染若干关键帧 -> out/stills/*.png + 联系表。母版阶段必跑。

    specs: ["l01:3.0", ...] 或 [[id, t_local], ...]；缺省为每段中点各一张。
    """
    print("== Stills")
    script = dict(script)
    script["_total"] = tl["total"]
    outdir = os.path.join(work, "stills")
    os.makedirs(outdir, exist_ok=True)
    by_id = {s["id"]: s for s in tl["scenes"]}
    if not specs:
        specs = [[s["id"], s["dur"] * 0.55] for s in tl["scenes"]]
    made = []
    for spec in specs:
        if isinstance(spec, str):
            sid, _, lt = spec.partition(":")
            lt = float(lt or 0.0)
        else:
            sid, lt = spec[0], float(spec[1])
        s = by_id.get(sid)
        if not s:
            print("   [skip] 没有这个场景:", sid)
            continue
        tg = s["start"] + float(lt)
        fr = scenes_mod.draw_frame(s, float(lt), s["dur"], tg, script,
                                   int(round(tg * tl["fps"])))
        fr = K.draw_subtitle(fr, K.sub_at(tl["scenes"], tg, script.get("sub_max_hold")))
        p = os.path.join(outdir, "%s_%05.1f.png" % (sid, float(lt)))
        fr.save(p)
        made.append(p)
    if made:
        cols = 6 if len(made) >= 6 else len(made)
        sheet = K.contact_sheet(made, os.path.join(outdir, "_sheet.png"), cols=cols, thumb_w=420)
        print("   %d 张静帧 -> %s" % (len(made), outdir))
        print("   联系表:", sheet, "  <- 必须亲眼看")
    return made


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="video-studio 编排器")
    ap.add_argument("--script", default="film.json")
    ap.add_argument("--scenes", default="scenes.py")
    ap.add_argument("--out", default="out/film.mp4")
    ap.add_argument("--work", default="work")
    ap.add_argument("--stage", default="all", choices=STAGES)
    ap.add_argument("--jobs", type=int, default=1, help="分块并行渲染进程数（frames 阶段）")
    ap.add_argument("--resume", action="store_true", help="复用已存在的分块")
    ap.add_argument("--force-tts", action="store_true", help="丢弃 TTS 缓存重新合成")
    ap.add_argument("--stills", default="", help="stills 阶段用：逗号分隔的 id:秒")
    a = ap.parse_args()

    os.makedirs(a.work, exist_ok=True)
    script = load_script(a.script)
    script["_scenes"] = str(Path(a.scenes).resolve())
    tl_p = os.path.join(a.work, "timeline.json")
    tts_p = os.path.join(a.work, "tts.json")

    def ensure_tl():
        if not os.path.exists(tl_p):
            raise SystemExit("[前置] 没有 %s，先跑 --stage layout" % tl_p)
        return json.load(open(tl_p, encoding="utf-8"))

    stage = a.stage
    if stage in ("all", "tts"):
        lines = st_tts(script, a.work, force=a.force_tts)
        tl = st_layout(script, a.work, lines)
    if stage in ("all", "layout"):
        if stage != "all":
            _need(os.path.exists(tts_p), "没有 tts.json，先跑 --stage tts")
            tl = st_layout(script, a.work)
    if stage in ("all", "audio"):
        tl = tl if stage == "all" else ensure_tl()
        _need(all(s.get("path") for s in tl["scenes"]), "timeline 里没有旁白路径，先跑 --stage tts")
        st_audio(tl, a.work, script)

    if stage == "stills":
        tl = ensure_tl()
        mod = load_module(a.scenes)
        specs = [x.strip() for x in a.stills.split(",") if x.strip()] if a.stills else None
        st_stills(tl, a.work, mod, script, specs)
        return 0

    if stage == "qa":
        # qa 只读：绝不重渲、也不重新合成。缺成片就直接报错。
        tl = ensure_tl()
        _need(os.path.exists(a.out), "没有成片 %s，先跑 --stage mux" % a.out)
        mod = load_module(a.scenes)
        st_qa(a.out, tl, a.work, scenes_mod=mod, script=script)
        return 0

    if stage in ("all", "frames", "mux"):
        tl = tl if stage == "all" else ensure_tl()
        audio = os.path.join(a.work, "audio.wav")
        silent = os.path.join(a.work, "silent.mp4")
        mod = load_module(a.scenes)
        if stage in ("all", "frames"):
            # 渲染帧本身不需要音频（silent.mp4 是无声的）。只有 --stage all 才要求
            # audio.wav 已经在前面阶段生成——否则 --stage frames 在空 work 目录会白渲全片再挂掉。
            if stage == "all":
                _need(os.path.exists(audio), "混音阶段异常：audio.wav 未生成")
            silent = st_frames(tl, a.work, mod, script, jobs=a.jobs, resume=a.resume)
        if stage in ("all", "mux"):
            _need(os.path.exists(silent), "没有 silent.mp4，先跑 --stage frames")
            _need(os.path.exists(audio), "没有 audio.wav，先跑 --stage audio")
            film = st_mux(silent, audio, a.out, script)
            if stage == "all":
                st_qa(film, tl, a.work, scenes_mod=mod, script=script)
    return 0


if __name__ == "__main__":
    sys.exit(main())
