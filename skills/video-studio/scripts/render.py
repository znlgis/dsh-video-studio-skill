
# -*- coding: utf-8 -*-
"""render.py — 一条命令从剧本到成片。

    python render.py --script film.json --scenes scenes.py --out out/成片.mp4 [--stage all]

阶段（可单独跑，产物全部落盘可续跑）：
    tts     逐句合成旁白 + 实测时长
    layout  生成 timeline.json（场景边界全部由真实旁白时长驱动）
    frames  逐帧渲染（确定性，可断点续跑、可按段并行）
    audio   采样级混音 -> audio.wav
    mux     编码 + 合成
    qa      自动质检 + 抽帧 + 联系表
"""
from __future__ import annotations
import argparse, importlib.util, json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import filmkit as K


def load_module(path: str, name="film_scenes"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_script(p) -> dict:
    s = json.load(open(p, encoding="utf-8"))
    for req in ("title", "lines"):
        if req not in s:
            raise SystemExit("film.json 缺少字段: " + req)
    for i, ln in enumerate(s["lines"]):
        ln.setdefault("id", "l%02d" % (i + 1))
        ln.setdefault("sub", ln["text"])
    return s


def st_tts(script, work):
    lines = script["lines"]
    print("== TTS (%d 句)" % len(lines))
    done = K.tts_batch(lines, work,
                       voice=script.get("voice", "zh-CN-YunyangNeural"),
                       rate=script.get("rate", "+0%"),
                       pitch=script.get("pitch", "+0Hz"))
    json.dump(done, open(os.path.join(work, "tts.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return done


def st_layout(script, work, lines=None):
    lines = lines or json.load(open(os.path.join(work, "tts.json"), encoding="utf-8"))
    print("== Layout")
    tl = K.build_timeline(lines, lead=script.get("lead", 0.55),
                          tail=script.get("tail", 0.85),
                          min_dur=script.get("min_dur", 3.0),
                          tail_map=script.get("tail_map"))
    tl["title"] = script["title"]
    tl["voice"] = script.get("voice", "zh-CN-YunyangNeural")
    json.dump(tl, open(os.path.join(work, "timeline.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    K.write_srt(tl["scenes"], os.path.join(work, "sub.srt"))
    print("   总时长 %.2fs / %d 帧 / %d 段" % (tl["total"], tl["frames"], len(tl["scenes"])))
    for s in tl["scenes"]:
        print("   %-8s %7.2f-%7.2f  %s" % (s["id"], s["start"], s["end"], s["text"][:40]))
    return tl


def st_audio(tl, work, script):
    print("== Audio")
    total = tl["total"]
    bus = K.AudioBus(total)
    # 1) 旁白
    for s in tl["scenes"]:
        pcm = K.decode_pcm(s["path"])
        bus.place(pcm, s["voice_start"], gain=script.get("voice_gain", 1.0),
                  fade_in=0.02, fade_out=0.06)
    # 2) 配乐（可选：由 script.music 指定和弦进行，全程序化合成）
    mus = script.get("music") or {}
    if mus.get("enabled", True):
        chords = mus.get("chords") or [[220.0, 261.63, 329.63], [174.61, 220.0, 261.63]]
        seg = float(mus.get("seg", 4.0))
        amp = float(mus.get("amp", 0.055))
        t = 0.0
        while t < total:
            ch = chords[int(t / seg) % len(chords)]
            pcm = K.pad_chord(ch, min(seg + 1.2, total - t + 0.2), amp=amp)
            bus.place(pcm, t, gain=mus.get("gain", 0.9), fade_in=0.3, fade_out=0.5)
            t += seg
    # 3) 音效（每个场景起点一个轻过渡音）
    if script.get("whoosh", True):
        for s in tl["scenes"][1:]:
            bus.place(K.noise_burst(0.45, amp=0.06, decay=7.0), max(0.0, s["start"] - 0.12), gain=0.7)
    out = os.path.join(work, "audio.wav")
    K.wav_write(out, bus.mix())
    print("   audio.wav %.2fs" % K.wav_dur(out))
    return out


def st_frames(tl, work, scenes_mod, script):
    print("== Frames")
    total = tl["frames"]
    sink = K.FrameSink(os.path.join(work, "silent.mp4"), crf=int(script.get("crf", 18)))
    scenes = tl["scenes"]
    idx = 0
    t0 = time.time()
    for fi in range(total):
        t = fi / float(tl["fps"])
        while idx + 1 < len(scenes) and t >= scenes[idx + 1]["start"]:
            idx += 1
        s = scenes[idx]
        lt = t - s["start"]
        frame = scenes_mod.draw_frame(s, lt, s["dur"], t, script, fi)
        if frame is None:
            frame = K.Image.new("RGB", (K.W, K.H), (10, 20, 34))
        frame = K.draw_subtitle(frame, K.sub_at(scenes, t))
        # 全片首尾淡入淡出
        fade = 1.0
        if t < 0.5: fade *= K.ease_out(t / 0.5)
        if t > tl["total"] - 0.8: fade *= max(0.0, (tl["total"] - t) / 0.8)
        if fade < 0.999:
            frame = K.Image.blend(K.Image.new("RGB", (K.W, K.H), (0, 0, 0)), frame, fade)
        sink.write(frame)
        if fi % 150 == 0:
            el = time.time() - t0
            fps = (fi + 1) / max(el, 1e-6)
            print("   %d/%d  %.1f fps  eta %.0fs" % (fi + 1, total, fps, (total - fi - 1) / max(fps, 1e-6)), flush=True)
    return sink.close()


def st_mux(silent, audio, out, script):
    print("== Mux")
    K.mux(silent, audio, out, crf=int(script.get("crf", 18)),
          preset=script.get("preset", "medium"))
    print("   ->", out)
    return out


def st_qa(film, tl, work):
    print("== QA")
    shots, ref = [], None
    for frac in (0.05, 0.3, 0.55, 0.8):
        p = os.path.join(work, "qa", "snap_%02d.png" % int(frac * 100))
        K.snap(film, tl["total"] * frac, p)
        shots.append(p)
    ref = shots[1] if len(shots) > 1 else (shots[0] if shots else None)
    rep = K.qa_report(film, expect_frames=tl["frames"],
                      min_bitrate=int(tl.get("min_bitrate", 3000000)),
                      ref_png=ref, ref_sec=tl["total"] * 0.3 if ref else None)
    pr = rep["probe"]; f = pr.get("format", {})
    print("   时长 %.2fs  大小 %.1f MB  码率 %.2f Mbps" % (
        float(f.get("duration", 0)), int(f.get("size", 0)) / 1048576, int(f.get("bit_rate", 0)) / 1e6))
    print("   响度", rep["loudness"])
    print("   字幕安全区:", "OK" if not K.qa_text_safe(tl["scenes"]) else K.qa_text_safe(tl["scenes"]))
    sheet = K.contact_sheet(shots, os.path.join(work, "qa", "sheet.png"))
    if sheet: print("   联系表:", sheet, "  <- 必须亲眼看")
    for w in rep["warnings"]:
        print("   [WARN]", w)
    if rep["errors"]:
        print("   [FAIL]")
        for e in rep["errors"]:
            print("     -", e)
    else:
        print("   [PASS] 质检通过")
    return rep["errors"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--out", default="out/film.mp4")
    ap.add_argument("--work", default="work")
    ap.add_argument("--stage", default="all",
                    choices=["all", "tts", "layout", "frames", "audio", "mux", "qa"])
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    script = load_script(a.script)
    tl_p = os.path.join(a.work, "timeline.json")

    if a.stage in ("all", "tts") or not os.path.exists(os.path.join(a.work, "tts.json")):
        lines = st_tts(script, a.work)
        tl = st_layout(script, a.work, lines)
    elif a.stage == "layout" or not os.path.exists(tl_p):
        tl = st_layout(script, a.work)
    else:
        tl = json.load(open(tl_p, encoding="utf-8"))

    if a.stage in ("all", "audio"):
        st_audio(tl, a.work, script)
    if a.stage in ("all", "frames", "mux", "qa"):
        mod = load_module(a.scenes)
        silent = st_frames(tl, a.work, mod, script)
        audio = os.path.join(a.work, "audio.wav")
        film = st_mux(silent, audio, a.out, script)
        st_qa(film, tl, a.work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
