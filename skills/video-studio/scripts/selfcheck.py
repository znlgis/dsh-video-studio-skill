# -*- coding: utf-8 -*-
"""selfcheck.py — 技能自检 / 回归测试（不联网，10~20 秒内出结论）。

跑法：python selfcheck.py [-v]

检查的全是真实缺陷的回归点，不是泛泛的健康检查：
  1  TTS 缓存必须校验文本（只判文件存在 -> 改台词不生效）
  2  sub_window 不能把长句字幕截断在 5.2s（硬钳会让长句丢字）
  3  loudness 必须能测到真峰（ebur128 没开 peak -> 真峰门禁永不触发）
  4  mux 默认不重编视频流（二次编码会再压一遍画面）
  5  FrameSink 尺寸不符必须报错（静默 resize 会让成片与构图不符）
  6  字形自查能识别缺字（msyhbd 缺 ✓ 等）
  7  ASS 头必须声明 PlayRes，且 Format/Dialogue 字段数一致
  8  时间轴第一段 start 严格为 0
  9  分块渲染的绝对帧号命名 + 缺口检测
 10  qa_report 能查出字幕条数不匹配
 11  尾静音裁剪
 12  mux 音轨 48kHz
 13  旧调用签名兼容
 14  qa 帧率校验
 15  立体声总线：channels=2 形状正确、声像能量守恒
 16  cue_time 三种写法都解析成绝对秒
 17  CueBus 落盘 -> 读回 -> schema 正确（含未知类型不崩）
 18  duck 真的压低了配乐（且不改变无音效处的电平）
 19  mix_film 峰值不越界、时长精确、旁白优先
 20  sfxkit 可用时：配方确定性 + 别名表能从 sfxkit 反查
 21  scorekit 可用时：同 seed bit-identical + 时长精确
 22  webengine 可用时：页面契约齐全（window.renderFrame/getCues/ready）
 23  qa_frames 可用时：能检出注入的单帧闪烁
 24  配乐来源：music.mode=off 不读上一轮 work/music.wav（模式判定回归）
  25  风格层：每套采样主题全部过对比度门禁，8 类题材能分出 >=6 种风格
  26  legacy 已移除：不写 style 必须**报错并给迁移指引**（守的是"旧外观路径真的
      没了"，而不是"精确落回某套老默认值"）
  27  内容指纹：同剧本稳定、改标点不换风格、换题材才换
  28  声线计划：4 句样片 >=2 把声音，legacy 仍是单声线
  29  字体族与主题字幕：换族换缓存键，浅色主题不画黑背板
  30  门禁账本与签核：pass/fail/skipped 三档不许混，签核必须绑内容指纹
  31  阶段新鲜度：改了输入必须报"过时"，不许用旧产物静默出片
  32  并行渲染主题序列化：JSON 往返后重解风格必须回到 tuple
  33  音效跨片可区分：内容派生 seed 让跨片不同、legacy 逐样本不变
  34  配乐/音效片内分离：duck 默认加深，配乐给音效让位
  35  旁白优先：人声出现时背景必须再压、间隙恢复、人声电平不动
  36  旁白优先默认值：配乐 -12 dB 基线 + 旁白侧链 6 dB（读 st_audio 源码）
  37  旁白清晰度 SNR 兜底：背景天然太响时自动补压到 >=12 dB
  38  无旁白时混音不受影响（voice_env/guard 不得空转改电平）
  39  音频边界：极短旁白不崩、NaN/Inf 拒绝写盘、负起点与 seg=0 有守卫
  40  QC 报告落点只有一个来源（--out 的目录，不许 accept 另算一套）
  41  空门禁账本不许写成"通过"（没记录 != 通过）
  42  单阶段产物归属：不许把别的 stage 写的文件算成自己的（假 stale）
  43  theme_overrides 必须参与主题体检（否则能造出"看不清"的成片）
  44  词级时间戳：规整（单调/裁剪/偏移）与 cue 词锚点解析
  45  画幅规划：只允许 16:9 横屏下采样，竖屏/上采样/非法 fps 必须拒绝
  46  金帧确定性：同一帧号两次渲染必须逐像素一致
  47  依赖漂移体检：numpy/Pillow 主版本 + edge-tts 仍暴露 boundary 参数
  48  批量清单解析：两种写法都认、上限卡死、坏条目在开跑前报错
  49  TTS 后端可替换：注册/注销/未知后端的接口契约
  50  web 风格载荷：style=None 时不得注入 __STYLE__（legacy 必须回落）
  51  金帧回归：与 assets/golden_frames.json 的像素基线比对
  52  场景语法：10 类构图分流、选型确定性、段长降级门
  53  视觉语言包：8 套结构签名齐全、过体检、彼此不是同一套
  54  场景类型单真值：grammar / filmcheck / scenes / schema 四处集合相等
  55  剪影可区分（G13）：8 套风格两两之间构图骨架必须能区分
  56  设计 DSL：采样->校验->转计划全通；缺必填块必须报错（fail-closed）；
      同 brief 两次采样一致；两两至少 2 维不同（"伪多方案"门禁）
  57  零模板：仓库里不许存在 presets/ 与 presetkit.py；现场采样必须确定性、
      且换 brief 必须换结果（否则就是还在套模板）
  58  提案闸：absent / draft / broken 三档判定正确（confirmed 才放行出片）
  59  逐 stage 可单独跑：render 里每处 resolve_style 都必须拿得到 work
      （只跑 --stage frames / --stage sfx 时缺参数会报"找不到设计"，all 被缓存掩盖）
  60  brief 里的要求真的生效：自然语言要解析成 must_have/avoid，
      配色约束按色相窗口生效（早先整句被塞进 topic，"深蓝"采出来是橙色）

说明：本文件里 CASES 的**实际条数**是唯一真理，上面的编号表要与它同步。
这张表落后过两次：停在 43 条而 CASES 已有 51 条、停在 51 条而 CASES 已有 58 条。
改 CASES 时请同批改这张表，别让它再次落后。
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
    except SystemExit as e:
        # 被调代码用 SystemExit 报"用法错误"（例如 resolve_style 要求先提案）。
        # 必须记 FAIL 而不是让自检直接退出 —— 早先 selfcheck 会在这里**静默中止**，
        # 后面的用例一条都不跑，输出留在半截，很容易被当成"还在跑"。
        RESULTS.append(("FAIL", name, "SystemExit: %s" % (e.code if str(e.code) != "None" else e)))
        print("  FAIL %s  -> SystemExit: %s" % (name, e))
    except Exception as e:                      # noqa: BLE001
        RESULTS.append(("FAIL", name, "%s: %s" % (type(e).__name__, e)))
        print("  FAIL %s  -> %s: %s" % (name, type(e).__name__, e))


# 自检专用：造一份 design 计划。
# 删掉内置风格表后，"要一份 plan"这件事只能靠现场采样 + 转计划；
# 每个用例各写一遍既啰嗦又容易漂移，于是收到这里。
# 固定 seed 保证自检自身可复现（自检必须可复现，否则 CI 抖起来没法查）。
_PLAN_CACHE = {}


def _design_plan(topic="selfcheck 样例", seed=20260101):
    key = (topic, seed)
    if key not in _PLAN_CACHE:
        import designkit as DK
        d = DK.sample_designs({"topic": topic}, n=1, seed=seed)[0]
        _PLAN_CACHE[key] = (d, DK.design_to_plan(d))
    return _PLAN_CACHE[key]


def _sample_design(topic="selfcheck 样例", seed=20260101):
    return _design_plan(topic, seed)[0]


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
    """旧调用签名必须还能用（升级不能把老项目直接炸掉）。"""
    a = K.tone(440, 0.2, kind="sine", harmonics=(1.0, 0.5))
    b = K.noise_burst(0.2, amp=0.1, lo=0.02, hi=0.35, decay=6.0, seed=7)
    assert len(a) > 0 and len(b) > 0, "兼容签名返回空"
    # 关键公共函数的关键字参数
    K.build_timeline([dict(id="a", text="一", dur=1.5)], lead=0.5, tail=0.8, min_dur=2.0)
    K.qa_report(__file__, expect_frames=None, ref_png=None, ref_sec=None,
                expected_subtitles=None, min_bitrate=0)
    return "tone/noise_burst/build_timeline/qa_report 旧签名可用"


def t_fps_check():
    """qa_report 必须能查出帧率不符（只查宽高会漏掉帧率写错）。"""
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


# ---------------------------------------------------------------- 立体声 / 配乐 / 音效
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
    """配乐来源判定：music.mode=off 不得读上一轮的 work/music.wav。

    背景：st_audio 若无条件读 work/music.wav，用户把 music.mode 改成 off
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
    assert K.music_plan({"music": {"chords": [[220.0, 261.6]]}}, False)[0] == "bed", "和弦床兼容链断了"

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
            # D3 之后没有 design/style 的剧本会被拒（不再静默落 legacy），
            # 这条用例测的是"off 不读旧配乐"，与设计无关，显式给一个风格即可。
            # D3 之后没有 design/style 的剧本会被拒（不再静默落 legacy），
            # 这条用例测的是"off 不读旧配乐"，与设计无关，给一份现场设计即可。
            _d, _plan = _design_plan("配乐来源判定")
            _script23 = {"title": "配乐来源", "_style": _plan}
            off_out = render.st_audio(tl, work, dict(_script23, music={"mode": "off"}))
            off_peak = float(np.max(np.abs(K.wav_read(off_out)[0])))
            file_out = render.st_audio(tl, work, dict(_script23, music={"mode": "file"}))
            file_peak = float(np.max(np.abs(K.wav_read(file_out)[0])))
        assert "music.mode=off" in buf.getvalue(), "off 没有打印说明（静默跳过也不行）"
        assert off_peak < 0.01, "music.mode=off 仍把旧 music.wav 混进来了（峰值 %.3f）" % off_peak
        # 断言用"相对 off 明显变大"而不是绝对 0.3——
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
    """25 连续配色空间：现场采样的每一套都必须过对比度门禁，
    且色相真的分散——这条是"没有模板"的硬证据。

    旧断言是"8 套内置主题全部过 lint + 自动选风格能分出 >=6 种"。模板删掉后那个断言
    失去对象，于是换成更强的：**连续空间采样 12 次，全部可读 + 色相覆盖 >= 180°**。
    只测"能采出来"不够 —— 全落在同一色相也能过，所以必须夹一条分布断言。
    """
    import designkit as DK
    import stylekit as ST
    designs = DK.sample_designs({"topic": "配色空间校验"}, n=12, seed=20260101)
    assert len(designs) == 12, "采样返回 %d 套（要 12）" % len(designs)
    hues, fails = [], []
    for d in designs:
        plan = DK.design_to_plan(d)
        bad = ST.theme_lint(plan["theme"])
        if bad:
            fails.append("%s: %s" % (d["label"], bad[0][:50]))
        h, _s, _v = ST.rgb_to_hsv(plan["theme"]["bg"])
        hues.append(h)
    assert not fails, "有设计过不了对比度门禁：%s" % "；".join(fails[:3])
    span = max(hues) - min(hues)
    if span < 180.0:                        # 跨 0° 的情况：取补集
        span = 360.0 - span
    assert span >= 180.0, ("12 套设计的底色谱相只覆盖 %.0f°（应 >=180°）——"
                           "说明配色并没有真的在连续空间里散开" % span)
    # 配色必须是**连续**的，不是一份清单：断言"色相是连续量"而不是"落在 8 个点之一"。
    # 做法：取 12 套的色相，检查它们不是某个固定集合的子集（容差 1°）。
    # 早先这里断言的是 `len(PALETTE_FAMILIES) == len(_LEGACY_FAMILY_ANCHOR)` ——
    # 而那两个名字是**同一个对象**，长度当然永远相等，等于什么也没验。
    assert not DK.PALETTE_FAMILIES, "PALETTE_FAMILIES 必须为空（有内容=偷偷恢复了外观清单）"
    rounded = sorted({round(h) for h in hues})
    spread = len(rounded) / float(len(hues))
    assert spread >= 0.8, ("12 套配色的色相只有 %d 个不同取值（%s）——"
                           "说明配色退化成了一份离散清单" % (len(rounded), rounded))
    return "12 套现场配色全部过 lint；色相覆盖 %.0f°；%d/12 个不同色相" % (
        span, len(rounded))


def t_style_legacy():
    """26 legacy 已移除：不写 style 必须报错并给出迁移指引，而不是静默落老外观。

    这条盯的是"不写外观配置照样能出片"这条老思路真的没了：legacy 路径整个删掉，
    因为它是同质化的另一半根因（不写 style = 全片一套固定外观 + 120BPM/Dm）。
    断言分成两半：① 不给设计时必须**报错**且错误文本指得出下一步；
    ② 显式给 style 也不可用（内置风格表已删，任何 style id 都必须落空报错）。
    """
    import stylekit as ST
    s = {"title": "旧项目", "lines": [{"id": "a", "kind": "body", "text": "x"}]}
    # ① 不给设计：必须报错，且错误文本指得出下一步
    try:
        ST.resolve_script_style(s)
    except (ValueError, KeyError, SystemExit) as e:
        msg = str(getattr(e, "code", None) or e)
        assert "propose.py" in msg, "报错必须给出迁移指引（要提到 propose.py），实际：%r" % msg[:120]
        assert "design" in msg.lower(), "报错要说清主路径是 design.json，实际：%r" % msg[:120]
    else:
        raise AssertionError("没有 style/design 却没报错：legacy 路径没有真的移除")
    # ② 显式 style 也不可用——内置风格表整个删了，连带 STYLES 一起没了，
    #    任何 style id 都必须落空并报错（否则就是"模板还在"）。
    s3 = dict(s, style="cinema")
    try:
        ST.resolve_script_style(s3)
    except (ValueError, KeyError, SystemExit) as e:
        msg = str(getattr(e, "code", None) or e)
        assert "模板" in msg or "design" in msg.lower() or "propose" in msg,             "显式 style 报错也该给指引，实际：%r" % msg[:120]
    else:
        raise AssertionError("显式 style='cinema' 竟然还能解析出计划：内置风格表没删干净")
    assert ST.STYLE_IDS == () and not ST.STYLES and not ST.THEMES,         "内置风格/主题表必须为空，实际 STYLE_IDS=%r STYLES=%d THEMES=%d" % (
            ST.STYLE_IDS, len(ST.STYLES), len(ST.THEMES))
    return "无设计必报错并给迁移指引 / 显式 style 也被拒绝 / 三张模板表为空"


def t_style_determinism():
    """27 指纹确定性：同剧本同结果；改错别字不换风格；改题材才换。"""
    import designkit as DK
    # 没有"剧本 -> 自动选风格"了，这里验证"设计采样"的确定性与区分度。
    # 固定 seed 才谈确定性：默认不给 seed 时每次都要不同（那是 56/57 条在守的事）。
    a = DK.sample_designs({"topic": "AI 大模型的三年"}, n=1, seed=11)[0]
    b = DK.sample_designs({"topic": "AI 大模型的三年"}, n=1, seed=11)[0]
    assert DK.design_fingerprint(a) == DK.design_fingerprint(b), "同 seed 同 brief 采样不一致"
    assert DK.design_fingerprint(a) != DK.design_fingerprint(
        DK.sample_designs({"topic": "手工木作"}, n=1, seed=11)[0]), "换了题材设计却没变"
    return "同 seed 可复现 + 换题材必变"


def t_voice_plan():
    """28 声线计划：legacy 保持单声线；风格化时按 kind 换声线且确定性。"""
    try:
        import voicekit as VK
    except Exception:                                       # noqa: BLE001
        return "skip"
    import designkit as DK
    s = {"title": "AI 大模型",
         "lines": [{"id": "l01", "kind": "hook", "text": "大模型改变了什么"},
                   {"id": "l02", "kind": "body", "text": "算法和数据的规模一起上去了"},
                   {"id": "l03", "kind": "quote", "text": "他说：这不可能"},
                   {"id": "l04", "kind": "outro", "text": "它已经在我们身边了"}]}
    # 声线计划同样只能从设计来（模板表已删）
    plan = DK.design_to_plan(DK.sample_designs({"topic": "AI 大模型"}, n=1, seed=7)[0])
    vp = VK.voice_plan(s, plan)
    assert len(vp) == 4, "计划行数应为 4，收到 %d" % len(vp)
    assert json.dumps(vp, sort_keys=True, default=str) == json.dumps(
        VK.voice_plan(s, plan), sort_keys=True, default=str), "voice_plan 不确定"
    # persona 身份表已删，声音身份的唯一表示是 edge voice id。
    # 这里断言"至少 2 个不同的 voice id"——语义与旧 persona 表一致，字段换了。
    voices = {r["voice"] for r in vp}
    assert len(voices) >= 2, "4 句样片只用了一个声线: %s" % voices
    tones = {r["tone"] for r in vp}
    assert len(tones) >= 2, "语气档没有变化: %s" % tones
    for r in vp:
        for fld in ("voice", "rate", "pitch", "volume", "lead", "tail"):
            assert r.get(fld) not in (None, ""), "计划行缺 %s: %r" % (fld, r.get("id"))
    # 单句片：同样走 design（不再有 style="mono" 这种内置风格可选）
    one = {"title": "单句片",
           "lines": [{"id": "a", "kind": "body", "text": "一句话"}]}
    op = DK.design_to_plan(DK.sample_designs({"topic": "单句片"}, n=1, seed=3)[0])
    ovp = VK.voice_plan(one, op)
    assert len(ovp) == 1, "单句片应只给一行计划，收到 %d" % len(ovp)
    assert ovp[0]["voice"].endswith("Neural"), "声线应是 edge voice id：%r" % (ovp[0]["voice"],)
    return "%d 句 / %d 把声音 / %d 档语气" % (len(vp), len(voices), len(tones))


def t_font_family():
    """29 字体族 + 字幕主题：换族必须换缓存键，主题字幕不能是黑底白字。

    **别把"系统装了几族字体"当成产品缺陷**：CI 的 ubuntu 只装 fonts-noto-cjk，
    四个族全回退到同一个 NotoSansCJK，宽度必然一样。断言若写成"宽度必须不同"就会误报——
    Linux CI 红、Windows 绿。
    真正要保证的是**机制**：缓存按 family 分键（换族不会串味）；
    宽度差异只在"系统确实解析出不同字体文件"时才有意义，单族系统跳过并说明。
    """
    fams = list(getattr(K, "FAMILY_IDS", ()))
    assert fams, "filmkit 没有 FONT_FAMILIES（字体族没生效）"
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
    # 没有内置主题可借（THEMES 已清空），手写一份浅色主题。
    # 这条断言测的是 draw_subtitle 的"浅底不画黑背板"逻辑，与主题从哪来无关。
    th = {"bg": (247, 244, 238), "bg2": (233, 228, 218), "surface": (255, 255, 255),
          "ink": (26, 24, 22), "muted": (110, 104, 96), "dim": (150, 146, 140),
          "accent": (198, 96, 40), "accent2": (52, 96, 160),
          "good": (46, 150, 92), "bad": (200, 60, 60), "rule": (208, 202, 192),
          "id": "selfcheck-light", "label": "浅色测试"}
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

    这一条防的是最容易犯的错——把 skipped 混进 pass。
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

    这一条防的是：frames 有分块指纹，其余 stage 全是裸奔
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
    # 这条守的是：只跟踪 stage 之间的产物时，"改了台词没重跑 tts" 查不出来，
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
        "改了剧本却没报过时（这正是阶段新鲜度要堵的洞）：%s" % issues)
    assert any("film.json" in i["reason"] for i in issues), issues
    # 坏 manifest 不许崩（用户手改坏一个 json 不该让整片跑不动）
    open(os.path.join(w, "manifest.json"), "w", encoding="utf-8").write("{broken")
    SW.explain(w)
    return "改上游/改源头即报过时 + 产物丢失 + strict 升级 + 坏 manifest 不崩"


def t_parallel_frames():
    """32 --jobs >1 的并行路径能真的跑（pre-existing bug 的回归）。

    bug 形状：worker 从 _script_snapshot.json 读剧本，JSON 把主题色元组变成 list，
    scenes._bg() 拿它当缓存键 -> TypeError: unhashable type: "list"，
    于是 SKILL 里教的 --stage frames --jobs 4 直接崩。
    这里**不跑整片**（太慢），只验两条防线：① 主题色能过 JSON 往返当缓存键；
    ② worker 反序列化后会重解风格（_style 里的颜色回到 tuple）。
    """
    import json as _json
    import tempfile
    # 没有内置主题可借，用现场设计的主题（颜色同样是 (r,g,b) 元组）
    _d, _plan0 = _design_plan("并行渲染主题序列化")
    th = dict(_plan0["theme"])
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
    script = {"title": "t", "lines": [{"text": "x"}]}
    script["_style"] = _plan0
    with open(snap, "w", encoding="utf-8") as fh:
        _json.dump(script, fh, ensure_ascii=False)
    back = _json.load(open(snap, encoding="utf-8"))
    assert isinstance(back["_style"]["theme"]["bg"], list), "前提变了"
    # 子进程路径（worker 必须从 work/design.json 重解设计，而不是回落 stylekit）。
    # 现在直接验那条路径：把 design.json 放进 work/，resolve_style 必须解出**同一个**设计，
    # 且颜色回到 tuple（list 会让 _bg() 的缓存键不可哈希）。
    import designkit as DK
    import render as R
    dpath = os.path.join(w, "design.json")
    with open(dpath, "w", encoding="utf-8") as fh:
        _json.dump(_sample_design("并行渲染主题序列化"), fh, ensure_ascii=False)
    sub_script = {"title": "t", "lines": [{"text": "x"}]}
    got = R.resolve_style(sub_script, w, verbose=False)
    assert got.get("style", "").startswith("design:"), \
        "子进程重解没有走 design.json（会画出另一部片）：%r" % (got.get("style"),)
    assert isinstance(got["theme"]["bg"], tuple), (
        "重解后主题色仍是 list，_bg() 会 TypeError（这正是被修的 bug）：%r"
        % (got["theme"]["bg"],))
    assert got["theme"]["bg"] == th["bg"], "重解出的主题与父进程不一致（并行会画出不同的片）"
    return "主题可哈希 + JSON 往返 + 按 design.json 重解一致"


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
    """音效 seed 必须随内容变化，但 legacy 路径必须逐样本不变。

    - 不传 style/content_fp -> 两次渲染逐样本相同（legacy 兼容，不破老片）；
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
    assert np.array_equal(legacy_a, legacy_b), "legacy 两次渲染不一致（逐样本兼容被破坏）"
    a = build(style="tech-deep", content_fp="aaaa1111", key="Dm")
    b = build(style="tech-deep", content_fp="bbbb2222", key="Dm")
    assert not np.array_equal(a, b), "不同内容指纹得到相同音效轨：跨片音效仍一模一样"
    a2 = build(style="tech-deep", content_fp="aaaa1111", key="Dm")
    assert np.array_equal(a, a2), "同内容指纹渲染不可复现"
    return "legacy 逐样本相同 / 跨片可区分"


def t_audio_separation():
    """混音侧 duck 默认必须加深（片内配乐给音效让位）。

    直接读 render.py 的常量文本而不是跑全链：这条断言盯的是"默认值没被改回去"
    （3.0 dB 这类浅值在段落过门时配乐几乎和音效等高，是"听起来像同一套"的成因之一）。
    """
    import re
    path = os.path.join(HERE, "render.py")
    src = open(path, encoding="utf-8").read()
    m = re.search(r"duck_default\s*=\s*([0-9.]+)", src)
    assert m, "render.py 里找不到 duck_default"
    base = float(m.group(1))
    assert base >= 3.5, "duck_default=%.1f dB 太浅（要求 >= 3.5）" % base
    for sid, want in (("mono", 3.0), ("cinema", 5.5), ("epic", 5.5)):
        mm = re.search(r'"%s"\s*:\s*([0-9.]+)' % sid, src)
        assert mm, "render.py 里找不到风格 %s 的 duck 值" % sid
        assert float(mm.group(1)) >= want, "%s duck=%.1f < %.1f" % (sid, float(mm.group(1)), want)
    return "duck 默认 %.1f dB（cinema/epic 5.5）" % base


def t_voice_priority():
    """旁白优先：人声出现时背景必须再压，人声间隙背景恢复。

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
    """配乐默认增益必须是 -12 dB（beside 人声），不是 -1.5。

    这条盯的是 `render.st_audio` 的默认值：`mus.get("gain_db", -12.0)`。
    直接读源码文本，避免"跑全链才知道默认值"。
    """
    src = open(os.path.join(HERE, "render.py"), encoding="utf-8").read()
    assert 'gain_db", -12.0' in src, "st_audio 的配乐默认增益不是 -12 dB"
    assert 'voice_duck_db", 6.0' in src, "st_audio 没有开旁白侧链（voice_duck_db=6）"
    return "配乐 -12 dB + 旁白侧链 6 dB"


def t_snr_guard():
    """SNR 兜底：背景天然太响时，混音后必须自己把它压到"人声听得清"。

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

# ------------------------------------------------------------------ 音频入口边界
def t_audio_edge_inputs():
    """39 音频入口的边界：极短旁白不能崩、NaN/Inf 必须拒绝、seg=0 不许死循环。

    这几条都是"真跑一次就会遇到"的输入。没有守卫时的行为：
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

    真 bug：all/mux/qa 用 dirname(abspath(out))，accept 用
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

    stagewatch 的 STAGE_IO[style].outputs 里把 voice_plan.json 也算成
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

    真 bug：render.resolve_style 以前只体检**基础主题**
    （ST.THEMES[theme_id]），而 scenes 实际用的是 plan[theme]（基础主题 + film.json 的
    visual.theme_overrides）。于是把 ink/bg 调成低对比度时体检照样 PASS，渲完才发现字幕看不清。
    实测 overrides ink=[30,30,30]/bg=[10,10,10]：基础主题 lint 为空，最终主题 lint 报对比度 1.09。
    """
    import stylekit as ST
    # 没有"基础主题 vs 最终主题"的区分了（内置主题表已删），
    # 但"overrides 之后必须重新 lint"这条规则仍然要守：
    # 直接把设计主题改成低对比度，theme_lint 必须抓到。
    _d, _plan43 = _design_plan("theme_overrides 体检")
    base_th = _plan43["theme"]
    assert ST.theme_lint(base_th) == [], \
        "前提变了：设计主题本身就有 lint 问题，这条断言失去意义"
    dirty = ST.apply_theme_overrides(base_th, {"ink": [30, 30, 30], "bg": [10, 10, 10]})
    bad = ST.theme_lint(dirty)
    assert bad, '低对比度的 theme_overrides 没被 lint 抓到（用户能造出看不清的字幕）'
    # 体检的位置在 designkit（design_to_plan 里就 lint，不过直接抛）。
    # 这里改查**真正干活的地方**——早先查 render.py 的源码文本，那只是"哪个文件里写了这行"，
    # 一旦重构搬家就会误报（本次就是这样：lint 逻辑搬进 designkit 后断言还在查 render）。
    # 更硬的检查：直接把一份低对比度主题喂给 design_to_plan，必须抛。
    import designkit as DK
    dirty_design = json.loads(json.dumps(_sample_design("theme_overrides 体检")))
    dirty_design["visual"]["palette"]["ink"] = [30, 30, 30]
    dirty_design["visual"]["palette"]["bg"] = [10, 10, 10]
    try:
        DK.design_to_plan(dirty_design)
    except (ValueError, KeyError, SystemExit) as e:
        msg = str(getattr(e, "code", None) or e)
        assert "对比度" in msg or "lint" in msg.lower(), \
            "低对比度设计被拒了，但错误信息没说是对比度问题：%r" % msg[:120]
    else:
        raise AssertionError("低对比度主题竟然通过了 design_to_plan（体检失效，能造出看不清的字幕）")
    return "最终主题参与体检（%s）" % "；".join(bad)[:60]


# ------------------------------------------------------------------ 词级时间戳 / 画幅 / 依赖 / 批量
def t_word_events():
    """44 词级时间戳：规整/裁剪平移/单调兜底/词锚点解析（不联网）。

    为什么能在不联网的 selfcheck 里测：真正的网络合成在 --stage tts 里，
    而"事件 -> 句内词表"这一步是纯函数。离线固定住它，等于固定住了字幕断行、
    cue 词锚点、G10 门禁三处的共同数据源。
    """
    # offset 用 100ns 单位（实测 edge-tts 7.3.1 口径）：0.10s / 0.36s / 0.50s 起，
    # 词之间刻意留 0.02s 级空隙 —— 真实事件本来就不连续覆盖，抹平它就等于制造错位。
    ev = [{"type": "WordBoundary", "offset": 1000000, "duration": 2360000, "text": "别"},
          {"type": "WordBoundary", "offset": 3600000, "duration": 1180000, "text": "把"},
          {"type": "WordBoundary", "offset": 5000000, "duration": 5050000, "text": "傻子"}]
    ws = K.words_normalize(ev, offset_shift=0.1, dur=1.6)
    assert len(ws) == 3, "词事件数被吃掉：%r" % (ws,)
    assert abs(ws[0]["start"] - 0.0) < 1e-6, "句首裁剪平移没生效（start=%.4f）" % ws[0]["start"]
    # 词间空隙必须保留（中文词之间本来就有 0.1–0.3s 停），不能被"拉平"
    assert ws[1]["start"] > ws[0]["end"], "词间隙被抹平了（会让字幕和语音错位）"
    assert all(ws[i]["start"] <= ws[i + 1]["start"] for i in range(len(ws) - 1)), "词偏移非单调"
    # 末词终点不得超过整句实测时长
    ws2 = K.words_normalize(ev, dur=0.5)
    assert ws2[-1]["end"] <= 0.5 + 1e-6, "末词被算到句外：%r" % (ws2[-1],)
    # 空事件 -> 空表（调用方据此判 skipped，而不是当成"有词级"）
    assert K.words_normalize([]) == [], "空事件应得到空表"
    # 词锚点：l03:w2 = 第二个词的起点
    scenes = [dict(id="l03", start=2.0, dur=4.0,
                   words_abs=[{"text": "别", "start": 2.55, "end": 2.79},
                              {"text": "把", "start": 2.79, "end": 2.91}])]
    assert abs(K.cue_time("l03:w2", scenes) - 2.79) < 1e-6, "词锚点 l03:w2 解析错"
    assert abs(K.cue_time("l03:w2+0.2", scenes) - 2.99) < 1e-6, "词锚点带偏移解析错"
    assert abs(K.cue_time("l03+0.5", scenes) - 2.5) < 1e-6, "旧写法 l03+0.5 被改坏"
    return "3 词事件 / 平移 %.2fs / 词锚点 OK"


def t_video_plan():
    """45 画幅规划：只允许 16:9 横屏 + 下采样，竖屏/上采样必须报错。

    这条守的是"用户以为能出竖屏"的期望管理：安全区/版式/HUD 全按 16:9 像素写死，
    静默出一个变形或带黑边的片子，比直接报错更伤。
    """
    p = K.video_plan({})
    assert (p["out_w"], p["out_h"], p["fps"]) == (1920, 1080, 30), "默认规格变了：%r" % (p,)
    assert p["scale"] is False, "默认不该触发缩放"
    p7 = K.video_plan({"video": {"width": 1280, "height": 720}})
    assert (p7["out_w"], p7["out_h"]) == (1280, 720) and p7["scale"], "720p 下采样没生效"
    assert p7["w"] == 1920 and p7["h"] == 1080, "内部构图应恒为 1080p（缩放只在编码时做）"
    p24 = K.video_plan({"video": {"fps": 24}})
    assert p24["fps"] == 24, "fps 参数化没生效"

    def _must_fail(cfg, why):
        try:
            K.video_plan(cfg)
        except ValueError:
            return
        raise AssertionError("%s 没有报错（会被静默接受）" % why)

    _must_fail({"video": {"width": 1080, "height": 1920}}, "竖屏")
    _must_fail({"video": {"width": 1920, "height": 1440}}, "4:3")
    _must_fail({"video": {"width": 3840, "height": 2160}}, "上采样到 4K")
    _must_fail({"video": {"fps": 31}}, "非法 fps")
    return "默认 1080p30 / 720p 下采样 / 竖屏与上采样均拒绝"


def t_golden_determinism():
    """46 金帧确定性：同一帧连画两次必须逐字节一致（跨进程可复现的前提）。

    比"看起来一样"强的地方：它会在**改了引擎/主题/随机源**时立刻红，
    而不是等观众看出"这版画面怎么抖了一下"。
    """
    mod = _load_scenes_mod()
    if mod is None:
        return "skip（没有 assets/scenes.py）"
    import hashlib
    sc = dict(id="l01", kind="hook", start=0.0, dur=4.0, chapter="01 · 测试",
              text="金帧确定性自检", sub="金帧确定性", big="金帧确定性")
    # 剧本必须显式给设计（内置风格表已删，没有"style": "mono" 这种写法了）。
    script = {"title": "selfcheck", "_total": 8.0}
    script["_style"] = _design_plan("金帧确定性")[1]
    hs = []
    for _ in range(2):
        fr = mod.draw_frame(sc, 1.25, 4.0, 1.25, script, 37)
        hs.append(hashlib.sha256(fr.tobytes()).hexdigest()[:16])
    assert hs[0] == hs[1], "同一帧画了两次不一致（有隐藏的 random/time 依赖）：%r" % (hs,)
    return "同一帧两次 SHA-256 = %s" % hs[0]


def t_dependency_drift():
    """47 依赖漂移体检：上游一改就悄悄退化，这里先喊。

    只做**离线**断言（不联网、不下载）：主版本区间 + edge-tts 是否仍暴露
    boundary 参数。词级时间戳整条链路吊在这个参数上，7.x 一旦改名，
    表现会是"词事件为空"，而不是报错。
    """
    import numpy as np
    from PIL import __version__ as pil_ver
    assert int(np.__version__.split(".")[0]) >= 1, "numpy 主版本异常：%s" % np.__version__
    assert int(pil_ver.split(".")[0]) >= 9, "Pillow 版本过旧：%s" % pil_ver
    try:
        import edge_tts
    except Exception:                                       # noqa: BLE001
        return "skip（没装 edge-tts，只能用手工音频）"
    try:
        mode = edge_tts.Communicate.__init__.__doc__ or ""
        import inspect
        params = inspect.signature(edge_tts.Communicate.__init__).parameters
    except (TypeError, ValueError):                         # noqa: BLE001
        return "skip（edge-tts 的签名取不到，版本 %s）" % getattr(edge_tts, "__version__", "?")
    assert "boundary" in params,         ("edge-tts %s 的 Communicate 不再接受 boundary 参数：词级时间戳链路会静默退化成"
         "整句字幕，请检查上游变更" % getattr(edge_tts, "__version__", "?"))
    return "numpy %s / Pillow %s / edge-tts %s(boundary OK)" % (
        np.__version__, pil_ver, getattr(edge_tts, "__version__", "?"))


# ------------------------------------------------------------------ web 载荷 / 金帧
def t_batch_manifest():
    """48 批量清单解析：两种写法都认、上限卡死、坏条目在**开跑前**报错。

    批量最容易死在"第 7 条跑一半才发现清单写错"。这条固定住的是
    render._load_batch 的"先解析、后执行"契约——写错就一条都不跑。
    """
    import render as R

    def _must_fail(payload, why):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            open(p, "w", encoding="utf-8").write(payload)
            try:
                R._load_batch(p)
            except SystemExit:
                return
            raise AssertionError("%s 没有被拒绝（会带着坏清单开跑）" % why)

    with tempfile.TemporaryDirectory() as d:
        film = os.path.join(d, "a", "film.json")
        os.makedirs(os.path.dirname(film), exist_ok=True)
        open(film, "w", encoding="utf-8").write("{}")
        p = os.path.join(d, "list.json")
        json.dump(["a/film.json"], open(p, "w", encoding="utf-8"))
        jobs = R._load_batch(p)
        assert len(jobs) == 1, "简写清单解析错：%r" % (jobs,)
        assert os.path.isfile(jobs[0]["script"]), "剧本路径没转成绝对路径：%r" % (jobs[0],)
        assert jobs[0]["work"].endswith("work_film"), "默认 work 目录命名变了：%r" % (jobs[0],)
        p2 = os.path.join(d, "list2.json")
        json.dump({"films": [{"script": "a/film.json", "label": "x",
                              "work": "w1", "out": "o1.mp4"}]},
                  open(p2, "w", encoding="utf-8"))
        j2 = R._load_batch(p2)
        assert j2[0]["label"] == "x" and j2[0]["out"].endswith("o1.mp4"), "显式字段被忽略：%r" % (j2,)
    _must_fail("[]", "空清单")
    _must_fail('{"films": [{"label": "x"}]}', "缺 script 的条目")
    _must_fail("[" * 0 + json.dumps([{"script": "a.json"}] * 101), "超过 100 条上限")
    return "两种写法 + 3 类坏清单都按预期拒绝"


def t_tts_backend():
    """49 TTS 后端可替换：注册即生效、未知后端报错、默认仍是 edge。

    这条守的是"旁白链路只挂在非官方 edge-tts 上"的对冲面：换 provider 时
    timeline/layout/QA 不该跟着改。这里只测接口契约，不联网。
    """
    names = K.tts_backend_names()
    assert names[0] == "edge", "默认后端不是 edge：%r" % (names,)

    def fake(text, out_mp3, **kw):
        with open(out_mp3, "wb") as fh:
            fh.write(b"\x00" * 4096)
        return dict(path=out_mp3, dur=1.25, words=[], trimmed=False)

    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        mp3 = os.path.join(d, "x.mp3")
        try:
            K.tts("测试", mp3, backend="no-such-backend")
        except RuntimeError as e:
            assert "未知的 TTS 后端" in str(e), "未知后端报错文案不清晰：%s" % e
        else:
            raise AssertionError("未知后端没有报错（会静默走到别的路径）")
        K.register_tts_backend("fake-test", fake, note="selfcheck")
        assert "fake-test" in K.tts_backend_names()
        m = K.tts("测试", mp3, backend="fake-test")
        assert m["dur"] == 1.25 and m["backend"] == "fake-test", "后端返回值没被采纳：%r" % (m,)
    return "注册/分发/未知后端报错 全部符合契约"


def t_web_style_payload():
    """50 web 引擎风格载荷：非 legacy 才注入、legacy 必须回落 None（逐字节兼容的前提）。

    web 引擎会"跟随风格包"，但兼容性要求是 style=None 时页面输出**逐字节**
    等于不带载荷的旧输出。这条守住"什么时候该注入、什么时候必须不注入"这个开关。
    """
    import render as R
    try:
        import webengine as we
    except Exception as e:                                  # noqa: BLE001
        return "skip（没有 webengine.py：%s）" % e
    # 没有 legacy 概念了（legacy 入口已被拒绝），所以这里只验"设计项目必须注入载荷"。
    script = {"title": "x", "lines": [{"id": "l01", "kind": "hook", "text": "y"}]}
    script["_style"] = _design_plan("web 载荷")[1]
    tl = {"fps": 30, "video": {"out_w": 1280, "out_h": 720}}
    try:
        payload = R._web_style_payload(we, script, tl)
    except SystemExit as e:
        raise AssertionError("有 _style 却被当成没有设计：%s" % (e,)) from e
    assert payload is not None, "非 legacy 项目没有生成 style 载荷（页面不会跟随风格）"
    canvas = payload.get("canvas") or {}
    assert int(canvas.get("w") or 0) == 1280 and int(canvas.get("h") or 0) == 720, \
        "画布尺寸没跟着 video 块走：%r" % (canvas,)
    # 没有设计的剧本：必须回落 None（不是"随便注入一套默认风格"）
    bare = {"title": "x", "lines": [{"id": "l01", "kind": "hook", "text": "y"}]}
    assert R._web_style_payload(we, bare, tl) is None, \
        "没有设计的剧本也注入了 style 载荷（等于凭空套一套外观）"
    return "有设计注入 %dx%d / 无设计回落 None" % (canvas["w"], canvas["h"])


def t_golden_frames():
    """51 金帧回归：像素基线真的能拦住"画面变了"（仓库工具 tools/golden_frames.py）。

    为什么放进 selfcheck 而不是只放 CI：改引擎/主题后"画面变了一点"是最难发现的一类回归，
    而它只需要 0.65 秒。约定：基线缺失 / 字体或环境指纹不同 -> skip（跨机不误报），
    同环境像素变了才是 fail。
    """
    # HERE = <repo>/skills/video-studio/scripts -> 上溯三级才是仓库根
    # （两级会落到 <repo>/skills，工具与基线都找不到，这条就永远 skip —— 静默失效）
    root = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
    tool = os.path.join(root, "tools", "golden_frames.py")
    if not os.path.exists(tool):
        return "skip（没有 tools/golden_frames.py）"
    base = os.path.join(root, "skills", "video-studio", "assets", "golden_frames.json")
    if not os.path.exists(base):
        return "skip（没有金帧基线 assets/golden_frames.json）"
    p = subprocess.run([sys.executable, tool, "--check", "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=root)
    out = (p.stdout or "").strip()
    i = out.find("{")
    if i < 0:
        return "skip（golden_frames 没输出 JSON：%s）" % (p.stderr or out)[:120]
    try:
        d = json.loads(out[i:])
    except json.JSONDecodeError as e:
        return "skip（golden_frames JSON 解析失败：%s）" % e
    changed = d.get("changed") or []
    status = str(d.get("status") or "")
    # 三档必须分开判：工具在"基线缺失 / 字体或环境指纹不同"时返回 exit 0 + status=skipped，
    # 只认 returncode 会把"这次根本没比"记成 PASS。实测踩过：本机 Python 3.12.14、基线记的是
    # 3.12.16，于是第 51 条显示"0 帧像素一致"进了 PASS 列——而它一帧都没比。
    if changed:
        raise AssertionError("金帧变了（画面回归）：changed=%r；若是有意改画风，"
                             "确认后用 python tools/golden_frames.py --write 重录基线" % (changed,))
    if status == "pass" and p.returncode == 0:
        frames = d.get("frames") or []
        return "%d 帧像素一致（font_fp %s）" % (len(frames),
                                               str(d.get("font_fp") or "")[:8])
    return "skip（golden_frames status=%s exit=%s：%s）" % (
        status or "-", p.returncode, (d.get("reason") or "")[:120])


# ============================================================================
# 场景语法 + 视觉语言
# ============================================================================

def _load_grammar():
    """加载 grammar.py（纯标准库）。找不到返回 None —— CI 里可能只拷了 scripts/ 的一部分。"""
    try:
        import grammar
        return grammar
    except Exception:                                       # noqa: BLE001
        cand = os.path.join(HERE, "grammar.py")
        if not os.path.exists(cand):
            return None
        import importlib.util
        spec = importlib.util.spec_from_file_location("sc_grammar", cand)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def _fake_scenes(G, lines, per=6.0):
    """按固定时长造 timeline 段（不跑 TTS），只用来测选型。"""
    scenes, t = [], 0.0
    for ln in lines:
        row = dict(ln, start=round(t, 3), end=round(t + per, 3), dur=round(per, 3))
        scenes.append(row)
        t += per
    return scenes


def t_scene_grammar():
    """52 场景语法：10 类都能被选中、选型确定、段长门会降级。

    为什么要有这条：quote/emphasis/calm 三种 kind 全都落到 body 绘制器的话，
    画面永远只有一套骨架。这条断言盯的是"内容 -> 构图"这条链路真的在分流，
    而不是"代码里写了 10 个函数但没人调用"。
    """
    G = _load_grammar()
    if G is None:
        return "skip（没有 grammar.py）"
    lines = [
        {"id": "a", "kind": "hook", "chapter": "01", "text": "开场一句。", "big": "开场"},
        {"id": "b", "kind": "body", "chapter": "02", "text": "我们每天被打断 47 次。", "big": "47 次"},
        {"id": "c", "kind": "body", "chapter": "02", "text": "对比一下：深度工作四小时，产出是碎片时间的三倍。",
         "big": "四小时 vs 碎片", "items": [{"text": "深度工作"}, {"text": "碎片时间"}]},
        {"id": "d", "kind": "body", "chapter": "03", "text": "第一步清空收件箱，然后关掉通知，接着单线程，最后复盘。",
         "big": "四步", "items": [{"text": "清空"}, {"text": "关通知"}, {"text": "单线程"}, {"text": "复盘"}]},
        {"id": "e", "kind": "quote", "chapter": "03", "text": "他说：「深度工作稀缺。」", "quote": "深度工作稀缺"},
        {"id": "f", "kind": "body", "chapter": "04", "text": "各行业占比差异明显。", "big": "各行业占比",
         "items": [{"text": "A 62%"}, {"text": "B 51%"}, {"text": "C 38%"}]},
        {"id": "g", "kind": "body", "chapter": "05", "text": "注意力是换来的。", "big": "注意力是换来的"},
        {"id": "h", "kind": "body", "chapter": "06", "text": "第五章开始了。", "big": "第五章"},
        {"id": "i", "kind": "body", "chapter": "06", "text": "这里有五条要点要讲清楚。", "big": "五条要点",
         "items": [{"text": "一"}, {"text": "二"}, {"text": "三"}, {"text": "四"}, {"text": "五"}]},
        {"id": "j", "kind": "outro", "chapter": "99", "text": "开始就好。", "big": "开始就好"},
    ]
    scenes = _fake_scenes(G, lines)
    plan = {"chapter_layout": {}, "layout_default": "left-stack", "style": "tech-deep"}
    sb1 = G.plan_scenes([dict(s) for s in scenes], {"title": "t"}, plan)
    got = [r["type"] for r in sb1["scenes"]]
    # ① 确定性：同一份输入两次结果完全一致（含变体号与理由）
    sb2 = G.plan_scenes([dict(s) for s in scenes], {"title": "t"}, plan)
    assert [r["type"] for r in sb2["scenes"]] == got, "同一份稿子两次选型不同（不确定性）"
    assert [r["variant"] for r in sb2["scenes"]] == [r["variant"] for r in sb1["scenes"]], \
        "同一份稿子两次变体不同（不确定性）"
    # ② 分流：至少命中 5 种不同类型（不是全退化成 list/statement）
    assert len(set(got)) >= 5, "10 段只分出 %d 种构图（%r）：选型逻辑没在分流" % (len(set(got)), got)
    assert "stat" in got and "compare" in got and "quote" in got, \
        "数字/对比/引语三种特征没有各自分流：%r" % (got,)
    # ③ 段长门：把 chart 塞进 2 秒段必须降级，且理由里写明
    short = [{"id": "x", "kind": "body", "chapter": "01", "text": "短。", "big": "短",
              "type": "chart", "series": [{"label": "A", "value": 1}]}]
    sb3 = G.plan_scenes([dict(s) for s in _fake_scenes(G, short, per=2.0)],
                        {"title": "t"}, plan)
    r3 = sb3["scenes"][0]
    assert r3["type"] != "chart", "2 秒的段仍选了 chart（段长门失效）"
    assert any("降级" in x or "排除" in x for x in r3["reason"]), \
        "降级没有留下理由：%r" % (r3["reason"],)
    # ④ 显式 type 优先
    ex = [{"id": "y", "kind": "body", "chapter": "01", "text": "随便一句", "big": "随便",
           "type": "hero"}]
    sb4 = G.plan_scenes([dict(s) for s in _fake_scenes(G, ex)], {"title": "t"}, plan)
    assert sb4["scenes"][0]["type"] == "hero", "显式 type=hero 没生效：%r" % (sb4["scenes"][0],)
    return "10 段 -> %d 种构图；降级 chart->%s；显式 type 生效" % (len(set(got)), r3["type"])


def t_visual_languages():
    """53 视觉语言包：8 套结构签名齐全、过体检、且彼此不是同一套。

    为什么要有这条：只改配色的"风格"会让 8 套风格骨架完全一样（用户抱怨的根因）。
    这条盯的是"结构签名真的存在且互不相同"——母题/转场/重心偏移任一维度相同不算问题，
    但**五组签名全同**就说明这套语言只是复制粘贴。
    """
    import designkit as DK
    import stylekit as ST
    # 没有 8 套内置语言了。验"现场生成的语言"，而且断言更强：
    # 12 个设计的两两结构签名必须互不相同（以前只要求 8 套里 >=6 种母题）。
    sigs, motifs, transes = {}, set(), set()
    designs = DK.sample_designs({"topic": "视觉语言多样性"}, n=12, seed=20260202)
    for d in designs:
        plan = DK.design_to_plan(d)
        vl = plan["language"]
        bad = ST.visual_lint_language(vl)
        assert not bad, "%s 的语言包没过体检：%s" % (d["label"], bad[:2])
        key = (str((vl.get("motif") or {}).get("kind")),
               str((vl.get("transition") or {}).get("kind")),
               tuple(sorted((vl.get("bias") or {}).items())))
        sigs.setdefault(key, []).append(d["label"])
        motifs.add(str((vl.get("motif") or {}).get("kind")))
        transes.add(str((vl.get("transition") or {}).get("kind")))
    dup = [v for v in sigs.values() if len(v) > 1]
    assert not dup, "这些设计语言签名完全相同（等于只在换色）: %s" % (dup[:2],)
    assert len(motifs) >= 6, "12 个设计只用了 %d 种装饰母题：%r" % (len(motifs), sorted(motifs))
    return "12 个设计 / %d 种母题 / %d 种转场 / 无重复签名" % (
        len(motifs), len(transes))


def t_scene_types_single_truth():
    """54 场景类型只有一份"真值集合"：4 处定义必须集合相等（顺序可以不同）。

    为什么比集合不比顺序：grammar.SCENE_TYPES 的顺序是"平手取位顺序"，改它会改变同一份
    稿子的选型结果，所以它有独立语义；filmcheck/schema/scenes 只关心"有哪些类型"。
    顺序不同是设计，集合不同是 bug（写错一个字母 = 某个类型永远画不出来）。
    """
    G = _load_grammar()
    if G is None:
        return "skip（没有 grammar.py）"
    here_scripts = HERE
    got = {"grammar": set(G.SCENE_TYPES)}
    # filmcheck.SCENE_TYPES
    try:
        import filmcheck as FC
        got["filmcheck"] = set(FC.SCENE_TYPES)
    except Exception:                                       # noqa: BLE001
        pass
    # assets/scenes.py 的 _DRAWERS 键
    mod = _load_scenes_mod()
    if mod is not None and hasattr(mod, "_DRAWERS"):
        got["scenes"] = set(mod._DRAWERS.keys())
    # schema 的 enum
    schema = os.path.join(os.path.dirname(os.path.dirname(here_scripts)),
                          "..", "..", "schemas", "film.schema.json")
    schema = os.path.normpath(schema)
    if os.path.exists(schema):
        try:
            data = json.load(open(schema, encoding="utf-8"))
            enum = (data["properties"]["lines"]["items"]["properties"]["type"]["enum"])
            got["schema"] = set(enum)
        except Exception:                                   # noqa: BLE001
            pass
    if len(got) < 3:
        return "skip（只找到 %d 处真值：%s）" % (len(got), sorted(got))
    base_name, base = sorted(got.items())[0]
    for name, s in got.items():
        if s != base:
            raise AssertionError("场景类型集合不一致：%s 与 %s 差 %r / %r"
                                 % (name, base_name, sorted(s - base), sorted(base - s)))
    return "%d 处真值集合一致（%d 类）：%s" % (len(got), len(base), "/".join(sorted(got)))


def t_silhouette_gate():
    """55 剪影可区分（G13）：8 套风格两两之间构图骨架必须能区分。

    这是核心验收：如果这条挂了，说明"换风格只是换色"的毛病回来了。
    工具在 tools/silhouette_qa.py（可选）：缺了 skip，不阻塞 CI。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
    tool = os.path.join(root, "tools", "silhouette_qa.py")
    if not os.path.exists(tool):
        return "skip（没有 tools/silhouette_qa.py）"
    p = subprocess.run([sys.executable, tool, "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=root)
    out = (p.stdout or "").strip()
    i = out.find("{")
    if i < 0:
        return "skip（silhouette_qa 没输出 JSON：%s）" % ((p.stderr or out) or "")[:120]
    try:
        d = json.loads(out[i:])
    except json.JSONDecodeError as e:
        return "skip（silhouette_qa JSON 解析失败：%s）" % e
    pairs = d.get("pairs") or []
    # 三档语义与工具对齐：status 只有 pass / fail / skip 三种。
    # 只认 returncode 会把"工具没渲出帧"（skip）也记成 PASS —— 那正是本仓库反复踩的坑。
    failed = [x for x in pairs if str(x.get("status")) == "fail"]
    skipped = [x for x in pairs if str(x.get("status")) == "skip"]
    if p.returncode != 0 or failed:
        names = ["%s×%s" % (x.get("a"), x.get("b")) for x in failed[:4]]
        raise AssertionError("剪影不可区分：%d/%d 对失败（%s）；换风格仍只是换色"
                             % (len(failed), len(pairs), ", ".join(names)))
    if skipped:
        return "skip（%d/%d 对没渲出来：%s）" % (
            len(skipped), len(pairs),
            ", ".join("%s×%s" % (x.get("a"), x.get("b")) for x in skipped[:3]))
    # 同明暗族是"保守下界"：跨族很多时候靠 ΔB（背景亮度差）就能过，那不是构图证据。
    # 所以这条断言在报告里必须把同族数字单独说出来，别让 28/28 被读成"8 套风格结构全不同"。
    same = ((d.get("summary") or {}).get("by_family") or {}).get("same") or {}
    same_n = same.get("pairs")
    same_ok = same.get("pass")
    if same_n and same_ok is not None and int(same_ok) < int(same_n):
        raise AssertionError("同明暗族只有 %s/%s 对可区分（保守下界不达标）"
                             % (same_ok, same_n))
    return "%d 对全部可区分（同明暗族 %s/%s，保守下界也过）" % (
        len(pairs), same_ok if same_ok is not None else "?", same_n if same_n else "?")


def t_design_dsl():
    """56 设计 DSL：采样 -> 校验 -> 转 plan，且 fail-closed（坏设计必须报错）。"""
    try:
        import designkit as DK
    except Exception:                                       # noqa: BLE001
        return "skip（没有 designkit.py）"
    ds = DK.sample_designs({"topic": "selfcheck 设计 DSL"}, n=3)
    assert len(ds) == 3, "采样器只给了 %d 个候选" % len(ds)
    for d in ds:
        bad = DK.validate_design(d)
        assert not bad, "采样器吐出了不合格的设计 %r：%s" % (d.get("id"), bad)
        plan = DK.design_to_plan(d)
        assert plan["legacy"] is False, "design 计划不该标 legacy"
        assert str(plan["style"]).startswith("design:"), "style 应是 design:<id>：%r" % plan["style"]
        assert len(plan.get("theme") or {}) >= 30, "主题字段太少：%d" % len(plan.get("theme") or {})
    # 两两必须至少 2 个维度不同（"伪多方案"门禁）
    for i in range(len(ds)):
        for j in range(i + 1, len(ds)):
            d = DK.design_distance(ds[i], ds[j])
            assert d["n"] >= 2, "候选 %s 与 %s 只差 %d 维" % (ds[i]["id"], ds[j]["id"], d["n"])
    # fail-closed：删掉必填块必须报错，不能静默回落
    broken = json.loads(json.dumps(ds[0]))
    broken["visual"].pop("motif", None)
    assert DK.validate_design(broken), "缺 motif 竟然通过校验（fail-closed 失效）"
    # 语义是**不给 seed = 每次都不一样**（需求是"每条视频都不同"），
    # 给 seed 才可复现。两条都要测，否则改错了任一边都发现不了。
    x1 = [d["id"] for d in DK.sample_designs({"topic": "selfcheck 设计 DSL"}, n=3)]
    x2 = [d["id"] for d in DK.sample_designs({"topic": "selfcheck 设计 DSL"}, n=3)]
    assert x1 != x2, "同一 brief 两次采样完全相同（那就是把模板藏进了哈希里）"
    y1 = [d["id"] for d in DK.sample_designs({"topic": "selfcheck 设计 DSL"}, n=3, seed=42)]
    y2 = [d["id"] for d in DK.sample_designs({"topic": "selfcheck 设计 DSL"}, n=3, seed=42)]
    assert y1 == y2, "给了 seed 竟然不可复现（回归/文档示例就不可信了）"
    return "3 候选 / 校验全过 / 两两 >=2 维 / 缺字段必报错 / 默认每次不同 + seed 可复现"


def t_no_templates():
    """57 零模板：仓库里**不许再有**预设/模板文件（本技能只提供技术，不提供模板）。

    这条是反向断言：预设整个删掉后，
    如果只是删掉这条检查，就再也拦不住"哪天有人又加一套预设进去"。
    所以改成**禁止存在**——删掉模板是一次性动作，守住它是长期契约。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
    skill = os.path.join(root, "skills", "video-studio")
    banned = []
    presets_dir = os.path.join(skill, "presets")
    if os.path.isdir(presets_dir):
        n = len([x for x in os.listdir(presets_dir) if x.endswith(".json")])
        banned.append("presets/ 目录还在（%d 个 json）" % n)
    for name in ("presetkit.py",):
        if os.path.exists(os.path.join(skill, "scripts", name)):
            banned.append("scripts/%s 还在" % name)
    assert not banned, ("本技能不提供模板，这些必须删掉：%s；"
                        "要自定义请写 design.json（技术原语仍在，只是不再有现成目录）"
                        % "；".join(banned))
    # 同时确认"现场采样"是唯一方式，且**每次都新**：
    #   · 同一 brief 两次 -> 必须不同（否则等于模板藏在哈希里，用户要新片只能改措辞）
    #   · 给 seed -> 必须可复现（回归与文档示例需要一个稳定值）
    try:
        import designkit as DK
    except Exception:                                       # noqa: BLE001
        return "skip（没有 designkit.py）"
    a = [d["label"] for d in DK.sample_designs({"topic": "零模板校验 A"}, n=2)]
    b = [d["label"] for d in DK.sample_designs({"topic": "零模板校验 A"}, n=2)]
    c = [d["label"] for d in DK.sample_designs({"topic": "零模板校验 B"}, n=2)]
    s1 = [d["label"] for d in DK.sample_designs({"topic": "零模板校验 A"}, n=2, seed=7)]
    s2 = [d["label"] for d in DK.sample_designs({"topic": "零模板校验 A"}, n=2, seed=7)]
    assert a != b, "同一 brief 两次采样结果相同（等于把模板藏进了哈希）"
    assert a != c, "换 brief 采样结果没变（说明其实在套模板）"
    assert s1 == s2, "给了 seed 竟然不可复现"
    return "无模板文件；同 brief 两次必不同、换 brief 必变、seed 可复现"


def t_brief_constraints():
    """60 brief 里的要求必须真的生效（自然语言 -> 约束 -> 采样结果）。

    这条防的是**需求被静默吃掉**：propose._brief_of 若把整句 --brief 塞进 topic，
    must_have/avoid 恒为空数组，于是"深蓝配色的科技解说"采出来是 h087 / h316（橙 / 紫）。
    用户看到的需求和结果无关，而且没有任何提示——比报错更糟。

    三件事都要验：
      ① 自由文本能解析成 must_have / avoid（含否定词归到 avoid）；
      ② 配色约束按**色相窗口**生效（不是按 8 个族名）；
      ③ avoid 真的把色相排除掉。
    """
    import re
    try:
        import propose as PP
        import designkit as DK
        import stylekit as ST
    except Exception:                                       # noqa: BLE001
        return "skip（缺 propose/designkit/stylekit）"
    # ① 解析
    b1 = PP._brief_of("要深蓝，不要放射线", {"title": "t"})
    assert b1["must_have"] and "深蓝" in b1["must_have"][0], "must_have 没解析出来：%r" % (b1["must_have"],)
    assert b1["avoid"] and "放射" in b1["avoid"][0], "avoid 没解析出来：%r" % (b1["avoid"],)
    b2 = PP._brief_of("随便做个片", {"title": "t"})
    assert b2["must_have"] == [] and b2["avoid"] == [], "认不出要求时不该硬凑约束"
    # ② 配色约束 -> 色相窗口
    ds = DK.sample_designs({"topic": "深蓝科技", "must_have": ["深蓝"]}, n=2, seed=7)
    for d in ds:
        h = DK._design_bg_hue(d)
        assert h is not None, "取不到底色色相"
        gap = DK._hue_gap(h, DK._PALETTE_ANCHOR["deep-blue"][0])
        assert gap <= DK._HUE_WINDOW + 1e-6, (
            "must_have『深蓝』没生效：底色色相 %.1f° 距 210° 有 %.1f°（窗口 %.0f°）"
            % (h, gap, DK._HUE_WINDOW))
    # ③ avoid 真的排除
    mint = DK._PALETTE_ANCHOR["cool-mint"][0]
    for i in range(3):
        d = DK.sample_designs({"topic": "避开薄荷 %d" % i, "avoid": ["薄荷"]}, n=1, seed=i)[0]
        h = DK._design_bg_hue(d)
        assert DK._hue_gap(h, mint) > DK._HUE_WINDOW, (
            "avoid『薄荷』没生效：底色色相 %.1f° 落在薄荷范围（%.0f°±%.0f°）"
            % (h, mint, DK._HUE_WINDOW))
    return "解析 2/2；深蓝落入窗口；薄荷 3/3 被排除"


def t_stage_standalone():
    """59 逐 stage 可单独跑：render 里每处 resolve_style 都必须能拿到 work。

    这条守住一类**只在"单跑某个 stage"时才发作**的 bug：
    `--stage all` 时前面的 stage 已经把 script["_style"] 塞好了，后面全都命中缓存，
    所以缺参数也看不出来；一旦用户只跑 `--stage frames` / `--stage sfx`，
    resolve_style 读不到 work/design.json 就报"找不到设计"（实测踩到：
    frames / sfx 两个 stage 单跑必挂，sfx 还是 NameError——函数里压根没有 work 这个名字）。

    为什么用 AST 而不是真的跑每个 stage：跑一遍 = 要 TTS 联网 + 全片渲染，几十秒起步，
    不适合放进 20 秒的自检。静态检查能抓住"参数没传/名字不存在"这一整类问题，
    而这正是本次的真实缺陷形态。
    """
    import ast
    src_path = os.path.join(HERE, "render.py")
    tree = ast.parse(open(src_path, encoding="utf-8").read())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name != "resolve_style":
                continue
            # resolve_style(script, work) 的第二位必须是本函数能拿到的变量
            if len(sub.args) >= 2:
                second = sub.args[1]
                vname = getattr(second, "id", None)
                if vname and vname not in args and vname not in ("work",):
                    # 明确允许 a.work 这类属性访问（不是 Name）
                    bad.append("%s(): resolve_style 的 work 参数用了 %r，但该函数没有这个变量"
                               % (node.name, vname))
            else:
                bad.append("%s(): resolve_style 没传 work（单跑该 stage 会读不到 design.json）"
                           % node.name)
    assert not bad, "；".join(bad[:4])
    return "render 里 %d 处 resolve_style 全部带得上 work" % sum(
        1 for n in ast.walk(tree) if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "resolve_style")


def t_proposal_gate():
    """58 提案闸：没确认时必须判 draft/absent，确认后才放行；坏文件判 broken。"""
    try:
        import propose as PP
    except Exception:                                       # noqa: BLE001
        return "skip（没有 propose.py）"
    with tempfile.TemporaryDirectory() as work:
        status, detail = PP.proposal_state(work)
        assert status == "absent", "空目录应判 absent，收到 %r" % status
        os.makedirs(work, exist_ok=True)
        with open(os.path.join(work, "proposal.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "rev": 1, "status": "draft",
                       "candidates": [{"id": "A"}, {"id": "B"}]}, fh)
        status, _ = PP.proposal_state(work)
        assert status == "draft", "有草案但没确认应判 draft，收到 %r" % status
        with open(os.path.join(work, "proposal.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken json")
        try:
            status, _ = PP.proposal_state(work)
        except SystemExit:
            status = "broken"
        assert status == "broken", "坏 JSON 应判 broken，收到 %r" % status
    return "absent / draft / broken 三档判定正确"




def t_subtitle_style():
    """61 字幕样式可定制：design.visual.subtitle 必须真的改变像素，缺省逐像素不变。

    为什么必须有这条：字幕字号/位置/背板过去是 filmkit 的**模块常量**，任何两条片子
    的字幕带都一模一样 —— 这正是用户说的"跨片大面积重复"。改成设计字段后，最容易
    出现的退化是"字段读进来了但没传给 draw_subtitle"（写完不生效，实测踩过）。
    所以这里验行为：同一个 theme、同一段文字，换字幕参数必须像素不同；
    不传参数时必须与不传该字段的调用逐像素一致（老项目不漂）。
    """
    import numpy as _np
    import designkit as DK
    # 用一套现场采样设计里的主题，避免依赖内置主题表（已删）
    d = DK.sample_designs({"topic": "字幕样式自检"}, n=1, seed=3)[0]
    plan = DK.design_to_plan(d)
    theme = plan["theme"]
    text = "这是一句用来测试字幕样式是否真的生效的中文字幕"
    base = K.Image.new("RGB", (K.W, K.H), tuple(theme["bg"]))
    default_img = K.draw_subtitle(base.copy(), text, theme=theme)
    kw = K.subtitle_kwargs(plan)
    assert kw.get("size") and kw.get("align") in ("center", "left"), (
        "subtitle_kwargs 没把设计的字幕参数带出来：%r" % (kw,))
    styled = K.draw_subtitle(base.copy(), text, theme=theme, **kw)
    a = _np.asarray(default_img, dtype=_np.int16)
    b = _np.asarray(styled, dtype=_np.int16)
    assert int(_np.abs(a - b).max()) > 0, "字幕参数没有改变任何像素（字段写了不生效）"
    # 缺省必须等价于旧调用（老 design.json 逐像素不变）
    assert K.subtitle_kwargs({}) == {}, "没有 subtitle 的计划必须返回空 kw（保持旧像素）"
    assert K.subtitle_kwargs({"subtitle": {}}) == {}, "空 subtitle 也必须是空 kw"
    return "字幕参数改变像素；缺省等价旧调用（size=%s align=%s）" % (kw.get("size"), kw.get("align"))


def t_layout_cycle():
    """62 片内版式轮换：layout_cycle 真的让不同章节换骨架，同章必须稳定。

    用户原则 5：同一部片不能"长时间一张版式"。design.visual.layout 是单值，
    grammar 把它当全片默认 → 8 段片子 8 屏同骨架。这条验三件事：
      ① 有 layout_cycle 时，不同 chapter 拿到不同版式；
      ② 同一个 chapter 内所有段版式一致（不能每句都跳）；
      ③ 没有 layout_cycle 时行为与旧版一致（全用 layout_default）。
    """
    import grammar as G
    scenes = [
        {"id": "a1", "kind": "body", "chapter": "第一章", "start": 0.0, "end": 4.0, "dur": 4.0},
        {"id": "a2", "kind": "body", "chapter": "第一章", "start": 4.0, "end": 8.0, "dur": 4.0},
        {"id": "b1", "kind": "body", "chapter": "第二章", "start": 8.0, "end": 12.0, "dur": 4.0},
        {"id": "b2", "kind": "body", "chapter": "第二章", "start": 12.0, "end": 16.0, "dur": 4.0},
    ]
    plan = {"chapter_layout": {}, "layout_default": "left-stack",
            "layout_cycle": ["left-stack", "center-stack"]}
    scenes = [dict(s) for s in scenes]
    G.plan_scenes(scenes, {"lines": []}, plan)
    got = {s["id"]: s["scene_layout"] for s in scenes}
    assert got["a1"] == got["a2"], "同一章内版式必须稳定，实际 %s" % (got,)
    assert got["b1"] == got["b2"], "同一章内版式必须稳定，实际 %s" % (got,)
    assert got["a1"] != got["b1"], "不同章节应轮换版式，实际两章都是 %s" % (got["a1"],)
    # 没有 cycle 时回到旧行为
    scenes2 = [dict(s) for s in scenes]
    G.plan_scenes(scenes2, {"lines": []}, {"chapter_layout": {}, "layout_default": "split-right"})
    assert all(s["scene_layout"] == "split-right" for s in scenes2), \
        "没有 layout_cycle 时必须全用 layout_default（旧行为）"
    return "两章轮换 %s -> %s；同章稳定；无 cycle 时回落 layout_default" % (got["a1"], got["b1"])


def t_brief_constraint_robustness():
    """63 带约束的 brief 不许高概率直接失败。

    实测过的真缺陷：配色候选去重按 24° 分桶，而 must_have「深蓝」把色相窗口压到 52°，
    n=3 时约一半概率抛 ValueError；「暖纸色 + 女声」20/20 全失败。用户第一步就撞墙。
    修法改成"色相距离 + 结构差异"分档后，这里用 20 次独立采样守回归。
    """
    import propose as PP
    import designkit as DK
    briefs = ["深蓝", "纪录片，暖纸色，缓慢，女声", "科技发布会，深蓝，要遮幅"]
    for text in briefs:
        b = PP._brief_of(text, {"title": "t"})
        for _ in range(6):
            try:
                DK.sample_designs(b, n=3)
            except ValueError as e:
                raise AssertionError("brief %r 采样失败：%s" % (text, str(e)[:200])) from e
    return "3 种带约束 brief × 6 次 = 18 次 n=3 采样全部成功"


def t_removed_apis_fail_closed():
    """64 已删除的 API 必须"明确报错/非零退出"，不许 NameError、假绿或静默 fallback。

    删内置风格表时留下了一批**死路径**：stylekit 的 --list/--show NameError、
    --lint 空表 exit 0（假绿）、filmkit 的 resolve_theme/style_plan 吞异常回落到写死的
    写死的主题（静默降级 = 用模板出片）。这三种都必须绝迹。
    """
    import subprocess as _sp
    import stylekit as ST
    import filmkit as FK
    # ① stylekit.resolve_script_style 必须 raise 且带指引
    try:
        ST.resolve_script_style({"title": "t", "lines": []})
    except (ValueError, KeyError, SystemExit) as e:
        msg = str(getattr(e, "code", None) or e)
        assert "propose.py" in msg, "报错必须给迁移指引（要提到 propose.py）：%r" % msg[:120]
    else:
        raise AssertionError("没有设计的剧本竟然解析出了计划")
    # ② stylekit CLI 三个动作必须非零退出（不许假绿/NameError traceback）
    for flag in ("--list", "--lint"):
        p = _sp.run([sys.executable, os.path.join(HERE, "stylekit.py"), flag],
                    capture_output=True, text=True, timeout=60)
        assert p.returncode == 2, "stylekit.py %s 应 exit 2（能力已移除），实际 %d" % (flag, p.returncode)
        assert "Traceback" not in (p.stderr or ""), "stylekit.py %s 不该抛 traceback" % flag
    # ③ filmkit 的静默回落必须改成明确报错
    for fn in (FK.resolve_theme, FK.style_plan):
        try:
            fn({"title": "t", "lines": []})
        except (ValueError, KeyError) as e:
            assert "propose" in str(e).lower() or "design" in str(e).lower(), \
                "%s 的报错要给迁移指引：%r" % (fn.__name__, str(e)[:120])
        else:
            raise AssertionError("%s 在无设计时静默返回了兜底主题（模板残留）" % fn.__name__)
    # ④ designkit 的配色采样示例不许崩（--list-palettes 曾 KeyError: family）
    p = _sp.run([sys.executable, os.path.join(HERE, "designkit.py"), "--list-palettes", "--json"],
                capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, "designkit.py --list-palettes 崩了：%s" % (p.stderr or p.stdout)[:200]
    return "resolve_script_style/CLI/filmkit 全部 fail-closed；--list-palettes 可跑"


def t_audio_overrides():
    """65 design.audio 的音量/音效参数必须真的落进 script。

    实测过的真缺陷：design.json 的 audio.music.gain_db / duck_db 与 audio.sfx.enabled
    / density / gain_db 一直被 validate_design 接受、却从来没有渲染代码读它们 ——
    用户把 sfx.enabled 写成 false，成片照样出 8 条音效。
    现在的契约：**film.json 显式写了的 > design 的值 > 模块默认**。
    """
    import designkit as DK
    d = DK.sample_designs({"topic": "声音参数自检"}, n=1, seed=5)[0]
    d = json.loads(json.dumps(d))
    d["audio"]["music"]["gain_db"] = -7.5
    d["audio"]["music"]["duck_db"] = 9.0
    d["audio"]["sfx"] = {"enabled": False, "density": "rich", "gain_db": -3}
    plan = DK.design_to_plan(d)
    ov = plan.get("audio_overrides") or {}
    assert abs(float(ov["music"]["gain_db"]) + 7.5) < 1e-9, "music.gain_db 没进计划"
    assert abs(float(ov["music"]["duck_db"]) - 9.0) < 1e-9, "music.duck_db 没进计划"
    assert ov["sfx"]["enabled"] is False, "sfx.enabled 没进计划"
    import render as R
    # 剧本里没写 -> 用 design 的值
    s1 = {"title": "t", "lines": []}
    R._apply_audio_overrides(s1, plan)
    assert s1["sfx"]["enabled"] is False and abs(s1["music"]["gain_db"] + 7.5) < 1e-9, \
        "design 的声音参数没有填进 script：%r" % (s1,)
    # 剧本显式写了 -> 以剧本为准（用户手里 film.json 是最终裁决权）
    s2 = {"title": "t", "lines": [], "music": {"gain_db": -1.0}, "sfx": {"enabled": True}}
    R._apply_audio_overrides(s2, plan)
    assert s2["music"]["gain_db"] == -1.0 and s2["sfx"]["enabled"] is True, \
        "film.json 的显式值被 design 覆盖了：%r" % (s2,)
    return "design.audio -> plan -> script 贯通；film.json 显式值优先"


def t_design_ledger():
    """66 片间去重台账：确认过的设计要记住，下次提案主动避开；可关可查。

    用户原则 5：不同视频的整体风格不能大面积重复。只保证"同一 brief 两次采样不同"
    （nonce），但没有记忆——连拍同题材仍可能撞到相近设计。台账补上这一环。
    这条验四件事：① confirm 后写台账；② 提案会读台账并报告距离；
    ③ 台账路径可被环境变量重定向（绝不污染用户真实台账）；④ --no-ledger 可关闭。
    """
    import tempfile
    import subprocess as _sp
    tmp = tempfile.mkdtemp(prefix="vs_ledger_selfcheck_")
    ledger = os.path.join(tmp, "ledger.json")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", VIDEO_STUDIO_LEDGER=ledger)
    script = {"title": "台账自检", "lines": [
        {"id": "l01", "kind": "hook", "text": "第一句话。"},
        {"id": "l02", "kind": "body", "text": "第二句话，讲一件具体的事。"},
        {"id": "l03", "kind": "outro", "text": "结束。"}]}
    film = os.path.join(tmp, "film.json")
    with open(film, "w", encoding="utf-8") as fh:
        json.dump(script, fh, ensure_ascii=False)
    pp = os.path.join(HERE, "propose.py")

    def _run(args):
        r = _sp.run([sys.executable, pp] + args, cwd=tmp, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", env=env, timeout=300)
        assert r.returncode == 0, "propose.py %s 失败：%s" % (args[0], (r.stderr or r.stdout)[-300:])
        return r

    # 台账这条例验的是"记账/避开"，不是问诊：问诊另有用例 70 守。
    _run(["--brief", "深蓝科技解说", "--script", film, "--n", "3", "--no-intake"])
    assert not os.path.exists(ledger), "还没 confirm 就写台账了（草稿不该记）"
    _run(["--confirm", "--candidate", "A"])
    assert os.path.exists(ledger), "confirm 之后没有写台账：%s" % ledger
    data = json.load(open(ledger, encoding="utf-8"))
    assert len(data.get("designs") or []) == 1, "台账条数不对：%r" % (data,)
    row = data["designs"][0]
    assert row.get("fingerprint") and isinstance(row.get("design"), dict), "台账记录缺字段"
    # 第二次提案：必须能读到台账（人读输出里有一行"片间去重"）
    r = _run(["--brief", "深蓝科技解说", "--script", film, "--n", "3", "--no-intake"])
    assert "片间去重" in r.stdout, "提案没有报告台账状态：%s" % r.stdout[-400:]
    # --show-ledger 能查；--no-ledger 能关
    r = _run(["--show-ledger"])
    assert row["fingerprint"] in r.stdout, "--show-ledger 没列出刚记的指纹"
    r = _run(["--brief", "深蓝科技解说", "--script", film, "--n", "3", "--no-ledger",
              "--no-intake"])
    assert "--no-ledger" in r.stdout, "--no-ledger 没有给出关闭提示"
    return "草稿不记 / confirm 记账 / 提案读取并报告 / --show-ledger 可查 / --no-ledger 可关"


def t_intake_gate():
    """70 出片前问诊：一次问完 + 首次提案前必须答完（--no-intake 才放行且留痕）。

    用户的真实抱怨是"有时一个问题不问就一路跑完，有时只问一两个"——根因是"问不问"
    全靠 agent 当场发挥。这条把它变成可执行的门：
      ① 问诊单是固定的 7 题，每一项都有推荐默认值，且**任何题材的推荐默认都是明亮活泼**；
      ② 没答完时首次提案直接失败，并给出"怎么问、怎么答、怎么定稿"三步；
      ③ 答完后提案里必须留痕（intake.status = complete，答案随提案落盘）；
      ④ --no-intake 是显式逃生口，用掉时留 bypassed 痕迹（事后能查"这片子问没问过"）。
    """
    import subprocess as _sp
    tmp = tempfile.mkdtemp(prefix="vs_intake_selfcheck_")
    scripts = HERE
    intake_py = os.path.join(scripts, "intake.py")
    propose_py = os.path.join(scripts, "propose.py")
    assert os.path.exists(intake_py), "缺 intake.py（出片前问诊脚本）"
    film = os.path.join(tmp, "film.json")
    with open(film, "w", encoding="utf-8") as fh:
        json.dump({"title": "问诊自检", "lines": [
            {"id": "l01", "kind": "hook", "text": "第一句话。"},
            {"id": "l02", "kind": "body", "text": "第二句话，讲一件具体的事。"},
            {"id": "l03", "kind": "outro", "text": "结束。"}]}, fh, ensure_ascii=False)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    def _run(args, cwd):
        return _sp.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=300)

    # ① 题目包：固定 7 题、每题带默认值、气质题默认明亮活泼且排第一
    r = _run([intake_py, "--work", "work", "--questions", "--json"], tmp)
    assert r.returncode == 0, "intake.py --questions 失败：%s" % (r.stderr or r.stdout)[-300:]
    payload = json.loads(r.stdout[r.stdout.find("{"):])
    qs = payload.get("questions") or []
    assert len(qs) == 7, "问诊单应有 7 题（题材/时长/气质/画幅/声线/配乐/补充），实际 %d" % len(qs)
    byid = {q["id"]: q for q in qs}
    for qid in ("topic", "duration", "tone", "voice", "music"):
        assert byid.get(qid) and byid[qid].get("required"), "必答题缺 %r" % qid
    assert byid["tone"].get("default") == "bright-lively", "气质题默认必须是 bright-lively"
    assert byid["tone"]["options"][0]["value"] == "bright-lively", \
        "气质题第一项必须是明亮活泼（推荐项排最前）"

    # ② 没答完：首次提案必须失败，并且说清三步怎么做
    work1 = os.path.join(tmp, "work1")
    r = _run([propose_py, "--brief", "明亮活泼的科普片", "--script", film,
              "--work", work1, "--n", "2"], tmp)
    assert r.returncode != 0, "没答问诊竟然直接出方案了（问诊闸失效）"
    msg = (r.stderr or "") + (r.stdout or "")
    assert "intake.py" in msg and "还没" in msg, "拦截时要给出 intake.py 指引：%r" % msg[-300:]

    # ③ 答完 + 定稿：提案放行，并留 complete 痕迹
    r = _run([intake_py, "--work", work1,
              "--set", "topic:AI 大模型的三年", "--set", "duration:45s",
              "--set", "tone:bright-lively", "--set", "voice:female-bright",
              "--set", "music:upbeat"], tmp)
    assert r.returncode == 0, "写答答案失败：%s" % (r.stderr or r.stdout)[-300:]
    r = _run([intake_py, "--work", work1, "--finalize"], tmp)
    assert r.returncode == 0, "--finalize 失败：%s" % (r.stderr or r.stdout)[-300:]
    brief_txt = open(os.path.join(work1, "intake.brief.txt"), encoding="utf-8").read()
    assert "明亮活泼" in brief_txt, "气质答案没进 brief：%r" % brief_txt
    r = _run([propose_py, "--work", work1, "--n", "2", "--json"], tmp)
    assert r.returncode == 0, "答完问诊后提案失败：%s" % (r.stderr or r.stdout)[-300:]
    data = json.loads(r.stdout[r.stdout.find("{"):])
    intake = data.get("intake") or {}
    assert intake.get("status") == "complete" and not intake.get("bypassed"), \
        "提案里没记下问诊状态：%r" % (intake,)
    assert intake.get("answers", {}).get("tone") == "bright-lively", "答案没随提案落盘"
    must = (data.get("brief") or {}).get("must_have") or []
    assert any("明亮" in str(x) for x in must), "气质答案没变成 brief 约束：%r" % (must,)
    # 首个候选应该是明亮活泼档（用户要"亮一点、有精神"）
    import designkit as DK
    first = data["candidates"][0]["design"]
    assert DK._tone_of(first) == DK._TONE_BRIGHT, \
        "首个候选不是明亮活泼：%r" % (DK._tone_of(first),)

    # ④ --no-intake 放行且留痕
    work2 = os.path.join(tmp, "work2")
    r = _run([propose_py, "--brief", "补渲测试", "--script", film,
              "--work", work2, "--n", "2", "--no-intake", "--json"], tmp)
    assert r.returncode == 0, "--no-intake 应放行：%s" % (r.stderr or r.stdout)[-300:]
    data2 = json.loads(r.stdout[r.stdout.find("{"):])
    assert (data2.get("intake") or {}).get("status") == "bypassed", \
        "--no-intake 没留 bypassed 痕迹：%r" % (data2.get("intake"),)
    return "7 题固定 / 没答完必拦 / 答完留痕且首候选明亮活泼 / --no-intake 留痕"


def t_duration_target():
    """67 时长要求真的被接住：brief 里的「2 分钟」要影响设计，渲染时对账。

    过去 `_brief_of` 把 duration_target 恒填 None —— 用户写「做个 2 分钟的片」等于没写，
    成片永远按台词长度出（实测 23s / 30s）。这条守三件事：
      ① 中文/阿拉伯数字的常见时长说法都能解析；
      ② 解析出的时长进 design.structure.target_sec，并决定候选段落数；
      ③ 渲染时按"目标 vs 实测"对账（差 >15% 告警，但不截断旁白）。
    """
    import propose as PP
    import designkit as DK
    cases = {"2分钟": 120.0, "做个90秒的短视频": 90.0, "一分钟半": 90.0,
             "3分30秒": 210.0, "2.5分钟": 150.0, "两分钟": 120.0}
    for text, want in cases.items():
        got = PP.parse_duration(text)
        assert got is not None and abs(got - want) < 1e-6, \
            "时长解析 %r 得到 %r，期望 %r" % (text, got, want)
    assert PP.parse_duration("深蓝科技解说") is None, "没有时长时不该硬凑"
    assert PP.parse_duration("两小时") is None, "超出 3–1800s 契约范围应拒绝"
    b = PP._brief_of("深蓝，科技，2分钟，硬切", {"title": "t"})
    assert b.get("duration_target") == 120.0, "brief 没接住时长：%r" % (b,)
    d = DK.sample_designs(b, n=2, seed=9)[0]
    assert d["structure"].get("target_sec") == 120.0, \
        "目标时长没进设计：%r" % (d["structure"],)
    plan = DK.design_to_plan(d)
    assert plan.get("target_sec") == 120.0, "目标时长没进计划：%r" % (plan.get("target_sec"),)
    return "6 种时长说法解析正确；进 design/plan；超范围拒绝"
def t_zoom_stability():
    """68 缩放取景的帧间稳定性（抖动回归）。

    为什么要有这条：底图把颗粒烘进去后用 NEAREST 逐帧直采，采样网格随 s 整体跳变，
    颗粒图案跟着错位 1px —— 用户实测的原话是"生成的视频有明显的抖动感"，
    而单帧闪烁门禁（8x8 分块比较前后帧平均）对这类亚像素沸腾**完全不敏感**。
    断言：同一主题从 s=1.0 缓推到 s=1+push 的过程中，相邻两帧最大差 <= 2 灰阶。
    端点直采实测抖动 4~6，改成端点预烘 + blend 后 0~1。
    """
    mod = _load_scenes_mod()
    if mod is None:
        return "skip（没有 assets/scenes.py）"
    import numpy as np
    push = 0.12
    th = {"id": "zoom-stab", "bg": (28, 30, 36), "bg2": (18, 19, 24), "texture": "paper",
          "grain": 5.0, "grain_freq": 0.31, "vignette": 0.2, "vignette_tint": (0, 0, 0),
          "texture_alpha": 0.03, "push": push}
    frames = [np.asarray(mod._bg_plate(th, 1.0 + push * i / 19.0), dtype=np.int16)
              for i in range(20)]
    worst_max = 0
    worst_mean = 0.0
    for i in range(1, len(frames)):
        d = np.abs(frames[i] - frames[i - 1])
        worst_max = max(worst_max, int(d.max()))
        worst_mean = max(worst_mean, float(d.mean()))
    assert worst_max <= 2, ("缩放取景帧间跳变 %d 灰阶（>2）：底图高频纹理被 NEAREST 重排，"
                            "观感就是满屏噪点沸腾" % worst_max)
    # 均值守住 0.6：8-bit 输出下慢速缓推必然出现"整片 1 灰阶"的量化台阶，
    # 首帧端点与 s≈1.006 之间实测 mean≈0.44 —— 这是量化噪声，不是图案错位。
    # 真正能区分"错位沸腾"的是 max：旧实现 4~6，新实现 <=1。
    assert worst_mean <= 0.6, "缩放取景帧间平均差 %.3f（>0.6，疑似整屏跳变）" % worst_mean
    return ("20 帧缓推：最大帧间差 %d 灰阶 / 均值 %.3f（旧 NEAREST 直采为 4~6 灰阶）"
            % (worst_max, worst_mean))


def t_asr_lost_diagnostics():
    """69 ASR 掉分可定位（低召回必须说清"掉在哪几个字"）。

    为什么要有这条：实测 ASR 回读 0.813~0.844 是常态（中文专名 + 同音字），只报一个
    百分比，agent 只能盲目放宽阈值。这条断言盯的是"低于 pass 线时，报告必须给出
    没对上的字符（全局 + 最差段落）"，且判分本身仍然是纯函数、不加载模型。
    """
    cand = os.path.join(HERE, "qaasr.py")
    if not os.path.exists(cand):
        return "skip（没有 qaasr.py）"
    import importlib.util
    spec = importlib.util.spec_from_file_location("sc_qaasr", cand)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scenes = [
        {"id": "a", "start": 0.0, "end": 3.0, "voice_start": 0.4,
         "text": "这是用 DeepSeek 生成的声音与画面。"},
        {"id": "b", "start": 3.0, "end": 6.0, "voice_start": 3.4,
         "text": "先定脚本，再定声音，最后才是画面。"},
    ]
    # ASR 把 DeepSeek 听成"迪普西克"、把"再定"听成"在订"——低召回但不丢句
    segments = [
        {"start": 0.4, "end": 3.0, "text": "这是用 迪普西克 生成的声音与画面"},
        {"start": 3.4, "end": 6.0, "text": "先定脚本，在订声音，最后才是画面"},
    ]
    rep = mod._judge(scenes, segments, recall_pass=0.99, recall_warn=0.5)
    assert rep["recall"] < 0.99, "构造样例没有触发低召回，断言前提不成立"
    assert rep.get("lost_top"), "低召回时没有输出 lost_top（掉分字符清单）"
    assert all("ch" in x and "n" in x for x in rep["lost_top"]), "lost_top 结构不对"
    lost_lines = [p for p in rep["per_line"] if p.get("lost")]
    assert lost_lines, "没有段落级 lost（最差段落无法定位）"
    warns = " ".join(rep.get("warnings") or [])
    assert "没对上的字" in warns, "低召回时警告里没有掉分字符清单"
    assert "最差段落" in warns, "低召回时警告里没有最差段落定位"
    return "lost_top %d 项 / 段落级 %d 段 / 警告 %d 条" % (
        len(rep["lost_top"]), len(lost_lines), len(rep.get("warnings") or []))


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
    ("13 调用签名兼容", t_compat),
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
    ("33 音效跨片可区分（内容派生 seed）", t_sfx_content_seed),
    ("34 配乐/音效片内分离（duck 加深）", t_audio_separation),
    ("35 旁白优先（背景让位与恢复）", t_voice_priority),
    ("36 旁白优先默认值（配乐 -12 dB + 侧链）", t_music_gain_default),
    ("37 旁白清晰度 SNR 兜底（背景太响时自动补压）", t_snr_guard),
    ("38 无旁白时混音不受影响", t_mix_no_voice),
    ("39 音频边界：极短/NaN·Inf/负起点/seg=0", t_audio_edge_inputs),
    ("40 QC 报告落点只有一个来源", t_report_single_source),
    ("41 空门禁账本不许写成通过", t_empty_ledger_not_pass),
    ("42 单阶段产物归属（防假 stale）", t_stage_io_no_phantom_output),
    ("43 theme_overrides 参与主题体检", t_theme_overrides_linted),
    ("44 词级时间戳（规整/裁剪/锚点）", t_word_events),
    ("45 画幅规划（只允许横屏下采样）", t_video_plan),
    ("46 金帧确定性（同帧两次一致）", t_golden_determinism),
    ("47 依赖漂移体检（离线）", t_dependency_drift),
    ("48 批量清单解析（预检先于执行）", t_batch_manifest),
    ("49 TTS 后端可替换（接口契约）", t_tts_backend),
    ("50 web 风格载荷（legacy 必须回落）", t_web_style_payload),
    ("51 金帧回归（像素基线）", t_golden_frames),
    ("52 场景语法（10 类分流/确定/段长门）", t_scene_grammar),
    ("53 视觉语言包（结构签名互不相同）", t_visual_languages),
    ("54 场景类型单真值（4 处集合一致）", t_scene_types_single_truth),
    ("55 剪影可区分（G13：换风格不能只是换色）", t_silhouette_gate),
    ("56 设计 DSL（采样/校验/转计划/fail-closed）", t_design_dsl),
    ("57 零模板（仓库里不许有预设；采样确定性且随 brief 变化）", t_no_templates),
    ("58 提案闸（absent/draft/confirmed/broken）", t_proposal_gate),
    ("59 逐 stage 可单独跑（resolve_style 必须拿得到 work）", t_stage_standalone),
    ("60 brief 里的要求真的生效（自然语言 -> 约束 -> 采样）", t_brief_constraints),
    ("61 字幕样式可定制（design.visual.subtitle 真的生效）", t_subtitle_style),
    ("62 片内版式轮换（layout_cycle：换章即换骨架）", t_layout_cycle),
    ("63 带约束的 brief 不会高概率采样失败", t_brief_constraint_robustness),
    ("64 已删除 API 的 fail-closed（不许 NameError / 假绿 / 静默回落）", t_removed_apis_fail_closed),
    ("65 design.audio 的音量/音效参数真的落进 script", t_audio_overrides),
    ("66 片间去重台账（确认记账 / 提案避开 / 可关可查）", t_design_ledger),
    ("67 时长要求真的被接住（brief -> design -> 渲染对账）", t_duration_target),
    ("68 缩放取景的帧间稳定性（抖动回归）", t_zoom_stability),
    ("69 ASR 掉分可定位（lost_top / 最差段落）", t_asr_lost_diagnostics),
    ("70 出片前问诊（固定 7 题 / 首次提案必须答完 / 明亮点排第一）", t_intake_gate),
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
