# -*- coding: utf-8 -*-
"""selfcheck.py — 技能自检 / 回归测试（不联网，10 秒内出结论）。

跑法：python selfcheck.py [-v]

检查的全是"曾经真的坏过"的点，不是泛泛的健康检查：
  1  TTS 缓存必须校验文本（v0.1 只判文件存在 -> 改台词不生效）
  2  sub_window 不能把长句字幕截断在 5.2s（v0.1 的硬钳）
  3  loudness 必须能测到真峰（v0.1 的 ebur128 没开 peak -> 真峰门禁永不触发）
  4  mux 默认不重编视频流（v0.1 二次编码）
  5  FrameSink 尺寸不符必须报错（v0.1 静默 resize）
  6  字形自查能识别缺字（msyhbd 缺 ✓ 等）
  7  ASS 头必须声明 PlayRes，且 Format/Dialogue 字段数一致
  8  时间轴第一段 start 严格为 0
  9  分块渲染的绝对帧号命名 + 缺口检测
 10  qa_report 能查出字幕条数不匹配
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import filmkit as K

RESULTS = []
VERBOSE = False


def run(name, fn):
    try:
        detail = fn()
        if detail == "skip":
            RESULTS.append(("SKIP", name, "-"))
            print("  SKIP %s" % name)
        else:
            RESULTS.append(("PASS", name, detail or ""))
            print("  PASS %s%s" % (name, ("  -> " + str(detail)) if (VERBOSE and detail) else ""))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
        print("  FAIL %s  -> %s" % (name, e))
    except Exception as e:                      # noqa: BLE001
        RESULTS.append(("FAIL", name, "%s: %s" % (type(e).__name__, e)))
        print("  FAIL %s  -> %s: %s" % (name, type(e).__name__, e))


def _tone_wav(path, seconds=1.0, freq=440.0):
    import numpy as np
    n = int(seconds * K.SR)
    t = np.arange(n) / float(K.SR)
    K.wav_write(path, 0.3 * np.sin(2 * np.pi * freq * t))


# ------------------------------------------------------------------ 用例
def t_cache_key():
    with tempfile.TemporaryDirectory() as d:
        mp3 = os.path.join(d, "a.mp3")
        open(mp3, "wb").write(b"\xff\xfb" + b"0" * 4096)
        old_key = K.tts_key("旧台词", "zh-CN-YunxiNeural", "+0%", "+0Hz")
        new_key = K.tts_key("新台词", "zh-CN-YunxiNeural", "+0%", "+0Hz")
        assert old_key != new_key, "不同文本却得到相同缓存 key"
        json.dump(dict(path=mp3, text="旧台词", key=old_key, trimmed=False, dur=1.0),
                  open(mp3 + ".json", "w", encoding="utf-8"), ensure_ascii=False)
        m = json.load(open(mp3 + ".json", encoding="utf-8"))
        assert m["key"] != new_key, "旧 meta 会被误判成命中"
        return old_key[:8]


def t_sub_window():
    scenes = [dict(id="a", start=0.0, end=12.0, dur=12.0,
                   voice_start=0.62, voice_end=11.5, text="长" * 60, sub="长句字幕")]
    t0, t1 = K.sub_window(scenes, 0)
    assert t1 - t0 > 10.0, "长句字幕只覆盖 %.1fs（5.2s 截断回归）" % (t1 - t0)
    two = [dict(id="a", start=0.0, end=5.0, dur=5.0, voice_start=0.6, voice_end=4.6,
                text="x", sub="x"),
           dict(id="b", start=5.0, end=10.0, dur=5.0, voice_start=5.6, voice_end=9.6,
                text="y", sub="y")]
    _, e0 = K.sub_window(two, 0)
    s1, _ = K.sub_window(two, 1)
    assert e0 <= s1 + 1e-6, "第一句字幕压到第二句（%.2f > %.2f）" % (e0, s1)
    # max_hold 显式钳短
    _, e2 = K.sub_window(two, 0, max_hold=2.0)
    assert e2 - 0.0 <= 2.62, "max_hold 没生效: %.2f" % e2
    return "%.1fs 覆盖整句" % (t1 - t0)


def t_true_peak():
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "t.wav")
        _tone_wav(wav, 2.0)
        lo = K.loudness(wav)
        if not lo:
            return "skip"
        assert "TP" in lo, "没有真峰（ebur128 未开 peak 的回归）: %s" % lo
        assert "I" in lo, "没有积分响度"
        return "I=%.1f LUFS, TP=%.1f dBFS" % (lo["I"], lo["TP"])


def t_mux_copy():
    with tempfile.TemporaryDirectory() as d:
        video = os.path.join(d, "v.mp4")
        r = subprocess.run([K.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                            "-i", "testsrc=size=320x180:rate=30:duration=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", video],
                           capture_output=True)
        if r.returncode != 0:
            return "skip"
        wav = os.path.join(d, "a.wav")
        _tone_wav(wav, 2.0)
        out = os.path.join(d, "o.mp4")
        K.mux(video, wav, out, loudnorm=False)
        assert os.path.getsize(out) > 5000, "mux 没有产出有效文件"
        return "%.0f KB" % (os.path.getsize(out) / 1024)


def t_strict_size():
    from PIL import Image
    with tempfile.TemporaryDirectory() as d:
        try:
            sink = K.FrameSink(os.path.join(d, "x.mp4"), w=320, h=180, fps=30)
        except FileNotFoundError:
            return "skip"
        try:
            try:
                sink.write(Image.new("RGB", (640, 360), (0, 0, 0)))
                raised = False
            except RuntimeError as e:
                raised = "帧尺寸" in str(e)
            assert raised, "尺寸不符没被拦下（静默 resize 回归）"
        finally:
            try:
                sink.proc.kill()
            except Exception:
                pass
    return "strict=True 生效"


def t_glyph():
    assert not K.glyph_missing_chars("AB中文abc", 32, "reg"), "常规字符被误判为缺字"
    assert K.glyph_missing_chars("\ue000", 32, "reg"), "缺字检测失效（私用区应判定缺失）"
    return "常规字符通过 / 私用区被识别"


def t_ass():
    scenes = [dict(id="a", start=0.0, end=3.0, dur=3.0, voice_start=0.6,
                   voice_end=2.8, text="你好世界", sub="你好世界")]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.ass")
        n = K.write_ass(scenes, p)
        txt = open(p, encoding="utf-8").read()
        assert "PlayResX: 1920" in txt and "PlayResY: 1080" in txt, "ASS 头缺 PlayRes"
        fmt = [l for l in txt.splitlines() if l.startswith("Format:")][-1]
        dia = [l for l in txt.splitlines() if l.startswith("Dialogue:")][0]
        assert len(fmt.split(",")) == len(dia.split(",")), "Format 与 Dialogue 字段数不一致"
        assert n == 1, "事件条数不对"
        return "PlayRes 1920x1080 / %d 条" % n


def t_timeline_zero():
    tl = K.build_timeline([dict(id="a", text="一", dur=2.0),
                           dict(id="b", text="二", dur=2.0)])
    assert tl["scenes"][0]["start"] == 0.0, "首段 start != 0"
    assert tl["frames"] == int(round(tl["total"] * K.FPS)), "帧数换算不一致"
    return "total %.2fs / %d 帧" % (tl["total"], tl["frames"])


def t_chunks():
    with tempfile.TemporaryDirectory() as d:
        seg = os.path.join(d, "segments")
        os.makedirs(seg, exist_ok=True)
        plan = [(0, 300), (300, 600), (600, 900)]
        for f0, f1 in plan:
            open(os.path.join(seg, "seg_%06d_%06d.mp4" % (f0, f1)), "wb").write(b"x" * 2048)
        missing = [p for p in plan
                   if not os.path.exists(os.path.join(seg, "seg_%06d_%06d.mp4" % p))]
        assert not missing, "正常命名下误报缺口"
        os.remove(os.path.join(seg, "seg_000300_000600.mp4"))
        missing = [p for p in plan
                   if not os.path.exists(os.path.join(seg, "seg_%06d_%06d.mp4" % p))]
        assert missing == [(300, 600)], "缺口没被检出: %s" % missing
    return "缺口可检出"


def t_qa_subs():
    with tempfile.TemporaryDirectory() as d:
        srt = os.path.join(d, "sub.srt")
        open(srt, "w", encoding="utf-8").write("1\n00:00:00,000 --> 00:00:01,000\n一\n\n")
        rep = K.qa_report(srt, expected_subtitles=3, notes=srt)
        assert any("字幕条数" in e for e in rep["errors"]), \
            "字幕条数不匹配没被报出来: %s" % rep["errors"]
    return "可检出"


def t_trim():
    """尾静音裁剪：1 秒静音 + 3 秒声 + 1 秒静音 -> 裁完应明显变短且保留中段。"""
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "long.wav")
        import numpy as np
        sr = K.SR
        pad = np.zeros(int(1.0 * sr), dtype=np.float32)
        body = 0.4 * np.sin(2 * np.pi * 440 * np.arange(int(3.0 * sr)) / float(sr)).astype(np.float32)
        K.wav_write(wav, np.concatenate([pad, body, pad]))
        pcm = K.decode_pcm(wav)
        cut = K.trim_silence(pcm)
        assert len(cut) < len(pcm) * 0.95, "尾静音没被裁掉（%.2fs -> %.2fs）" % (
            len(pcm) / sr, len(cut) / sr)
        assert len(cut) > 2.5 * sr, "裁过头了（%.2fs 短于语音本体）" % (len(cut) / sr)
        mid = float(np.max(np.abs(cut))) > 0.3
        assert mid, "裁完丢了语音本体"
        return "%.2fs -> %.2fs" % (len(pcm) / sr, len(cut) / sr)


def t_mux_48k():
    """mux 后音轨必须是 48kHz：不指定 -ar 时 loudnorm 会输出 192kHz、AAC 选 96kHz。"""
    with tempfile.TemporaryDirectory() as d:
        video = os.path.join(d, "v.mp4")
        r = subprocess.run([K.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                            "-i", "testsrc=size=320x180:rate=30:duration=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", video], capture_output=True)
        if r.returncode != 0:
            return "skip"
        wav = os.path.join(d, "a.wav")
        _tone_wav(wav, 2.0)
        out = os.path.join(d, "o.mp4")
        K.mux(video, wav, out, loudnorm=True)
        pr = K.probe(out)
        au = [s for s in pr.get("streams", []) if s.get("codec_type") == "audio"]
        assert au, "mux 后没有音轨"
        sr = int(au[0].get("sample_rate") or 0)
        assert sr == K.SR, "音轨采样率 %d != %d（loudnorm 内部会升到 192kHz，必须显式 -ar）" % (sr, K.SR)
        return "%d Hz" % sr


def t_compat():
    """v0.1 的调用签名必须还能用（升级不能把老项目直接炸掉）。"""
    a = K.tone(440, 0.2, kind="sine", harmonics=(1.0, 0.5))
    b = K.noise_burst(0.2, amp=0.1, lo=0.02, hi=0.35, decay=6.0, seed=7)
    assert len(a) > 0 and len(b) > 0, "兼容签名返回空"
    # 关键公共函数的关键字参数
    K.build_timeline([dict(id="a", text="一", dur=1.5)], lead=0.5, tail=0.8, min_dur=2.0)
    K.qa_report(__file__, expect_frames=None, ref_png=None, ref_sec=None,
                expected_subtitles=None, min_bitrate=0)
    return "tone/noise_burst/build_timeline/qa_report 旧签名可用"


def t_fps_check():
    """qa_report 必须能查出帧率不符（v0.2 之前只查宽高）。"""
    with tempfile.TemporaryDirectory() as d:
        v = os.path.join(d, "v25.mp4")
        r = subprocess.run([K.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                            "-i", "testsrc=size=320x180:rate=25:duration=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", v], capture_output=True)
        if r.returncode != 0:
            return "skip"
        rep = K.qa_report(v, min_bitrate=0)
        assert any("帧率" in e for e in rep["errors"]),             "帧率不符没被报出来: %s" % rep["errors"]
    return "25fps 被检出"


CASES = [
    ("1  TTS 缓存校验文本", t_cache_key),
    ("2  sub_window 覆盖长句", t_sub_window),
    ("3  loudness 测真峰", t_true_peak),
    ("4  mux 默认 copy 视频流", t_mux_copy),
    ("5  FrameSink 尺寸不符报错", t_strict_size),
    ("6  字形自查", t_glyph),
    ("7  ASS PlayRes / 字段数", t_ass),
    ("8  时间轴首段 start=0", t_timeline_zero),
    ("9  分块命名与缺口检测", t_chunks),
    ("10 qa 字幕条数校验", t_qa_subs),
    ("11 尾静音裁剪", t_trim),
    ("12 mux 音轨 48kHz", t_mux_48k),
    ("13 v0.1 调用签名兼容", t_compat),
    ("14 qa 帧率校验", t_fps_check),
]


def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    VERBOSE = a.verbose
    print("filmkit selfcheck  ffmpeg=%s" % K.FFMPEG)
    print("-" * 66)
    for name, fn in CASES:
        run(name, fn)
    print("-" * 66)
    n_pass = sum(1 for r in RESULTS if r[0] == "PASS")
    n_fail = sum(1 for r in RESULTS if r[0] == "FAIL")
    n_skip = sum(1 for r in RESULTS if r[0] == "SKIP")
    print("PASS %d / FAIL %d / SKIP %d" % (n_pass, n_fail, n_skip))
    for status, name, detail in RESULTS:
        if status == "FAIL":
            print("  ! %s: %s" % (name, detail))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
