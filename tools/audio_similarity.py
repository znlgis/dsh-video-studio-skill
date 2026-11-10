#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""audio_similarity.py — 配乐/音效同质化诊断 + 音频改造验收（只读，不产片）。

用法：
    python tools/audio_similarity.py                 # 打印 4 组对照
    python tools/audio_similarity.py --strict        # 额外按契约阈值判定，失败 exit 1
    python tools/audio_similarity.py --out dir/      # 把对照样本写成 wav 便于人耳试听

四组对照（阈值来自 skills/video-studio/references/audio-variation.md §5）：

    A 跨片配乐（v0.7）：同风格/同 bpm/同调、只换内容指纹 -> 相关性（目标 <= 0.85）；
                换风格 -> 相关性（目标 <= 0.52，且不能比改造前更接近）
    B 跨片音效（v0.7）：同 cue 序列，legacy（不传 style/content_fp）必须逐样本相同；
                不同 content_fp 必须不同（否则"跨片音效都一样"没被修掉）
    C 片内分离（v0.7）：duck 默认深度（目标 >= 3.5 dB）与配乐/音效的频谱质心对照
    D 旁白优先（v0.7.1）：人声段 旁白/背景 响度差（目标 >= 12 dB）+ 侧链让位 >= 4 dB

本脚本在改造前是"诊断工具"，改造后是"验收工具"：--strict 会逐条对阈值判定。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "skills" / "video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

# 契约 §5 的阈值（改契约时两边一起改）
THRESH_A_SAME_STYLE_MAX = 0.85      # 同风格、只换内容指纹的相关性上限
THRESH_A_CROSS_STYLE_MAX = 0.52     # 换风格的相关性上限（改造前 0.534）
# 实测余量提示（v0.7.2）：本机稳定测到 0.514（连跑 3 次逐位相同），离阈值只剩 0.006。
# 这个生成链是确定性的，所以换平台/换 numpy 若把 0.514 推到 0.52 以上，
# 更可能是「风格差异化真的缩水了」而不是浮点噪声 —— 请先查 scorekit 的变奏参数，
# 别第一反应去抬阈值（抬到 0.53 就离改造前 0.534 只剩 0.004，等于这条门禁白设）。
THRESH_C_DUCK_MIN = 3.5             # duck 默认深度下限（dB）
THRESH_D_SNR_MIN = 12.0             # 人声段 旁白/背景 响度差下限（dB）

FAILS = []


def _ok(name, cond, detail):
    print("   [%s] %s  %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append("%s: %s" % (name, detail))


def _fmt_db(v):
    """dB 值格式化：NaN（无可判定区间）打印成 'n/a' 而不是让 %f 抛 TypeError。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if v != v else ("%.1f" % v)


def _rms(x):
    a = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(a)))) if a.size else 0.0


def _corr(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if y.ndim == 2:
        y = y.mean(axis=1)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    if n == 0:
        return float("nan")
    sx, sy = float(np.std(x)), float(np.std(y))
    if sx <= 1e-12 or sy <= 1e-12:
        # 常量/全静音信号：相关系数没有定义。旧实现返回 0，于是"配乐渲染成静音"这种
        # 最严重的回归会让 A1/A2 反而判 PASS（0 <= 0.85）。
        return float("nan")
    return float(np.mean((x - x.mean()) * (y - y.mean())) / (sx * sy))


def _centroid_hz(x, sr=48000):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    return float((spec * freqs).sum() / (spec.sum() + 1e-12))


def _utf8_stdio():
    for st in (sys.stdout, sys.stderr):
        try:
            if st is not None and not st.isatty():
                st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            pass


def _music(dur, style, fp):
    import scorekit as SK
    samples, _rep = SK.render_styled(dur, style=style, bpm=110, key="Dm",
                                     mood="steady", seed=7, fingerprint=fp)
    return samples


def _cue_track(content_fp=None, style=None, key=None):
    import filmkit as K
    scenes = [{"id": "l01", "kind": "hook", "start": 0.0, "end": 2.0},
              {"id": "l02", "kind": "body", "start": 2.0, "end": 6.0}]
    rows = [{"at": "l02", "type": "ui_click"}, {"at": "l02+0.5", "type": "whoosh"}]
    bus = K.CueBus(scenes=scenes, total=6.0)
    bus.extend(rows)
    return bus.build_audio(total=6.0, verbose=False, style=style,
                           content_fp=content_fp, key=key)


def _voice_priority_snr(sr=48000, guard_snr_db=12.0):
    """量"人声比背景响多少"：用真实 scorekit 配乐 + 类人声，按 st_audio 的默认参数混音。

    返回 (人声活跃区 SNR dB, 间隙背景 RMS, 人声处背景额外让位 dB)。
    口径与 `filmkit.voice_snr` 一致：只统计人声活跃区（含间隙会稀释问题）。

    guard_snr_db 默认 12（= st_audio 默认，量"最终交付"的 SNR）；测"侧链本身让位多少"
    时传 0 关掉兜底——否则兜底的整体补压会被算成侧链效果，D2 在侧链被删掉时也能过。
    """
    import filmkit as K
    import numpy as np
    import scorekit as SK
    n = int(6.0 * sr)
    t = np.arange(n) / float(sr)
    voice = np.zeros(n, dtype=np.float64)
    for a, b in ((0.2, 2.6), (3.4, 5.8)):        # 两段"说话"，中间留间隙
        i, j = int(a * sr), int(b * sr)
        voice[i:j] = 0.12 * np.sin(2 * np.pi * 220 * t[i:j])
    # 真实配乐（scorekit 输出 RMS 约 0.15，比"类人声"响 —— 正是需要侧链/兜底的场景）
    music, _ = SK.render_styled(6.0, style="tech-deep", bpm=110, key="Am",
                                mood="steady", seed=7, fingerprint="d-group")
    music = np.asarray(music, dtype=np.float32)
    out = K.mix_film(voice.astype(np.float32), music, None, total=6.0, fade=0.0,
                     music_gain_db=-12.0, voice_duck_db=6.0,
                     guard_snr_db=float(guard_snr_db), verbose=False)
    v2 = np.stack([voice, voice], axis=1)
    bg = out - v2
    snr = K.voice_snr(voice.astype(np.float32), bg)
    gap = slice(int(2.8 * sr), int(3.2 * sr))
    gap_rms = float(np.sqrt(np.mean(bg[gap] ** 2)))
    # 人声处背景相对"未压背景"的额外让位：未压背景 = music * 10^(-12/20)
    base_bg = music * 10 ** (-12.0 / 20.0)
    b_talk = slice(int(0.5 * sr), int(2.2 * sr))
    duck_eff = 20 * np.log10((float(np.sqrt(np.mean(base_bg[b_talk] ** 2))) + 1e-12) /
                             (float(np.sqrt(np.mean(bg[b_talk] ** 2))) + 1e-12))
    return snr, gap_rms, duck_eff


def _duck_default():
    src = (SCRIPTS / "render.py").read_text(encoding="utf-8")
    m = re.search(r"duck_default\s*=\s*([0-9.]+)", src)
    return float(m.group(1)) if m else float("nan")


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="配乐/音效相似度诊断（v0.7 验收）")
    ap.add_argument("--out", default="", help="把对照样本写成 wav 到这个目录")
    ap.add_argument("--dur", type=float, default=20.0, help="配乐对照时长（秒）")
    ap.add_argument("--strict", action="store_true", help="按契约 §5 阈值判定，失败 exit 1")
    args = ap.parse_args(argv)

    import sfxkit as SX

    print("=" * 72)
    print("A. 跨片配乐（差异来源 = 风格 / bpm / 调 / 内容指纹 / seed）")
    print("=" * 72)
    a1 = _music(args.dur, "tech-deep", "content-a")
    a2 = _music(args.dur, "tech-deep", "content-b")
    a3 = _music(args.dur, "magazine", "content-a")
    a4 = _music(args.dur, "tech-deep", "content-a")   # 同指纹复渲染，验证确定性
    same_style = _corr(a1, a2)
    cross_style = _corr(a1, a3)
    rms_a1 = _rms(a1)
    print("  配乐非静音（RMS > -60 dBFS）    : %s  （rms=%.4f，静音时相关系数无定义）"
          % (rms_a1 > 0.001, rms_a1))
    print("  同风格·同bpm·同调，只换内容指纹 : corr = %.3f  （目标 <= %.2f）"
          % (same_style, THRESH_A_SAME_STYLE_MAX))
    print("  换风格（tech-deep -> magazine） : corr = %.3f  （目标 <= %.2f）"
          % (cross_style, THRESH_A_CROSS_STYLE_MAX))
    print("  同指纹两次渲染逐样本相同        : %s" % bool(np.array_equal(a1, a4)))
    if args.strict:
        # A0 是前置条件：若配乐渲染成静音/常量，A1/A2 的相关系数没有定义（NaN），
        # 必须显式判 FAIL，而不是让 NaN 比较悄悄变成 False 后只剩一句 corr=nan。
        _ok("A0 配乐非静音", rms_a1 > 0.001, "rms=%.4f" % rms_a1)
        _ok("A1 同风格换指纹可区分", same_style <= THRESH_A_SAME_STYLE_MAX,
            "corr=%.3f" % same_style)
        _ok("A2 换风格更不像", cross_style <= THRESH_A_CROSS_STYLE_MAX,
            "corr=%.3f" % cross_style)
        _ok("A3 配乐可复现", bool(np.array_equal(a1, a4)), "同指纹逐样本一致")

    print()
    print("=" * 72)
    print("B. 跨片音效（CueBus 内容派生 seed）")
    print("=" * 72)
    b_legacy1, b_legacy2 = _cue_track(), _cue_track()
    b_new_a = _cue_track("aaaa1111", "tech-deep", "Dm")
    b_new_b = _cue_track("bbbb2222", "tech-deep", "Dm")
    b_new_a2 = _cue_track("aaaa1111", "tech-deep", "Dm")
    legacy_same = bool(np.array_equal(b_legacy1, b_legacy2))
    cross_diff = not bool(np.array_equal(b_new_a, b_new_b))
    repeat_same = bool(np.array_equal(b_new_a, b_new_a2))
    print("  legacy（不传 style/fp）两次相同 : %s  （必须 True，v0.4 兼容）" % legacy_same)
    print("  不同内容指纹 -> 音效轨不同      : %s  （必须 True）" % cross_diff)
    print("  同内容指纹两次相同              : %s  （必须 True，可复现）" % repeat_same)
    if args.strict:
        _ok("B1 legacy 逐样本兼容", legacy_same, "两次渲染相同")
        _ok("B2 跨片音效可区分", cross_diff, "不同 content_fp 不再逐样本相同")
        _ok("B3 音效可复现", repeat_same, "同 content_fp 逐样本一致")

    print()
    print("=" * 72)
    print("C. 片内分离：混音 duck 深度 + 频谱质心对照")
    print("=" * 72)
    duck = _duck_default()
    print("  duck 默认深度                   : %.1f dB  （目标 >= %.1f）"
          % (duck, THRESH_C_DUCK_MIN))
    for name, x in (("impact", SX.render("impact", seed=1000)),
                    ("whoosh", SX.render("whoosh", seed=1000)),
                    ("riser", SX.render("riser", seed=1000)),
                    ("music(前4s)", a1[: 48000 * 4])):
        print("  %-12s 频谱质心 ~ %6.0f Hz" % (name, _centroid_hz(x)))
    print("  提示音调性跟随：in_key 现在接受 key=（配乐调性），不再硬编码 Dm。")
    if args.strict:
        _ok("C1 duck 加深", duck >= THRESH_C_DUCK_MIN, "duck=%.1f dB" % duck)

    print()
    print("=" * 72)
    print("D. 旁白优先：人声与背景的响度差（v0.7.1）")
    print("=" * 72)
    snr_db, base_db, _duck_with_guard = _voice_priority_snr()
    # 侧链让位单独量一次（guard_snr_db=0）：兜底的整体补压也是"背景变低"，
    # 混在一起算会把 D2 变成"guard 生效"的证据，侧链被删掉时 D2 照样 PASS。
    _snr_no_guard, _gap_no_guard, duck_db_eff = _voice_priority_snr(guard_snr_db=0.0)
    print("  人声段 旁白/背景 响度差       : %s dB  （目标 >= %.1f，含 guard 兜底）"
          % (_fmt_db(snr_db), THRESH_D_SNR_MIN))
    print("  人声处背景额外让位             : %s dB  （目标 >= 4，仅侧链，guard 关）"
          % _fmt_db(duck_db_eff))
    print("  间隙里背景电平                 : %.3f（应为恢复后的基线）" % base_db)
    if args.strict:
        _ok("D1 旁白听得清", snr_db is not None and snr_db == snr_db
            and snr_db >= THRESH_D_SNR_MIN, "SNR=%s dB" % _fmt_db(snr_db))
        _ok("D2 旁白侧链生效", duck_db_eff >= 4.0, "让位 %s dB" % _fmt_db(duck_db_eff))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        from scorekit import save as music_save
        music_save(out / "music_tech_deep_a.wav", a1)
        music_save(out / "music_tech_deep_b.wav", a2)
        music_save(out / "music_magazine.wav", a3)
        for t in ("whoosh", "riser", "impact"):
            SX.save(out / ("sfx_%s.wav" % t), SX.render(t, seed=1000))
        print("\n对照样本已写入: %s" % out)

    if args.strict:
        print()
        if FAILS:
            print("STRICT FAIL: %d 项未达标" % len(FAILS))
            for f in FAILS:
                print("  - %s" % f)
            return 1
        print("STRICT PASS: 全部阈值达标（A/B/C/D 四组）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
