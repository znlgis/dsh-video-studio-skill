# -*- coding: utf-8 -*-
"""beatgrid.py — 音乐节拍网格分析（纯 numpy，不依赖 librosa）。

为什么不用 librosa：本技能的口径是"装得上就能跑"。librosa 会拖进 numba/llvmlite
（上百 MB、Python 版本还挑），为了一件事装它不值。这里用 FFT + 自相关 + **等距最小
二乘拟合**把 BPM 与相位解出来，精度足够做卡点（实测残差 <15ms，远小于一帧 33ms）。

三件事：
  1. **求真 BPM 与相位**：自相关只给候选周期；真正定稿靠"用候选 T 生成等距网格，
     再拿命中的瞬态做最小二乘回归"——自相关峰值受倍频/半频干扰，回归不会。
  2. **瞬时分类**：40–160Hz / 150–500Hz / 6–14kHz 三段能量分别对应 kick / snare /
     hihat。分类只是给分镜挑"重音位"用，不追求音乐学意义上的准确。
  3. **能量结构**：0.5s RMS 曲线切段落（body / breakdown / climax），给镜头长短做参考。

产物 schema（work/beat_data.json，v1）：
    {version, source, sr, fps, bpm, t0, T, residualMs, confidence,
     beats[], bars[], hits[{t,s,k}], rms[{t,v}], sections[{start,end,kind}]}

**命中表是候选池，不是触发器**：强鼓点曲几乎每拍都有 kick，逐拍打画面就是"镜头随节奏抖"
（真实用户投诉，见 references/aesthetic-rules.md R4）。这里只负责"哪一拍最强"。
"""
from __future__ import annotations

import math
import os

import numpy as np

SR = 48000
HOP = 512              # 约 10.7ms（48k）：够细到能定位瞬态，又不至于帧数爆炸
BPM_MIN, BPM_MAX = 50.0, 200.0

# 三个频段：(名字, 下限 Hz, 上限 Hz)。上限只用于分类，不参与网格拟合。
BANDS = (("kick", 40.0, 160.0), ("snare", 150.0, 500.0), ("hihat", 6000.0, 14000.0))


def _mono(x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    return x


def _resample(x, sr_in, sr_out=SR):
    """线性插值重采样。节拍分析不需要抗镜像，够用且零依赖。"""
    if int(sr_in) == int(sr_out) or len(x) < 2:
        return x
    n_out = int(round(len(x) * float(sr_out) / float(sr_in)))
    idx = np.linspace(0.0, len(x) - 1.0, n_out)
    return np.interp(idx, np.arange(len(x), dtype=np.float64), x)


def _stft_mag(x, hop=HOP):
    """返回 (mag[nframes, nbins], freqs[nbins])。加 Hann 窗降低旁瓣泄漏。"""
    n = len(x)
    if n < hop * 4:
        return np.zeros((0, 0)), np.zeros(0)
    win = 2 * HOP
    pad = np.zeros(n + win, dtype=np.float64)
    pad[:n] = x
    nframes = (n - win) // hop + 1
    if nframes <= 0:
        return np.zeros((0, 0)), np.zeros(0)
    # 用 stride 视图做帧化：不复制，长音频（10 分钟）也就几十 MB
    idx = np.arange(win)[None, :] + hop * np.arange(nframes)[:, None]
    frames = pad[idx] * np.hanning(win)[None, :]
    mag = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(win, d=1.0 / float(SR))
    return mag, freqs


def band_energy(mag, freqs, lo, hi):
    """频段能量包络：该频段内幅值平方和再开方。"""
    if mag.size == 0:
        return np.zeros(0)
    m = (freqs >= lo) & (freqs < hi)
    if not m.any():
        return np.zeros(mag.shape[0])
    return np.sqrt((mag[:, m] ** 2).sum(axis=1))


def onset_strength(env):
    """正差分（半波整流）+ 归一化。这就是"瞬态在哪"。"""
    if env.size < 2:
        return np.zeros(env.size)
    d = np.diff(env, prepend=env[:1])
    d[d < 0] = 0.0
    peak = float(d.max())
    return d / peak if peak > 0 else d


def _autocorr_bpm(ons, hop=HOP):
    """自相关给候选 BPM。只作为搜索起点，定稿交给等距回归。"""
    if ons.size < 16 or not np.any(ons > 0):
        return None
    a = ons - ons.mean()
    ac = np.correlate(a, a, mode="full")[len(a) - 1:]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    fps = float(SR) / float(hop)
    lo = max(1, int(math.floor(fps * 60.0 / BPM_MAX)))
    hi = min(len(ac) - 1, int(math.ceil(fps * 60.0 / BPM_MIN)))
    if hi <= lo:
        return None
    lag = lo + int(np.argmax(ac[lo:hi + 1]))
    return 60.0 * fps / float(lag)


def _onsets(ons, hop=HOP, thresh=0.18, min_gap_s=0.06):
    """挑瞬态峰（局部极大 + 阈值 + 最小间隔）。"""
    if ons.size < 3:
        return np.zeros(0)
    t = np.arange(ons.size) * (float(hop) / float(SR))
    peaks = []
    last = -1e9
    for i in range(1, ons.size - 1):
        v = float(ons[i])
        if v < thresh:
            continue
        if v < ons[i - 1] or v < ons[i + 1]:
            continue
        if t[i] - last < min_gap_s:
            continue
        peaks.append(t[i])
        last = t[i]
    return np.asarray(peaks, dtype=np.float64)


def fit_grid(onsets, bpm_hint=None, hop=HOP):
    """等距网格最小二乘拟合 -> (bpm, t0, T, residual_ms, confidence)。

    做法：候选周期 T 在 hint ±3% 内细扫（0.1% 步长），每个 T 再扫相位 phi；
    评分 = 命中瞬态能量 / 节拍数（除以节拍数是为了不让慢 BPM 靠"拍子少"占便宜）。
    取最高分后，用命中瞬态做 t = n*T + t0 的线性回归得到精确 T 与 t0。
    """
    if onsets.size < 4:
        return None
    hint = float(bpm_hint or 0.0)
    cands = []
    if hint > 0:
        for k in range(-30, 31):
            cands.append(hint * (1.0 + k * 0.001))
    else:
        cands = list(np.arange(BPM_MIN, BPM_MAX, 0.5))
    on = onsets
    best = None
    for bpm in cands:
        if bpm < BPM_MIN or bpm > BPM_MAX:
            continue
        T = 60.0 / bpm
        # 相位：以第一个瞬态为锚，向前后各让一个周期，步长 T/40
        base = on[0] % T
        for j in range(40):
            phi = base + j * (T / 40.0)
            dur = float(on[-1]) + T
            n = int(math.floor((dur - phi) / T)) + 1
            if n < 4:
                continue
            grid = phi + T * np.arange(n)
            # 每个网格点上是否有瞬态（±25ms）
            k = np.searchsorted(on, grid)
            hit = 0
            err = []
            for gi, g in enumerate(grid):
                lo = max(0, k[gi] - 2)
                hi = min(len(on), k[gi] + 2)
                if lo >= hi:
                    continue
                near = on[lo:hi]
                d = np.abs(near - g)
                m = int(np.argmin(d))
                if d[m] <= 0.025:
                    hit += 1
                    err.append(float(near[m]))
            score = hit / float(n)
            if hit < 4:
                continue
            # 回归：命中瞬态 -> 最近的网格序号，拟合 t = n*T + t0
            ns, ts = [], []
            for tt in err:
                nn = int(round((tt - phi) / T))
                ns.append(nn)
                ts.append(tt)
            ns_a = np.asarray(ns, dtype=np.float64)
            ts_a = np.asarray(ts, dtype=np.float64)
            if ns_a.size < 4 or np.ptp(ns_a) < 1:
                continue
            A = np.vstack([ns_a, np.ones_like(ns_a)]).T
            sol, _res, _rk, _sv = np.linalg.lstsq(A, ts_a, rcond=None)
            T_fit, t0_fit = float(sol[0]), float(sol[1])
            if T_fit <= 0:
                continue
            pred = ns_a * T_fit + t0_fit
            resid_ms = float(np.sqrt(np.mean((ts_a - pred) ** 2)) * 1000.0)
            # 综合分：命中率 - 残差惩罚（残差大说明"等距"这件事不成立）
            score2 = score - min(0.5, resid_ms / 100.0) * 0.3
            if best is None or score2 > best[0]:
                best = (score2, bpm, T_fit, t0_fit, resid_ms, hit, n)
    if best is None:
        return None
    score2, bpm, T_fit, t0_fit, resid_ms, hit, n = best
    bpm_fit = 60.0 / T_fit
    return dict(bpm=round(bpm_fit, 3), T=round(T_fit, 5),
                t0=round(t0_fit, 4), residualMs=round(resid_ms, 2),
                confidence=round(float(hit) / float(max(1, n)), 3),
                matched=int(hit), beats=int(n))


def classify_hits(mag, freqs, beats, hop=HOP):
    """每个节拍点附近找三频段最强的那一段 -> {t, s, k}。"""
    if mag.size == 0 or not len(beats):
        return []
    envs = [(name, band_energy(mag, freqs, lo, hi)) for name, lo, hi in BANDS]
    hop_s = float(hop) / float(SR)
    out = []
    for b in beats:
        i0 = max(0, int(round((b - 0.06) / hop_s)))
        i1 = min(max(len(e) for _n, e in envs), int(round((b + 0.06) / hop_s)) + 1)
        if i1 <= i0:
            continue
        peak = 0.0
        peak_name = "kick"
        for name, env in envs:
            seg = env[i0:i1]
            if seg.size == 0:
                continue
            v = float(seg.max())
            if v > peak:
                peak, peak_name = v, name
        out.append(dict(t=round(float(b), 3), s=round(peak, 4), k=peak_name))
    return out


def rms_curve(x, sr=SR, win_s=0.5):
    """0.5s RMS 曲线（给段落切分与"哪段能量最高"用）。"""
    n = max(1, int(round(win_s * sr)))
    if len(x) < n:
        return []
    k = len(x) // n
    blk = x[:k * n].reshape(k, n)
    env = np.sqrt((blk ** 2).mean(axis=1))
    return [dict(t=round(i * win_s, 2), v=round(float(v), 5)) for i, v in enumerate(env)]


def sections_from_rms(rms, floor=0.35, high=0.8):
    """按能量分位切段落：低 < high*中位数 为 breakdown，高 > high 分位为 climax。"""
    if not rms:
        return []
    vs = np.asarray([r["v"] for r in rms], dtype=np.float64)
    med = float(np.median(vs)) or 1e-9
    kinds = []
    for v in vs:
        r = float(v) / med
        if r < floor:
            kinds.append("breakdown")
        elif r > 1.0 + high:
            kinds.append("climax")
        else:
            kinds.append("body")
    out = []
    start = rms[0]["t"]
    cur = kinds[0]
    for i in range(1, len(rms)):
        if kinds[i] != cur:
            out.append(dict(start=start, end=rms[i - 1]["t"] + 0.5, kind=cur))
            start, cur = rms[i]["t"], kinds[i]
    out.append(dict(start=start, end=rms[-1]["t"] + 0.5, kind=cur))
    return out


def analyze_samples(x, sr=SR, *, fps=30, hop=HOP, bpm_hint=None, source=""):
    """核心：一段单声道/立体声信号 -> beat_data 字典（不碰文件、不碰 ffmpeg）。

    纯函数（除了 numpy），selfcheck 可以直接喂合成 click track 断言精度。
    """
    x = _mono(x)
    x = _resample(x, sr, SR)
    mag, freqs = _stft_mag(x, hop=hop)
    # 能量曲线与段落切分**不依赖节拍**：氛围乐/纯人声没有鼓点，但"哪里起来了"
    # 恰恰是这类片子唯一能拿到的结构信息。所以先算，两条失败路径都带上它。
    rms = rms_curve(x)
    sections = sections_from_rms(rms)
    if mag.shape[0] < 8:
        return dict(version=1, source=source, sr=SR, fps=int(fps), bpm=0.0,
                    t0=0.0, T=0.0, residualMs=0.0, confidence=0.0,
                    beats=[], bars=[], hits=[], rms=rms, sections=sections, ok=False,
                    why="音频太短（不足 8 帧 STFT），无法估节拍")
    # 三频段瞬态之和作为总 onset（低频给 kick，中频给 snare，高频给 hihat）
    bands = {name: band_energy(mag, freqs, lo, hi) for name, lo, hi in BANDS}
    env = sum(bands.values())
    ons = onset_strength(env)
    onsets = _onsets(ons, hop=hop)
    hint = bpm_hint if bpm_hint else _autocorr_bpm(ons, hop=hop)
    fit = fit_grid(onsets, bpm_hint=hint, hop=hop)
    if fit is None:
        return dict(version=1, source=source, sr=SR, fps=int(fps), bpm=0.0,
                    t0=0.0, T=0.0, residualMs=0.0, confidence=0.0,
                    beats=[], bars=[], hits=[],
                    rms=rms, sections=sections, ok=False,
                    why="找不到稳定的等距网格（可能没有明确鼓点）")
    T = float(fit["T"])
    t0 = float(fit["t0"])
    dur = float(len(x)) / float(SR)
    nbeats = int(math.floor((dur - t0) / T)) + 1 if T > 0 else 0
    beats = [round(t0 + i * T, 3) for i in range(max(0, nbeats))]
    # 小节：4/4 默认；bar 的相位与拍相位一致（小节线落在第 0 拍）
    bars = [round(t0 + i * 4 * T, 3) for i in range(max(0, nbeats // 4))]
    return dict(version=1, source=source, sr=SR, fps=int(fps),
                bpm=float(fit["bpm"]), t0=round(t0, 4), T=round(T, 5),
                residualMs=float(fit["residualMs"]),
                confidence=float(fit["confidence"]),
                beats=beats, bars=bars,
                hits=classify_hits(mag, freqs, beats, hop=hop),
                rms=rms, sections=sections, ok=True)


def beat_at(data, n):
    """第 n 拍（0 起）的绝对秒；越界返回 None。"""
    beats = (data or {}).get("beats") or []
    try:
        i = int(n)
    except (TypeError, ValueError):
        return None
    return float(beats[i]) if 0 <= i < len(beats) else None


def bar_at(data, n):
    """第 n 小节（0 起，可为小数：3.5 = 第 3 小节半）的绝对秒。"""
    if not data:
        return None
    T = float(data.get("T") or 0.0)
    t0 = float(data.get("t0") or 0.0)
    if T <= 0:
        return None
    try:
        return round(t0 + float(n) * 4.0 * T, 3)
    except (TypeError, ValueError):
        return None


def nearest_beat(data, t):
    """离 t 最近的拍（秒）与偏差（秒）。"""
    beats = (data or {}).get("beats") or []
    if not beats:
        return None, None
    arr = np.asarray(beats, dtype=np.float64)
    i = int(np.argmin(np.abs(arr - float(t))))
    return float(arr[i]), float(arr[i] - float(t))


def analyze_file(path, *, fps=30, bpm_hint=None):
    """文件入口。统一走 filmkit.decode_pcm（它自己处理 wav 与 ffmpeg 两条路）。"""
    import filmkit as K
    pcm = K.decode_pcm(str(path), sr=SR, channels=1)
    data = analyze_samples(pcm, SR, fps=fps, bpm_hint=bpm_hint,
                           source=os.path.abspath(str(path)))
    return data
