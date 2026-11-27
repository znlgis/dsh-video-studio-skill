# -*- coding: utf-8 -*-
"""audio_offset.py — 实测成片音轨相对混音母带的偏移（给 mux.audio_offset_ms 用）。

为什么需要实测：mux 之后 mp4 里的音轨是 AAC 编码过的，编码器会在音轨头留 priming 帧，
不同 ffmpeg 版本/采样率/编码器设置下长度还不一样。这个偏移不大（常见 ~20–45 ms，
48 kHz @30fps 约 0.6–1.4 帧），但"音画同步"门禁量的是毫秒级：不补，整片会稳定地偏一个方向，
然后被误判成 cues 打错——修 cues 永远修不好。

做法：不去猜编码器参数，直接**拿交付的 mp4 跟混音母带比**。两边各算一条短时能量包络
（20 ms 步长），在 ±200 ms 内做归一化互相关，取峰值位置的时延。能量包络对 AAC 的
相位/频谱改动不敏感，但对"声音在哪一毫秒"很敏感，正好是我们要的东西。

用法：
    python tools/audio_offset.py --video out/film.mp4 --audio work/audio.wav --fps 30
    python tools/audio_offset.py --video out/film.mp4 --audio work/audio.wav --json

退出码：0 = 测得（即使偏移是 0）；2 = 测不了（缺 ffmpeg / 音轨为空 / 两边太不像），
此时**不要**瞎填偏移，0 就是对的（默认不补偿）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np

SR = 48000          # 与 filmkit.SR 同口径：母带就是 48 kHz
HOP_MS = 20         # 包络步长：20 ms 足够分辨一帧 @30fps 的偏移，又不会被单采样噪声带偏
MAX_LAG_MS = 200    # 搜索上限：超过 200 ms 就不是编码偏移而是别的问题（cue 打错/音轨换了）
MIN_SIM = 0.30      # 归一化互相关峰值低于它 -> 两边根本不是同一条声音，拒绝给结论


def _ffmpeg() -> str:
    """复用技能里的 ffmpeg 解析逻辑（FILMKIT_FFMPEG 环境变量也认）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "..", "skills", "video-studio", "scripts")
    cand = os.path.normpath(cand)
    if os.path.isdir(cand):
        sys.path.insert(0, cand)
        try:
            import filmkit as K
            return str(K.FFMPEG)
        except Exception:                                   # noqa: BLE001
            pass
    return "ffmpeg"


def decode_mono(path: str, ffmpeg: str, sr: int = SR) -> np.ndarray:
    """把任意音频/视频文件的音轨解成单声道 float32（f32le 直读，不经临时文件）。"""
    cmd = [ffmpeg, "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
           "-ar", str(sr), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        msg = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise SystemExit("解不出音轨（%s）：%s" % (path, msg[-1] if msg else "ffmpeg 失败"))
    if not p.stdout:
        raise SystemExit("音轨是空的：%s" % path)
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def envelope(x: np.ndarray, sr: int = SR, hop_ms: float = HOP_MS) -> np.ndarray:
    """短时 RMS 包络。用**差分**再用会更好？不用：静音段在差分里会放大底噪。"""
    hop = max(1, int(round(sr * hop_ms / 1000.0)))
    n = len(x) // hop
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    blk = x[: n * hop].reshape(n, hop).astype(np.float64)
    return np.sqrt((blk ** 2).mean(axis=1))


def _norm(a: np.ndarray) -> np.ndarray:
    a = a - a.mean()
    s = a.std()
    return a / s if s > 1e-9 else np.zeros_like(a)


def measure(video: str, audio: str, ffmpeg: str, fps: float = 30.0,
            max_lag_ms: float = MAX_LAG_MS, hop_ms: float = HOP_MS) -> dict:
    """-> {offset_ms, offset_frames, offset_samples, sim, ok}

    符号约定与 render.py 一致：**正值 = mp4 里的音频比母带晚**（听起来晚了），
    要把它整体提前，就写 mux.audio_offset_ms = 这个正值。
    """
    a = envelope(decode_mono(video, ffmpeg), hop_ms=hop_ms)
    b = envelope(decode_mono(audio, ffmpeg), hop_ms=hop_ms)
    m = min(len(a), len(b))
    if m < 8:
        return {"ok": False, "why": "两边有效包络太短（%d / %d 帧）" % (len(a), len(b))}
    a, b = _norm(a[:m]), _norm(b[:m])
    max_lag = int(round(max_lag_ms / hop_ms))
    # 逐 lag 算归一化互相关：a[t] 对 b[t-lag]。lag>0 = mp4 的声音晚出现。
    best_lag, best = 0, -1.0
    sims = {}
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[lag:], b[: m - lag]
        else:
            x, y = a[: m + lag], b[-lag:]
        if x.size < 8:
            continue
        sim = float(np.dot(x, y) / max(1e-9, x.size))
        sims[lag] = sim
        if sim > best:
            best_lag, best = lag, sim
    # 亚步长插值：20 ms 的步长只给出 ±10 ms 的分辨率，但一帧 @30fps 是 33 ms，
    # 「差半帧」和「差一帧」在门禁里是两种结论。用峰值左右两个点做抛物线拟合，
    # 把精度拉到毫秒级（不增加任何解码成本）。
    frac = 0.0
    l0, l1, l2 = sims.get(best_lag - 1), sims.get(best_lag), sims.get(best_lag + 1)
    if l0 is not None and l1 is not None and l2 is not None:
        denom = (l0 - 2.0 * l1 + l2)
        if abs(denom) > 1e-12:
            frac = 0.5 * (l0 - l2) / denom
            frac = max(-0.5, min(0.5, frac))
    offset_ms = (best_lag + frac) * hop_ms
    return {
        "ok": bool(best >= MIN_SIM),
        "offset_ms": round(offset_ms, 2),
        "offset_frames": round(offset_ms / 1000.0 * float(fps), 3),
        "offset_samples": int(round(offset_ms / 1000.0 * SR)),
        "similarity": round(best, 3),
        "minSimilarity": MIN_SIM,
        "hopMs": hop_ms,
        "maxLagMs": max_lag_ms,
        "video": os.path.abspath(video),
        "audio": os.path.abspath(audio),
        "fps": float(fps),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="实测成片音轨偏移（给 film.json 的 mux.audio_offset_ms）")
    ap.add_argument("--video", required=True, help="成片或任意含音轨的文件（通常 out/film.mp4）")
    ap.add_argument("--audio", required=True, help="混音母带（通常 work/audio.wav）")
    ap.add_argument("--fps", type=float, default=30.0, help="帧率（只用于换算成帧，默认 30）")
    ap.add_argument("--max-lag-ms", type=float, default=MAX_LAG_MS,
                    help="搜索半径（默认 %d ms；超过这个值多半不是编码偏移）" % MAX_LAG_MS)
    ap.add_argument("--json", action="store_true", help="只输出 JSON（机读）")
    ap.add_argument("--write", default="", help="把结果写进这个 JSON 文件（例如 work/audio_offset.json）")
    a = ap.parse_args(argv)
    for p in (a.video, a.audio):
        if not os.path.exists(p):
            raise SystemExit("找不到文件：%s" % p)
    res = measure(a.video, a.audio, _ffmpeg(), fps=a.fps, max_lag_ms=a.max_lag_ms)
    if a.write:
        d = os.path.dirname(os.path.abspath(a.write))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        if not res.get("ok"):
            print("[测不出] %s" % (res.get("why") or
                                   "两段声音相关度 %.3f 低于 %.2f：它们很可能不是同一条声音"
                                   % (res.get("similarity", 0.0), MIN_SIM)))
            print("  结论：不要填偏移（缺省 0 就是对的）；先确认 --video 与 --audio 是同一版。")
            return 2
        sign = "晚" if res["offset_ms"] > 0 else ("早" if res["offset_ms"] < 0 else "对得上")
        print("== 音轨偏移实测")
        print("   mp4 音轨相对母带：%+.2f ms（%+.3f 帧 @%.0ffps）-> %s"
              % (res["offset_ms"], res["offset_frames"], a.fps, sign))
        print("   相关度 %.3f（阈值 %.2f），步长 %d ms，搜索半径 %d ms"
              % (res["similarity"], MIN_SIM, res["hopMs"], res["maxLagMs"]))
        print("")
        if res["offset_ms"] > 0:
            print("   想让交付成片对上，把这个写进 film.json（正值 = 音频提前）：")
        elif res["offset_ms"] < 0:
            print("   想让交付成片对上，把这个写进 film.json（负值 = 音频推迟）：")
        else:
            print("   已经是 0，不需要补偿。要显式固定这个结论也可以写：")
        print("     \"mux\": {\"audio_offset_ms\": %.2f}" % res["offset_ms"])
        print("   然后重跑 --stage audio mux qa。")
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
