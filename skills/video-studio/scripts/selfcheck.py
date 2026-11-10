# -*- coding: utf-8 -*-
"""selfcheck.py — 技能自检 / 回归测试（不联网，10~20 秒内出结论）。

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
 11  尾静音裁剪
 12  mux 音轨 48kHz
 13  v0.1 调用签名兼容
 14  qa 帧率校验
 15  （v0.3）立体声总线：channels=2 形状正确、声像能量守恒
 16  （v0.3）cue_time 三种写法都解析成绝对秒
 17  （v0.3）CueBus 落盘 -> 读回 -> schema 正确（含未知类型不崩）
 18  （v0.3）duck 真的压低了配乐（且不改变无音效处的电平）
 19  （v0.3）mix_film 峰值不越界、时长精确、旁白优先
 20  （v0.3）sfxkit 可用时：配方确定性 + 别名表能从 sfxkit 反查
 21  （v0.3）scorekit 可用时：同 seed bit-identical + 时长精确
 22  （v0.3）webengine 可用时：页面契约齐全（window.renderFrame/getCues/ready）
 23  （v0.3）qa_frames 可用时：能检出注入的单帧闪烁
 24  （v0.4.0）配乐来源：music.mode=off 不读上一轮 work/music.wav（模式判定回归）
  25  （v0.5.0）风格层：8 套主题全部过对比度门禁，8 类题材能分出 >=6 种风格
  26  （v0.5.0）legacy 兼容：没写 style 字段必须精确落回 Dm/120/seed7/单声线
  27  （v0.5.0）内容指纹：同剧本稳定、改标点不换风格、换题材才换
  28  （v0.5.0）声线计划：4 句样片 >=2 把声音，legacy 仍是单声线
  29  （v0.5.0）字体族与主题字幕：换族换缓存键，浅色主题不画黑背板
  30  （v0.6.0）门禁账本与签核：pass/fail/skipped 三档不许混，签核必须绑内容指纹
  31  （v0.6.0）阶段新鲜度：改了输入必须报"过时"，不许用旧产物静默出片
  32  （v0.6.0）并行渲染主题序列化：JSON 往返后重解风格必须回到 tuple
  33  （v0.7.0）音效跨片可区分：内容派生 seed 让跨片不同、legacy 逐样本不变
  34  （v0.7.0）配乐/音效片内分离：duck 默认加深，配乐给音效让位
  35  （v0.7.1）旁白优先：人声出现时背景必须再压、间隙恢复、人声电平不动
  36  （v0.7.1）旁白优先默认值：配乐 -12 dB 基线 + 旁白侧链 6 dB（读 st_audio 源码）
  37  （v0.7.1）旁白清晰度 SNR 兜底：背景天然太响时自动补压到 >=12 dB
  38  （v0.7.1）无旁白时混音不受影响（voice_env/guard 不得空转改电平）
  39  （v0.7.2）音频边界：极短旁白不崩、NaN/Inf 拒绝写盘、负起点与 seg=0 有守卫
  40  （v0.7.2）QC 报告落点只有一个来源（--out 的目录，不许 accept 另算一套）
  41  （v0.7.2）空门禁账本不许写成"通过"（没记录 != 通过）
  42  （v0.7.2）单阶段产物归属：不许把别的 stage 写的文件算成自己的（假 stale）
  43  （v0.7.2）theme_overrides 必须参与主题体检（否则能造出"看不清"的成片）
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import filmkit as K

RESULTS = []
VERBOSE = False


def _setup_stdio():
    """非 tty（管道 / 日志 / CI）时把 stdout/stderr 切到 UTF-8，避免 Windows 下中文乱码。

    真控制台（isatty=True）保持系统编码：交互运行时中文照样正常，
    只有"输出被重定向"才需要固定 UTF-8 字节。与 render / scorekit / webengine 同口径。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            pass


def run(name, fn):
    try:
        detail = fn()
        # 前缀判定：SKIP 的理由要能写给人看（"skip（缺 xxx.py）"），但 "skipped != 通过"
        # 这条语义不能被理由文字冲掉——旧实现只认精确 "skip"，带理由的降级会被记成 PASS。
        if str(detail or "").strip().lower().startswith("skip"):
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


# ---------------------------------------------------------------- v0.3.0 新增
def t_stereo_bus():
    """立体声总线：形状、声像、单声道来源自动展开。"""
    import numpy as np
    b = K.AudioBus(1.0, channels=2)
    assert b.bus.shape == (int(round(1.0 * K.SR)) + K.SR, 2), "立体声总线形状不对: %s" % (b.bus.shape,)
    src = np.ones(480, dtype=np.float32)
    b.place(src, 0.0, gain=0.5, pan_pos=-1.0)          # 全左
    seg = b.bus[10:400]
    assert abs(float(seg[:, 0].max()) - 0.5) < 1e-3, "声像左声道电平不对"
    assert float(np.abs(seg[:, 1]).max()) < 1e-3, "pan_pos=-1 右声道应该静音"
    b2 = K.AudioBus(1.0, channels=2)
    b2.place(np.ones(480, dtype=np.float32), 0.0, gain=0.5)   # 居中
    seg2 = b2.bus[10:400]
    assert abs(float(seg2[:, 0].max()) - float(seg2[:, 1].max())) < 1e-4, "居中声像左右应相等"
    return "channels=2 形状与声像正确"


def t_cue_time():
    """提示点 at 的三种写法。"""
    scenes = [dict(id="l01", start=0.0), dict(id="l02", start=3.5)]
    assert K.cue_time("abs:12.5") == 12.5, "abs: 写法解析错"
    assert K.cue_time(7.5) == 7.5, "数字写法解析错"
    assert K.cue_time("l02+0.35", scenes) == 3.85, "id+偏移 解析错"
    assert K.cue_time("l02-0.2", scenes) == 3.3, "id-偏移 解析错"
    assert K.cue_time("l02", scenes) == 3.5, "纯 id 解析错"
    try:
        K.cue_time("nope+1", scenes)
        raise AssertionError("找不到场景却没有报错")
    except ValueError:
        pass
    return "abs/数字/id±偏移 全部正确"


def t_cue_json():
    """CueBus 落盘 -> 读回 -> schema；未知类型不进 CUE_ALIASES 但也不崩。"""
    with tempfile.TemporaryDirectory() as d:
        bus = K.CueBus([dict(id="l01", start=0.0)], total=10.0)
        bus.add("l01+1.2", "whoosh", dur=0.9)
        bus.add("abs:4.0", "impact", gain=-2)
        bus.add(8.5, "blip")                       # 别名 blip -> ui_blip
        p = os.path.join(d, "cues.json")
        payload = bus.write(p, duration=10.0)
        assert payload["version"] == 1
        assert len(payload["cues"]) == 3, "提示点条数不对"
        assert [c["t"] for c in payload["cues"]] == sorted(c["t"] for c in payload["cues"]), "未按时间排序"
        assert payload["cues"][2]["type"] == "ui_blip", "别名没被展开: %s" % payload["cues"][2]
        back = K.CueBus.from_json(p)
        assert len(back.items) == 3, "读回条数不对"
        # 越界提示点（超过片长 2s）应被丢弃
        bus.items.append(dict(t=99.0, type="impact", src="pil"))
        assert len(bus.resolved()) == 3, "越界提示点没有被丢掉"
    return "3 条 / 别名展开 / 排序 / 越界丢弃"


def t_duck():
    """音效响起时配乐让位；没有音效的地方电平不变。"""
    import numpy as np
    n = int(2.0 * K.SR)
    music = np.full((n, 2), 0.5, dtype=np.float32)
    sfx = np.zeros((n, 2), dtype=np.float32)
    sfx[n // 2:n // 2 + K.SR // 4] = 0.9
    out = K.duck(music, sfx, depth_db=6.0)
    head = float(np.mean(out[: K.SR // 2]))
    mid = float(np.min(out[n // 2: n // 2 + K.SR // 4]))
    assert abs(head - 0.5) < 0.02, "没有音效的地方被压了（%.3f）" % head
    assert mid < 0.5 - 0.05, "duck 没生效（最深处 %.3f）" % mid
    return "静止段 %.3f / 音效段 %.3f" % (head, mid)


def t_mix_film():
    """三总线混音：时长精确、不越界、旁白在。"""
    import numpy as np
    n = int(1.5 * K.SR)
    t = np.arange(n) / float(K.SR)
    voice = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    music = np.full((n, 2), 0.2, dtype=np.float32)
    sfx = np.zeros((n, 2), dtype=np.float32)
    sfx[100:100 + 240] = 0.8
    out = K.mix_film(voice, music, sfx, total=1.5, fade=0.0)
    assert out.shape == (n, 2), "输出形状不对: %s" % (out.shape,)
    assert float(np.max(np.abs(out))) <= 0.9 + 1e-6, "峰值越界: %.3f" % float(np.max(np.abs(out)))
    assert float(np.max(np.abs(out))) > 0.2, "混出来几乎是静音"
    out0 = K.mix_film(voice, None, None, total=1.5, fade=0.0)
    assert abs(float(np.max(np.abs(out0))) - float(np.max(np.abs(voice)))) < 1e-5, "只有旁白时电平应不变"
    return "%.2fs / 峰值 %.3f" % (len(out) / float(K.SR), float(np.max(np.abs(out))))


def t_music_plan():
    """配乐来源判定：music.mode=off 不得读上一轮的 work/music.wav（v0.3.0 的回归）。

    背景：v0.3.0 的 st_audio 无条件读 work/music.wav，于是用户把 music.mode 改成 off
    之后，上一轮跑出来的旧配乐仍被混进成片（文档写的是 off = 不要配乐）。
    这里既测判定函数，也真的跑一次 st_audio 看输出电平。
    """
    # 1) 纯判定函数（K.music_plan）
    assert K.music_plan({"music": {"mode": "off"}}, True)[0] == "off", "mode=off 没被判成 off"
    assert K.music_plan({"music": {"enabled": False}}, True)[0] == "off", "enabled=false 没被判成 off"
    assert K.music_plan({"bpm": 120}, True)[0] == "score", "有 bpm 应走 scorekit"
    assert K.music_plan({"bpm": 120, "music": {"mode": "bed"}}, True)[0] == "bed", "mode=bed 解析错"
    assert K.music_plan({"music": {"mode": "file"}}, True)[0] == "file", "mode=file 解析错"
    assert K.music_plan({"music": {"mode": "file"}}, False)[0] == "missing-file", "mode=file 缺文件没报错"
    assert K.music_plan({"music": {"mode": "score"}}, True)[0] == "missing-bpm", "mode=score 缺 bpm 没报错"
    assert K.music_plan({"music": {"mode": "weird"}})[0] == "unknown-mode", "未知 mode 没报错（会静默当 auto）"
    assert K.music_plan({}, True)[0] == "file", "缺省时外部 music.wav 兼容链断了"
    assert K.music_plan({}, False)[0] == "none", "缺省且什么都没有时应判 none"
    assert K.music_plan({"music": {"chords": [[220.0, 261.6]]}}, False)[0] == "bed", "v0.2 和弦床兼容链断了"

    # 2) 端到端：st_audio 在 mode=off 下不得混入 work/music.wav
    try:
        import render   # 同目录编排器；只借用它的 stage 函数
    except Exception:                                       # noqa: BLE001
        return "skip"
    import numpy as np
    with tempfile.TemporaryDirectory() as d:
        work = os.path.join(d, "work")
        os.makedirs(work)
        # 旁白轨故意做成全零：这样 audio.wav 的峰值只可能来自配乐
        voice = os.path.join(d, "voice.wav")
        K.wav_write(voice, np.zeros(K.SR, dtype=np.float32))
        tl = dict(total=1.0, fps=K.FPS, frames=K.FPS, scenes=[
            dict(id="l01", start=0.0, end=1.0, dur=1.0, voice_start=0.1, voice_end=0.9,
                 path=voice, text="一", sub="一")])
        n = int(1.0 * K.SR)
        t = np.arange(n) / float(K.SR)
        loud = (0.8 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
        K.wav_write(os.path.join(work, "music.wav"), np.stack([loud, loud], axis=1))
        buf = io.StringIO()
        with redirect_stdout(buf):
            off_out = render.st_audio(tl, work, {"music": {"mode": "off"}})
            off_peak = float(np.max(np.abs(K.wav_read(off_out)[0])))
            file_out = render.st_audio(tl, work, {"music": {"mode": "file"}})
            file_peak = float(np.max(np.abs(K.wav_read(file_out)[0])))
        assert "music.mode=off" in buf.getvalue(), "off 没有打印说明（静默跳过也不行）"
        assert off_peak < 0.01, "music.mode=off 仍把旧 music.wav 混进来了（峰值 %.3f）" % off_peak
        # v0.7.1：断言改成"相对 off 明显变大"而不是绝对 0.3——
        # 配乐基线降到 -12 dB（旁白优先）后，0.8 幅度的外部配乐混出来约 0.2；
        # 绝对阈值会把"旁白优先调好了"判成"没读到外部配乐"，这类断言会被调参牵着走。
        assert file_peak > 0.1 and file_peak > off_peak * 3.0, (
            "music.mode=file 没有用外部配乐（off %.3f -> file %.3f）" % (off_peak, file_peak))
    return "off 峰值 %.3f / file 峰值 %.3f" % (off_peak, file_peak)


def t_sfxkit():
    """sfxkit 存在时的接口与确定性自检；不存在就 SKIP。"""
    mod = None
    try:
        import sfxkit as mod  # noqa: F401
    except Exception:                                       # noqa: BLE001
        return "skip"
    names = set(mod.load_all().keys()) if hasattr(mod, "load_all") else set()
    assert names, "sfxkit 没有任何配方"
    need = {"ui_blip", "ui_click", "typing", "whoosh", "impact", "sub_drop", "riser", "wind"}
    missing = need - names
    assert not missing, "sfxkit 缺配方: %s" % ", ".join(sorted(missing))
    a = mod.render("impact", seed=3)
    b = mod.render("impact", seed=3)
    import numpy as np
    assert len(a) == len(b) and np.array_equal(np.asarray(a), np.asarray(b)), "同一 seed 两次渲染不一致"
    assert float(np.max(np.abs(a))) > 1e-3, "impact 渲染出来是静音"
    aliases = getattr(mod, "CUE_ALIASES", None)
    if aliases:
        for k, v in aliases.items():
            assert v in names, "别名 %s -> %s 指向不存在的配方" % (k, v)
    return "%d 配方 / 确定性 OK" % len(names)


def t_scorekit():
    """scorekit 存在时：同 seed 一致、时长精确、不削波。"""
    try:
        import scorekit as sk
    except Exception:                                       # noqa: BLE001
        return "skip"
    import numpy as np
    a, rep = sk.render(12.0, bpm=120, key="Dm", seed=5)
    b, _ = sk.render(12.0, bpm=120, key="Dm", seed=5)
    a = np.asarray(a)
    b = np.asarray(b)
    assert a.shape == b.shape and np.array_equal(a, b), "同一 seed 两次配乐不一致"
    assert abs(len(a) / float(sk.SR) - 12.0) < 0.05, "配乐时长不对: %.3fs" % (len(a) / float(sk.SR))
    pk = float(np.max(np.abs(a)))
    assert pk <= 1.0, "配乐削波: %.3f" % pk
    assert pk > 1e-3, "配乐是静音"
    return "12s / %.2f LUFS? / 峰值 %.2f" % (float(rep.get("lufs", 0.0)) if isinstance(rep, dict) else 0.0, pk)


def t_webengine():
    """webengine 存在时：模块可导入 + 关键接口在（不真的起浏览器，避免自检变慢）。

    注意作用域：这条检查的是**工具本身**是否可用。页面模板 assets/web/ 属于
    "项目自己的东西"——用户按 quickstart 只拷了 scripts/*.py 时它本来就不在工作目录里，
    此时不该判 FAIL（那会把"没拷模板"和"工具坏了"混为一谈），而是明确提示要不要拷。
    """
    try:
        import webengine as we  # noqa: F401
    except Exception:                                       # noqa: BLE001
        return "skip"
    for fn in ("snap", "export_cues", "render_full", "qa_text", "page_meta"):
        assert hasattr(we, fn), "webengine 缺函数 %s" % fn

    here = os.path.dirname(os.path.abspath(__file__))
    # 页面模板可能在技能目录（装成插件时）也可能在用户工作目录（按 quickstart 拷过去）
    cands = [os.path.join(os.path.dirname(here), "assets", "web"),
             os.path.join(os.getcwd(), "web"),
             os.path.join(os.getcwd(), "assets", "web")]
    web = next((p for p in cands if os.path.exists(os.path.join(p, "engine.js"))), None)
    if web is None:
        return "模块可用；工作目录没有 web/ 页面模板（用 --engine web 前先 cp -r <SKILL_DIR>/assets/web ./web）"
    need = ["index.html", "engine.js"]
    miss = [n for n in need if not os.path.exists(os.path.join(web, n))]
    assert not miss, "web/ 缺文件: %s（%s）" % (miss, web)
    txt = open(os.path.join(web, "engine.js"), encoding="utf-8", errors="replace").read()
    for key in ("renderFrame", "getCues", "DURATION"):
        assert key in txt, "engine.js 没导出 %s（页面契约）" % key
    return "接口齐全 + 页面契约齐全（%s）" % os.path.relpath(web, os.getcwd())


def t_qa_frames():
    """qa_frames 存在时：能 import 且函数齐全（真正的检出能力由它自己的 --selftest 保证）。"""
    try:
        import qa_frames as qf
    except Exception:                                       # noqa: BLE001
        return "skip"
    for fn in ("flicker_scan", "black_windows", "luma_curve", "sync_check", "report"):
        assert hasattr(qf, fn), "qa_frames 缺函数 %s" % fn
    return "5 个函数齐全"



def t_style_kits():
    """25 风格层：8 套主题过对比度门禁 + 自动选风格能把 8 类题材分开。"""
    import stylekit as ST
    bad = ST.lint_all()
    assert not bad, "内置主题没过对比度门禁: %s" % bad
    topics = {
        "tech": "AI 算法 模型 数据 架构 开源 编程 大模型 代码 系统",
        "report": "报告 复盘 年度 总结 研究 分析 调研 白皮书 趋势 发布",
        "craft": "手工 自然 历史 传统 木 田野 匠 古 茶 手艺 山河",
        "launch": "活动 上新 招募 挑战 年轻 潮流 种草 福利 开箱 限量",
        "truth": "观点 为什么 本质 真相 极简 反思 边界 定义 取舍",
        "story": "故事 记忆 城市 深夜 旅行 纪实 情绪 时代 变迁 命运",
        "night": "深夜 学习 效率 专注 写作 整理 笔记 书 日常 房间",
        "epic": "史诗 爆发 冲击 巅峰 决战 荣耀 震撼 传奇 极限 突破",
    }
    got = {}
    for key, text in topics.items():
        s = {"title": text, "style": "auto",
             "lines": [{"id": "l01", "kind": "hook", "text": text},
                       {"id": "l02", "kind": "body", "text": text}]}
        got[key] = ST.resolve_script_style(s)["style"]
    assert len(set(got.values())) >= 6, "8 类题材只分出 %d 套风格: %s" % (len(set(got.values())), got)
    return "%d 套主题 + %d 类题材 -> %d 种风格" % (len(ST.STYLE_IDS), len(topics), len(set(got.values())))


def t_style_legacy():
    """26 legacy 兼容：没写 style 字段必须精确落回 v0.4 的配乐/旁白参数。"""
    import stylekit as ST
    s = {"title": "旧项目", "lines": [{"id": "a", "kind": "body", "text": "x"}]}
    p = ST.resolve_script_style(s)
    assert p["legacy"] is True, "没有 style 字段却没走 legacy"
    assert p["music"]["bpm"] == 120.0, "legacy BPM 应为 120，收到 %r" % (p["music"]["bpm"],)
    assert p["music"]["key"] == "Dm", "legacy 调性应为 Dm，收到 %r" % (p["music"]["key"],)
    assert p["music"]["seed_effective"] == 7, "legacy seed 应为 7（不做内容派生）"
    assert p["music"]["seed_from_content"] is False
    assert p["voice"]["map"] == {}, "legacy 不该有声线映射"
    assert p["voice"]["policy"]["jitter"] == 0.0 and p["voice"]["policy"]["auto"] is False
    # legacy 的节奏必须等于 render.py v0.4 的函数默认值，否则"没写 lead 的老剧本"会变节奏
    assert (p["pacing"]["lead"], p["pacing"]["tail"], p["pacing"]["min_dur"]) == (0.55, 0.85, 3.0), p["pacing"]
    # 显式 "legacy" 也必须是同一档
    s2 = dict(s, style="legacy")
    assert ST.resolve_script_style(s2)["legacy"] is True
    # 显式风格则必须真的换掉三件套
    s3 = dict(s, style="cinema")
    p3 = ST.resolve_script_style(s3)
    assert p3["legacy"] is False and p3["music"]["bpm"] in ST.STYLES["cinema"]["music"]["bpm_ladder"]
    assert p3["music"]["key"] in ST.STYLES["cinema"]["music"]["keys"]
    return "bpm=%g key=%s seed=%d / cinema: bpm=%g key=%s" % (
        p["music"]["bpm"], p["music"]["key"], p["music"]["seed_effective"],
        p3["music"]["bpm"], p3["music"]["key"])


def t_style_determinism():
    """27 指纹确定性：同剧本同结果；改错别字不换风格；改题材才换。"""
    import stylekit as ST
    base = {"title": "AI 大模型的三年", "style": "auto",
            "lines": [{"id": "l01", "kind": "hook", "text": "大模型改变了什么"},
                      {"id": "l02", "kind": "body", "text": "算法和数据的规模一起上去了"}]}
    a = ST.resolve_script_style(base)
    b = ST.resolve_script_style(json.loads(json.dumps(base)))
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str), \
        "同一剧本两次解析结果不同（不确定性）"
    typo = json.loads(json.dumps(base))
    typo["lines"][1]["text"] += "。"
    c = ST.resolve_script_style(typo)
    assert c["style"] == a["style"], "改一个标点就换了风格: %s -> %s" % (a["style"], c["style"])
    assert c["music"]["bpm"] == a["music"]["bpm"], "改一个标点就换了 BPM"
    assert c["fingerprint"]["full"] != a["fingerprint"]["full"], "full 指纹没变（内容派生失效）"
    other = {"title": "深夜学习指南", "style": "auto",
             "lines": [{"id": "l01", "kind": "hook", "text": "为什么深夜反而能专注"},
                       {"id": "l02", "kind": "body", "text": "整理笔记，写作，阅读都更安静"}]}
    d = ST.resolve_script_style(other)
    assert d["style"] != a["style"], "换了一部片风格却相同: %s" % d["style"]
    return "%s -> %s（同一剧本稳定），换片 -> %s" % (a["style"], c["style"], d["style"])


def t_voice_plan():
    """28 声线计划：legacy 保持单声线；风格化时按 kind 换声线且确定性。"""
    try:
        import voicekit as VK
    except Exception:                                       # noqa: BLE001
        return "skip"
    import stylekit as ST
    s = {"title": "AI 大模型", "style": "auto",
         "lines": [{"id": "l01", "kind": "hook", "text": "大模型改变了什么"},
                   {"id": "l02", "kind": "body", "text": "算法和数据的规模一起上去了"},
                   {"id": "l03", "kind": "quote", "text": "他说：这不可能"},
                   {"id": "l04", "kind": "outro", "text": "它已经在我们身边了"}]}
    plan = ST.resolve_script_style(s)
    vp = VK.voice_plan(s, plan)
    assert len(vp) == 4, "计划行数应为 4，收到 %d" % len(vp)
    assert json.dumps(vp, sort_keys=True, default=str) == json.dumps(
        VK.voice_plan(s, plan), sort_keys=True, default=str), "voice_plan 不确定"
    personas = {r["persona"] for r in vp}
    assert len(personas) >= 2, "4 句样片只用了一个声线: %s" % personas
    tones = {r["tone"] for r in vp}
    assert len(tones) >= 2, "语气档没有变化: %s" % tones
    for r in vp:
        for fld in ("voice", "rate", "pitch", "volume", "lead", "tail"):
            assert r.get(fld) not in (None, ""), "计划行缺 %s: %r" % (fld, r.get("id"))
    # legacy：单声线、body 语气、无抖动
    leg = {"title": "旧项目", "lines": [{"id": "a", "kind": "body", "text": "x"}]}
    lp = ST.resolve_script_style(leg)
    lvp = VK.voice_plan(leg, lp)
    assert len({r["voice"] for r in lvp}) == 1, "legacy 出现多声线"
    assert all(r["tone"] == "body" for r in lvp), "legacy 不该换语气档"
    return "%d 句 / %d 把声音 / %d 档语气" % (len(vp), len(personas), len(tones))


def t_font_family():
    """29 字体族 + 字幕主题：换族必须换缓存键，主题字幕不能是黑底白字。

    **别把"系统装了几族字体"当成产品缺陷**：CI 的 ubuntu 只装 fonts-noto-cjk，
    四个族全回退到同一个 NotoSansCJK，宽度必然一样。v0.5.0 的第一版就在这里误报——
    断言写成"宽度必须不同"，于是 Linux CI 红、Windows 绿。
    真正要保证的是**机制**：缓存按 family 分键（换族不会串味）；
    宽度差异只在"系统确实解析出不同字体文件"时才有意义，单族系统跳过并说明。
    """
    fams = list(getattr(K, "FAMILY_IDS", ()))
    assert fams, "filmkit 没有 FONT_FAMILIES（v0.5 字体族没生效）"
    resolved = {}
    for fam in fams:
        f = K.font(40, "bold", fam)
        assert f is not None, "字体族 %s 加载失败" % fam
        assert f.size == 40, "字体族 %s 的字号被改写成 %s" % (fam, f.size)
        resolved[fam] = str(getattr(f, "path", "") or "")
    # (a) 机制保证：四个族必须是四条独立缓存，且键里带 family（否则 wrap 与绘制会用不同字体）
    keys = [k for k in K._font_cache if len(k) == 3 and k[0] == 40 and k[1] == "bold"]
    assert len({k[2] for k in keys}) == len(fams), \
        "字体缓存键没带 family（换族会串味）: %s" % (keys,)
    # (b) 语义保证：仅当系统真解析出 >=2 个字体文件时，宽度才必须有差异
    uniq_files = len(set(resolved.values()))
    widths = {}
    for fam in fams:
        widths[fam] = round(K.text_w("中文测试ABC", K.font(40, "bold", fam)), 3)
    uniq_w = len(set(widths.values()))
    if uniq_files >= 2:
        assert uniq_w >= 2, ("换族没换字形（%d 个字体文件却只有 %d 种宽度）: %s"
                             % (uniq_files, uniq_w, widths))
        detail = "%d 个字体族 / %d 个字体文件 / %d 种宽度" % (len(fams), uniq_files, uniq_w)
    else:
        # 单族回退（CI 的 fonts-noto-cjk，或极简 Linux 容器）：只验机制，不验宽度
        detail = "%d 个字体族 / 单族回退（%s）/ 只验缓存分键" % (
            len(fams), os.path.basename(list(resolved.values())[0]) or "?")
    # 缓存键必须区分 family（否则 wrap 与绘制会用不同字体，文字溢出安全区）
    a = K.wrap_cn_cached("这是一句用来测试换行缓存键的话", 40, 300, "reg", "sans")
    b = K.wrap_cn_cached("这是一句用来测试换行缓存键的话", 40, 300, "reg", "kai")
    assert isinstance(a, list) and isinstance(b, list)
    # 旧签名必须仍然可用（用户脚本里有大量 font(46,"reg")）
    assert K.font(46, "reg") is not None
    assert K.wrap_cn_cached("旧调用", 40, 500) is not None
    # 字幕主题：浅色主题不该画黑背板
    from PIL import Image
    try:
        import stylekit as ST
        th = ST.THEMES["magazine"]
    except Exception:                                       # noqa: BLE001
        th = None
    if th is not None:
        img = Image.new("RGB", (K.W, K.H), th["bg"])
        K.draw_subtitle(img, "浅色主题字幕", theme=th)
        px = img.getpixel((960, int((K.SUB_BAND[1] + K.SUB_BAND[3]) / 2)))
        assert sum(px) > 200, "浅色主题的字幕背板是深色（%s），说明 theme 没生效" % (px,)
    img2 = Image.new("RGB", (K.W, K.H), (0, 0, 0))
    K.draw_subtitle(img2, "旧调用", theme=None)
    return detail


def t_gates():
    """30 门禁账本 / 签核 / QC 报告：三档状态必须能区分"没查"和"通过"。

    这一条防的是 v0.6.0 自己最容易犯的错——把 skipped 混进 pass。
    用户看到的"质检通过"必须能区分"我查了 9 项"和"我只查了 3 项"：
    前者是真知情，后者是假安全感。顺带验签核绑内容指纹：改了剧本旧签核必须失效。
    """
    import tempfile
    import gates as GT
    w = tempfile.mkdtemp(prefix="sc_gates_")
    lg = GT.GateLedger(w, {"title": "t", "lines": [{"text": "x"}]}, engine="pil")
    lg.ok("A", "通过项", "1")
    lg.fail("B", "失败项", "2")
    lg.skip("C", "没执行项", "3")
    c = lg.counts()
    assert c == {"pass": 1, "fail": 1, "skipped": 1}, c
    # 三档语义：fail 一定失败；skipped 只在 --strict-gates 下失败
    assert len(lg.exit_errors()) == 1, "fail 没被算成失败"
    assert len(lg.exit_errors(strict=True)) == 2, "--strict-gates 没把 skipped 升级成失败"
    lg.write_json()
    assert os.path.exists(os.path.join(w, "gates.json")), "gates.json 没落盘"
    # 签核：没签 = skipped；签了 = pass；内容指纹变了 = skipped（不是 pass！）
    ch = lg.content_hash
    st0, _ = GT.signoff_state(w, ch)
    assert all(v["status"] == "skipped" for v in st0.values()), st0
    GT.write_signoff(w, "stills", ch, note="看过 sheet")
    st1, _ = GT.signoff_state(w, ch)
    assert st1["stills"]["status"] == "pass", st1["stills"]
    st2, stale = GT.signoff_state(w, "别的指纹")
    assert st2["stills"]["status"] == "skipped" and "stills" in stale, (st2, stale)
    # 内容指纹：改剧本 / 换引擎必须变，渲染期私有键（_path/_style）不许影响
    h1 = GT.content_hash({"title": "a", "lines": []}, engine="pil")
    h2 = GT.content_hash({"_path": "/x", "title": "a", "lines": []}, engine="pil")
    h3 = GT.content_hash({"title": "a", "lines": []}, engine="web")
    assert h1 == h2, "渲染期私有键影响了内容指纹（会导致假失效）"
    assert h1 != h3, "换引擎了内容指纹却没变（旧签核会错误地继续有效）"
    # QC 报告：三档都要出现在矩阵里，且 skipped 不能被渲染成"通过"
    GT.append_cost(w, "frames", 1.5, frames=30)
    p = GT.write_report(w, os.path.join(w, "out", "QC-REPORT.md"))
    txt = open(p, encoding="utf-8").read()
    for must in ("门禁矩阵", "人工签核", "耗时台账", "**skipped**", "**fail**"):
        assert must in txt, "报告里缺 %r" % must
    assert "未通过" in txt and "通过" in txt, "报告结论段没区分通过/未通过"
    # 原子写：不留 .tmp 尾巴
    assert not [f for f in os.listdir(w) if f.endswith(".tmp")], os.listdir(w)
    return "三档 1/1/1 + 签核 4 道闸 + 指纹失效 + 报告矩阵"


def t_stagewatch():
    """31 阶段新鲜度：改上游 -> 下游必须报过时；首跑不刷噪声；产物丢失要报。

    这一条防的是 v0.6.0 前的老问题：frames 有分块指纹，其余 stage 全是裸奔
    （改 style 后 audio 仍读旧 music.wav、改 hold 后 frames 仍用旧 cues.json，都不报错）。
    """
    import tempfile
    import stagewatch as SW
    w = tempfile.mkdtemp(prefix="sc_sw_")
    assert all(not r["issues"] for r in SW.explain(w)), "空目录不该报过时"

    def _wf(name, text):
        p = os.path.join(w, name)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)

    _wf("style.json", "s1")
    _wf("voice_plan.json", "v1")
    SW.snapshot(w, "style")
    assert not [i for r in SW.explain(w, ["style"]) for i in r["issues"]], "刚跑完就报过时"
    _wf("tts.json", "t1")
    SW.snapshot(w, "tts")
    # 改 tts 的上游（style.json）-> tts 必须报 input-changed
    _wf("style.json", "s2")
    issues = [i for r in SW.explain(w, ["tts"]) for i in r["issues"]]
    assert any(i["kind"] == "input-changed" for i in issues), issues
    assert any("render.py" in (i.get("rerun") or "") for i in issues), "过时了却没给重跑命令"
    # 产物被删 -> 必须报 output-missing
    os.remove(os.path.join(w, "tts.json"))
    issues = [i for r in SW.explain(w, ["tts"]) for i in r["issues"]]
    assert any(i["kind"] == "output-missing" for i in issues), issues
    # 默认只告警；--strict-stale 才失败
    errs, warns = SW.report(w, ["tts"], write=lambda lv, m: None)
    assert not errs and warns, (errs, warns)
    errs_strict, _ = SW.report(w, ["tts"], strict=True, write=lambda lv, m: None)
    assert errs_strict, "--strict-stale 没有让过时变成失败"
    # **源头输入**（剧本 / scenes.py）改了但没重跑 -> 必须报 source-changed。
    # 这条是 v0.6.0 补的洞：早期只跟踪 stage 之间的产物，"改了台词没重跑 tts" 查不出来，
    # 而那正是最需要报警的场景（旧 tts 配新剧本 = 配音字幕全错位）。
    w2 = tempfile.mkdtemp(prefix="sc_sw2_")
    film = os.path.join(w2, "film.json")
    scenes = os.path.join(w2, "scenes.py")
    open(film, "w", encoding="utf-8").write("{\"title\":\"a\"}")
    open(scenes, "w", encoding="utf-8").write("# v1")
    src = {"script": film, "scenes": scenes}
    _wf2 = lambda n, t: open(os.path.join(w2, n), "w", encoding="utf-8").write(t)
    _wf2("tts.json", "t1")
    SW.snapshot(w2, "tts", src)
    issues = [i for r in SW.explain(w2, ["tts"], src) for i in r["issues"]]
    assert not issues, "刚跑完就报过时：%s" % issues
    open(film, "w", encoding="utf-8").write("{\"title\":\"b\"}")   # 改剧本
    issues = [i for r in SW.explain(w2, ["tts"], src) for i in r["issues"]]
    assert any(i["kind"] == "source-changed" for i in issues), (
        "改了剧本却没报过时（这正是 v0.6.0 要堵的洞）：%s" % issues)
    assert any("film.json" in i["reason"] for i in issues), issues
    # 坏 manifest 不许崩（用户手改坏一个 json 不该让整片跑不动）
    open(os.path.join(w, "manifest.json"), "w", encoding="utf-8").write("{broken")
    SW.explain(w)
    return "改上游/改源头即报过时 + 产物丢失 + strict 升级 + 坏 manifest 不崩"


def t_parallel_frames():
    """32 --jobs >1 的并行路径能真的跑（v0.6.0 修的 pre-existing bug）。

    bug 形状：worker 从 _script_snapshot.json 读剧本，JSON 把主题色元组变成 list，
    scenes._bg() 拿它当缓存键 -> TypeError: unhashable type: "list"，
    于是 SKILL 里教的 --stage frames --jobs 4 直接崩。
    这里**不跑整片**（太慢），只验两条防线：① 主题色能过 JSON 往返当缓存键；
    ② worker 反序列化后会重解风格（_style 里的颜色回到 tuple）。
    """
    import json as _json
    import tempfile
    import stylekit as ST
    th = dict(ST.THEMES["tech-deep"])
    # ① 防御层：scenes._hashable 把 list 冻结成 tuple
    scenes = _load_scenes_mod()
    if scenes is not None and hasattr(scenes, "_hashable"):
        frozen = scenes._hashable(("baked", th["bg"], th["bg2"]))
        hash(frozen)                       # 不能抛 unhashable
        assert isinstance(frozen[1], tuple), "主题色没被冻结成 tuple：%r" % (frozen[1],)
    # ② 根因层：JSON 往返后，_render_chunk_worker 里必须能拿到 tuple 的 bg
    round_trip = _json.loads(_json.dumps({"bg": th["bg"]}, ensure_ascii=False))
    assert isinstance(round_trip["bg"], list), "前提变了：JSON 居然保住了 tuple"
    w = tempfile.mkdtemp(prefix="sc_par_")
    snap = os.path.join(w, "snap.json")
    script = {"title": "t", "style": "tech-deep", "lines": [{"text": "x"}]}
    script["_style"] = ST.resolve_script_style(script)
    with open(snap, "w", encoding="utf-8") as fh:
        _json.dump(script, fh, ensure_ascii=False)
    back = _json.load(open(snap, encoding="utf-8"))
    assert isinstance(back["_style"]["theme"]["bg"], list), "前提变了"
    # 重解风格（render.resolve_style 的纯函数部分）：必须回到 tuple
    again = ST.resolve_script_style(back)
    assert isinstance(again["theme"]["bg"], tuple), (
        "--jobs >1 时主题色仍是 list，_bg() 会 TypeError（这正是被修的 bug）：%r"
        % (again["theme"]["bg"],))
    return "主题标量可哈希 + 反序列化后重解回 tuple"


def _load_scenes_mod():
    """加载 assets/scenes.py 模板（找不到就返回 None：CI 里可能只拷 scripts/）。"""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "..", "assets", "scenes.py")
    if not os.path.exists(cand):
        return None
    try:
        spec = importlib.util.spec_from_file_location("sc_assets_scenes", cand)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:                                       # noqa: BLE001
        return None

def t_sfx_content_seed():
    """v0.7：音效 seed 必须随内容变化，但 legacy 路径必须逐样本不变。

    - 不传 style/content_fp -> 两次渲染逐样本相同（v0.4 兼容，不破老片）；
    - 传了不同 content_fp  -> 必须不同（否则跨片音效还是"都一样"）；
    - 同一 content_fp      -> 逐样本相同（可复现，是程序化视频的底线）。
    """
    import numpy as np
    scenes = [{"id": "l01", "kind": "hook", "start": 0.0, "end": 2.0},
              {"id": "l02", "kind": "body", "start": 2.0, "end": 6.0}]
    rows = [{"at": "l02", "type": "ui_click"}, {"at": "l02+0.5", "type": "whoosh"}]

    def build(**kw):
        b = K.CueBus(scenes=scenes, total=6.0)
        b.extend(rows)
        return b.build_audio(total=6.0, verbose=False, **kw)

    try:
        legacy_a, legacy_b = build(), build()
    except Exception:                                       # noqa: BLE001
        return "skip"                                       # sfxkit 不可用
    assert np.array_equal(legacy_a, legacy_b), "legacy 两次渲染不一致（v0.4 兼容被破坏）"
    a = build(style="tech-deep", content_fp="aaaa1111", key="Dm")
    b = build(style="tech-deep", content_fp="bbbb2222", key="Dm")
    assert not np.array_equal(a, b), "不同内容指纹得到相同音效轨：跨片音效仍一模一样"
    a2 = build(style="tech-deep", content_fp="aaaa1111", key="Dm")
    assert np.array_equal(a, a2), "同内容指纹渲染不可复现"
    return "legacy 逐样本相同 / 跨片可区分"


def t_audio_separation():
    """v0.7：混音侧 duck 默认必须加深（片内配乐给音效让位）。

    直接读 render.py 的常量文本而不是跑全链：这条断言盯的是"默认值没被改回去"
    （v0.4 的 3.0 dB 在段落过门时配乐几乎和音效等高，是"听起来像同一套"的成因之一）。
    """
    import re
    path = os.path.join(HERE, "render.py")
    src = open(path, encoding="utf-8").read()
    m = re.search(r"duck_default\s*=\s*([0-9.]+)", src)
    assert m, "render.py 里找不到 duck_default"
    base = float(m.group(1))
    assert base >= 3.5, "duck_default=%.1f dB 太浅（v0.7 要求 >= 3.5）" % base
    for sid, want in (("mono", 3.0), ("cinema", 5.5), ("epic", 5.5)):
        mm = re.search(r'"%s"\s*:\s*([0-9.]+)' % sid, src)
        assert mm, "render.py 里找不到风格 %s 的 duck 值" % sid
        assert float(mm.group(1)) >= want, "%s duck=%.1f < %.1f" % (sid, float(mm.group(1)), want)
    return "duck 默认 %.1f dB（cinema/epic 5.5）" % base


def t_voice_priority():
    """v0.7.1 旁白优先：人声出现时背景必须再压，人声间隙背景恢复。

    以前配乐基线只比旁白低 1.5 dB，视频以旁白为主时观众要费力分辨字。
    修法：`mix_film` 用旁白包络做侧链——人声处配乐 -6 dB、音效 -3 dB，间隙回原电平。
    这里分别验证「人声段被压」和「静音段恢复」，以及旁白自身电平不被影响。
    """
    import numpy as np
    n = int(2.0 * K.SR)
    t = np.arange(n) / float(K.SR)
    voice = np.zeros(n, dtype=np.float32)
    voice[: n // 2] = (0.2 * np.sin(2 * np.pi * 220 * t[: n // 2])).astype(np.float32)
    music = np.full((n, 2), 0.2, dtype=np.float32)
    out = K.mix_film(voice, music, None, total=2.0, fade=0.0,
                     music_gain_db=0.0, voice_duck_db=6.0, guard_snr_db=0.0)
    v = np.stack([voice, voice], axis=1)
    music_part = out - v                      # 混音是线性叠加，减掉人声即背景分量
    # 人声段：取中段（避开包络起音/释放的斜坡）
    talk = music_part[int(0.35 * K.SR): int(0.45 * K.SR)]
    gap = music_part[int(1.55 * K.SR): int(1.95 * K.SR)]
    talk_rms = float(np.sqrt(np.mean(talk * talk)))
    gap_rms = float(np.sqrt(np.mean(gap * gap)))
    # 期望：人声段 ≈ 0.2*10^(-6/20)=0.100；间隙 ≈ 0.2
    assert abs(talk_rms - 0.1003) < 0.03, "人声段背景没被压到 -6 dB（RMS %.3f，期望 0.100）" % talk_rms
    assert abs(gap_rms - 0.2) < 0.03, "人声间隙背景没恢复（RMS %.3f，期望 0.200）" % gap_rms
    # SNR 口径：只统计人声活跃区（含间隙会把问题稀释掉）。
    # 这里配乐基线是 0 dB，只有 -6 dB 侧链 -> SNR 应为 -3+6 = 3 dB 左右。
    snr = K.voice_snr(voice, music_part)
    assert snr is not None and 2.0 <= snr <= 4.0, "voice_snr 口径异常（%.1f dB，期望约 3）" % (snr or -99)
    # 旁白自身电平不能被侧链动过
    voice_part = out[:, 0] - music_part[:, 0]
    seg = voice_part[int(0.35 * K.SR): int(0.45 * K.SR)]
    want = voice[int(0.35 * K.SR): int(0.45 * K.SR)]
    assert float(np.max(np.abs(seg - want))) < 1e-4, "旁白电平被侧链改变（旁白优先不能压人声）"
    return "人声段背景 %.3f / 间隙 %.3f（dB 差 %.1f）" % (
        talk_rms, gap_rms, 20 * np.log10(gap_rms / (talk_rms + 1e-9)))


def t_music_gain_default():
    """v0.7.1：配乐默认增益必须是 -12 dB（beside 人声），不是旧版的 -1.5。

    这条盯的是 `render.st_audio` 的默认值：`mus.get("gain_db", -12.0)`。
    直接读源码文本，避免"跑全链才知道默认值"。
    """
    src = open(os.path.join(HERE, "render.py"), encoding="utf-8").read()
    assert 'gain_db", -12.0' in src, "st_audio 的配乐默认增益不是 -12 dB"
    assert 'voice_duck_db", 6.0' in src, "st_audio 没有开旁白侧链（voice_duck_db=6）"
    return "配乐 -12 dB + 旁白侧链 6 dB"


def t_snr_guard():
    """v0.7.1 SNR 兜底：背景天然太响时，混音后必须自己把它压到"人声听得清"。

    构造一段人声 + 一段刻意很响的配乐（比人声高 10 dB），不设侧链、只靠 guard：
    `mix_film(guard_snr_db=12)` 后实测人声活跃区的 人声/背景 必须 >= 12 dB，
    且人声本身电平不能被兜底改过（兜底只压背景）。
    """
    import numpy as np
    sr = K.SR
    n = int(3.0 * sr)
    t = np.arange(n) / float(sr)
    voice = np.zeros(n, dtype=np.float32)
    voice[int(0.4*sr):int(2.6*sr)] = (0.2 * np.sin(2*np.pi*220*t[int(0.4*sr):int(2.6*sr)])).astype(np.float32)
    # 配乐 RMS 约 0.6，比人声（RMS 0.141）高 ~12.5 dB —— 刻意做成"盖住旁白"的反例
    loud = np.full((n, 2), 0.6, dtype=np.float32)
    out = K.mix_film(voice, loud, None, total=3.0, fade=0.0,
                     music_gain_db=0.0, voice_duck_db=0.0, voice_duck_sfx_db=0.0,
                     guard_snr_db=12.0, guard_cap_db=30.0, verbose=False)
    v2 = np.stack([voice, voice], axis=1)
    bg = out - v2
    snr = K.voice_snr(voice, bg)
    assert snr is not None and snr >= 11.9, "SNR 兜底没生效：人声/背景 %.1f dB（要求 >=12）" % (snr or -99)
    # 人声电平不能被兜底动过
    seg_v = (out[:, 0] - bg[:, 0])[int(1.0*sr):int(2.0*sr)]
    want = voice[int(1.0*sr):int(2.0*sr)]
    assert float(np.max(np.abs(seg_v - want))) < 1e-4, "SNR 兜底改到了人声电平"
    return "兜底前约 -12.5 dB -> 兜底后 %.1f dB" % snr


def t_mix_no_voice():
    """没有旁白时混音行为不变：没有 voice_env/guard 可用，背景应原样输出。"""
    import numpy as np
    n = int(0.5 * K.SR)
    music = np.full((n, 2), 0.2, dtype=np.float32)
    out = K.mix_film(None, music, None, total=0.5, fade=0.0, music_gain_db=0.0, verbose=False)
    assert abs(float(np.max(np.abs(out))) - 0.2) < 1e-5, "无旁白时配乐被改动了（峰值 %.3f）" % float(np.max(np.abs(out)))
    return "无旁白时背景不被压"

# ------------------------------------------------------------------ v0.7.2 新增（全项目深审）
def t_audio_edge_inputs():
    """39 音频入口的边界：极短旁白不能崩、NaN/Inf 必须拒绝、seg=0 不许死循环。

    这几条都是"真跑一次就会遇到"的输入。v0.7.1 及以前的行为：
      * voice_env 在极短音频上 np.convolve(mode="same") 返回核长 -> np.maximum 广播失败；
      * duck_voice 在"旁白比背景短"时包络与背景长度不同 -> 广播失败；
      * wav_write 对 NaN 做 clip 再 astype(int16)，把 NaN 静默写成 0（写出损坏 WAV 却不报错）；
      * AudioBus.place 传负起点时负下标切片 -> n=0 -> 广播崩；
      * music_bed 的 seg=0 让 t 不前进 -> while 死循环（有 100000 次上限，6 秒片要 2 分钟）。
    """
    import numpy as np
    short = np.full(600, 0.2, dtype=np.float32)          # 12.5 ms
    assert len(K.voice_env(short)) >= 1, "voice_env 对极短音频返回空包络"
    bg = np.full((K.SR, 2), 0.2, dtype=np.float32)
    got = K.duck_voice(bg, short, depth_db=6.0)
    assert got.shape == (K.SR, 2), "duck_voice 在旁白比背景短时形状不对: %r" % (got.shape,)
    m = np.full((600, 2), 0.2, dtype=np.float32)
    assert K.duck(m, short).shape == (600, 2), "duck 在短信号上形状不对"

    with tempfile.TemporaryDirectory() as d:
        for name, bad in (("nan", np.float32("nan")), ("inf", np.float32("inf"))):
            p = os.path.join(d, name + ".wav")
            try:
                K.wav_write(p, np.array([0.1, bad], dtype=np.float32))
            except ValueError:
                assert not os.path.exists(p), "wav_write 报错了却仍写出文件（%s）" % p
            else:
                raise AssertionError("wav_write 把 %s 静默写进了 WAV（应当报错）" % name)

    bus = K.AudioBus(2, K.SR, 1.0)
    bus.place(np.ones(1000, dtype=np.float32), -0.25)     # 负起点：从 0 秒开始，不许崩
    bus2 = K.AudioBus(2, K.SR, 0.3)
    try:
        bus2.music_bed([[220.0, 277.0, 330.0]], seg=0.0)
    except ValueError:
        pass
    else:
        raise AssertionError("music_bed(seg=0) 没被拒绝（会死循环）")
    return "极短旁白不崩 / NaN·Inf 拒绝 / 负起点与 seg=0 有守卫"


def t_report_single_source():
    """40 QC 报告落点只能有一个来源：--out 的目录。

    v0.7.2 修的真 bug：all/mux/qa 用 dirname(abspath(out))，accept 用
    dirname(abspath(work))/out。--out 指到 deliver/ 时，签核更新的是另一份没人
    交付的文件，交付出去的那份仍是"未签核"——正好打穿"报告是交付凭据"这条承诺。
    """
    import types
    try:
        import render
    except Exception as e:                                  # noqa: BLE001
        return "skip（import render 失败：%s）" % e
    assert hasattr(render, "_report_path"), "render 没有统一的 _report_path"
    a = types.SimpleNamespace(out=os.path.join("deliver", "film.mp4"), work="work")
    got = render._report_path(a)
    want = os.path.join("deliver", "QC-REPORT.md")
    assert os.path.abspath(got) == os.path.abspath(want), \
        "报告没落在 --out 旁边：%s（期望 %s）" % (got, want)
    src = open(os.path.join(HERE, "render.py"), encoding="utf-8").read()
    n = src.count('"QC-REPORT.md"')
    assert n == 1, "QC-REPORT.md 路径出现了 %d 处（应只有 _report_path 一处，否则又会两边漂移）" % n
    return "报告落点统一到 --out 目录（源码里只有一处拼接）"


def t_empty_ledger_not_pass():
    """41 门禁账本为空时，QC 报告不许说"通过"。

    render_report 原来 if counts.get(FAIL): ... else: **通过**——gates.json 被删、
    写坏、或根本没跑 qa 时，交付凭据会是一份宣称通过的纸。这条盯的就是那个 else。
    """
    try:
        import gates as GT
    except Exception as e:                                  # noqa: BLE001
        return "skip（没有 gates.py：%s）" % e
    with tempfile.TemporaryDirectory() as d:
        p = GT.write_report(d, os.path.join(d, "QC-REPORT.md"))
        txt = open(p, encoding="utf-8").read()
    assert "**通过**" not in txt, "空门禁账本却写成『通过』（交付凭据撒谎）"
    assert "未判定" in txt, "空门禁账本没写『未判定』（用户会以为跑过了）"
    return "0 条门禁 -> 未判定（不是通过）"


def t_stage_io_no_phantom_output():
    """42 单阶段连跑不许报"自己的产物不见了"（假 stale）。

    v0.7.2 修：stagewatch 的 STAGE_IO[style].outputs 里把 voice_plan.json 也算成
    style 的产物，而它其实是 tts 阶段写的（render.py 的 st_tts）。于是 --stage style
    连跑两次，第二次必然报"产物 voice_plan.json 不见了"；打开 --strict-stale 就变成
    硬失败——SKILL.md 第 3 步（全新项目第一条生产命令）自己走不通。
    """
    try:
        import stagewatch as SW
    except Exception as e:                                  # noqa: BLE001
        return "skip（没有 stagewatch.py：%s）" % e
    style_out = list((SW.STAGE_IO.get("style") or {}).get("outputs") or [])
    tts_out = list((SW.STAGE_IO.get("tts") or {}).get("outputs") or [])
    if "voice_plan.json" in style_out:
        assert "voice_plan.json" in tts_out, \
            "voice_plan.json 被算成 style 的产物，但没有别的 stage 认领它（假 stale 会回来）"
    dup = [(n, st) for st, io in SW.STAGE_IO.items() for n in (io.get("outputs") or [])
           if n in style_out and st != "style"]
    assert not dup, \
        "style 的产物被别的 stage 也声明成输出（会产生假 stale）：%r" % (dup,)
    return "style 产物 %s / tts 产物 %s" % (",".join(sorted(style_out)), ",".join(sorted(tts_out)))


def t_theme_overrides_linted():
    """43 theme_overrides 必须参与主题体检（否则用户能造出"看不清"的成片）。

    v0.7.2 修的真 bug：render.resolve_style 以前只体检**基础主题**
    （ST.THEMES[theme_id]），而 scenes 实际用的是 plan[theme]（基础主题 + film.json 的
    visual.theme_overrides）。于是把 ink/bg 调成低对比度时体检照样 PASS，渲完才发现字幕看不清。
    实测 overrides ink=[30,30,30]/bg=[10,10,10]：基础主题 lint 为空，最终主题 lint 报对比度 1.09。
    """
    import stylekit as ST
    base = {'title': 'x', 'style': 'auto',
            'lines': [{'id': 'l01', 'kind': 'body', 'text': 'y'}]}
    p = ST.resolve_script_style(dict(base, visual={'theme_overrides': {
        'ink': [30, 30, 30], 'bg': [10, 10, 10]}}))
    assert ST.theme_lint(ST.THEMES[p['theme_id']]) == [], \
        "前提变了：基础主题本身就有 lint 问题，这条断言失去意义"
    bad = ST.theme_lint(p['theme'])
    assert bad, '低对比度的 theme_overrides 没被 lint 抓到（用户能造出看不清的字幕）'
    src = open(os.path.join(HERE, "render.py"), encoding="utf-8").read()
    assert "ST.theme_lint(plan.get('theme')" in src or 'ST.theme_lint(plan.get("theme")' in src, \
        "render.resolve_style 仍在体检基础主题而不是最终主题"
    return "最终主题参与体检（%s）" % "；".join(bad)[:60]


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
    ("15 立体声总线", t_stereo_bus),
    ("16 cue_time 三种写法", t_cue_time),
    ("17 CueBus 落盘/读回 schema", t_cue_json),
    ("18 duck 让位", t_duck),
    ("19 mix_film 三总线", t_mix_film),
    ("20 sfxkit 配方与确定性", t_sfxkit),
    ("21 scorekit 确定性与时长", t_scorekit),
    ("22 webengine 页面契约", t_webengine),
    ("23 qa_frames 函数齐全", t_qa_frames),
    ("24 配乐来源判定（off 不读旧配乐）", t_music_plan),
    ("25 风格层：8 套主题过对比度", t_style_kits),
    ("26 风格 legacy 兼容", t_style_legacy),
    ("27 内容指纹确定性", t_style_determinism),
    ("28 声线计划", t_voice_plan),
    ("29 字体族与主题字幕", t_font_family),
    ("30 门禁账本与签核", t_gates),
    ("31 阶段新鲜度守卫", t_stagewatch),
    ("32 并行渲染主题序列化", t_parallel_frames),
    ("33 音效跨片可区分（v0.7 内容派生 seed）", t_sfx_content_seed),
    ("34 配乐/音效片内分离（v0.7 duck 加深）", t_audio_separation),
    ("35 旁白优先（v0.7.1 背景让位与恢复）", t_voice_priority),
    ("36 旁白优先默认值（配乐 -12 dB + 侧链）", t_music_gain_default),
    ("37 旁白清晰度 SNR 兜底（背景太响时自动补压）", t_snr_guard),
    ("38 无旁白时混音不受影响", t_mix_no_voice),
    ("39 音频边界：极短/NaN·Inf/负起点/seg=0", t_audio_edge_inputs),
    ("40 QC 报告落点只有一个来源", t_report_single_source),
    ("41 空门禁账本不许写成通过", t_empty_ledger_not_pass),
    ("42 单阶段产物归属（防假 stale）", t_stage_io_no_phantom_output),
    ("43 theme_overrides 参与主题体检", t_theme_overrides_linted),
]


def main():
    global VERBOSE
    _setup_stdio()
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
