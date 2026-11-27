# -*- coding: utf-8 -*-
"""reviewkit.py — 每镜固定验收帧 + 输出音轨偏移补偿。

两块都是"交付凭据"的地基，故意做成**纯函数**（不读文件、不碰画布），
好让 selfcheck / ci_check 在不装 ffmpeg 的机器上也能断言。

## 一、每镜固定验收帧（review frames）

问题：抽帧靠手感。同一部片这次抽 3.2s、下次抽 4.7s，"上次那帧是好的"没法复核，
评审也只能对着随手抽的图说话。

做法：在 layout 阶段就把每段的两个帧号**算死并写进 timeline.json**：

    enter   入场后一点点（动作已经起来、还没落定）
    settle  落定后一点点（关键信息已经就在画面上）

两个都夹在 [start, end-1] 里，越界一律夹紧而不是报错——段长由实测语音决定，
短到 1.2s 的过渡句也必须能出两张图。

为什么不取 start / end 本身：start 那帧往往还是上一段的尾巴（转场中点），
end 那帧已经压在下一段的第一帧上，都不代表"这一段长什么样"。

## 二、输出音轨偏移（audio out offset）

ffmpeg 的 AAC 编码器会在音轨前面留 priming 帧，mux 出来的 mp4 里音频相对
视频墙钟会有一个**系统性**偏移（48 kHz、AAC 常见 ~1.28 帧 @30fps）。它不大，
但"音画同步"门禁量的是毫秒级——不补这一项，整片会稳定地早/晚一点点，
然后被误判成 cues 打错。

这里只负责**算**：offset_seconds -> 采样点。真正怎么用（把总线整体后移/前移）
由调用方决定，本模块不碰 numpy。
"""
from __future__ import annotations

DEFAULT_OUT_OFFSET_FRAMES = 0.0    # 默认不补偿：不改既有成片的音频，回归才不漂

# 两个帧都离段边界多远（秒）。数值是经验值：进入 0.55s 处主体动作已过半，
# 落定留 0.25s 让缓动收尾完成、同时避开下一段的第一帧。
ENTER_SEC = 0.55
SETTLE_BACK_SEC = 0.25
ENTER_FRAC = 0.35          # 段太短时改用"段长的比例"，但要被下面两个硬夹钳住
SETTLE_FRAC = 0.75


def _clamp(v, lo, hi):
    if hi < lo:
        hi = lo
    return lo if v < lo else (hi if v > hi else v)


def review_frames_for_scene(start, end, fps, index=0):
    """单段 -> {enter, settle, enter_s, settle_s}（帧号 int、秒保留 3 位）。

    fps <= 0 时抛 ValueError：调用方一定是从 timeline 里拿的，拿到 0 说明上游坏了，
    静默按 30 会算出一套和成片对不上的帧号（比报错糟得多）。
    """
    fps = int(fps or 0)
    if fps <= 0:
        raise ValueError("fps 必须是正整数，收到 %r" % (fps,))
    s = float(start)
    e = float(end)
    if e <= s:                                  # 0 长段：两张图钉在同一帧，不崩
        e = s + 1.0 / fps
    dur = e - s
    enter_s = s + min(ENTER_SEC, max(0.0, dur * ENTER_FRAC))
    settle_s = e - min(SETTLE_BACK_SEC, max(0.0, dur * (1.0 - SETTLE_FRAC)))
    if settle_s <= enter_s:                     # 极短段：至少隔一帧
        settle_s = enter_s + 1.0 / fps
    last = int(max(s, e - 1.0 / fps) * fps)
    enter_f = int(_clamp(int(round(enter_s * fps)), int(s * fps), last))
    settle_f = int(_clamp(int(round(settle_s * fps)), enter_f, last))
    return {
        "index": int(index),
        "enter": enter_f,
        "settle": settle_f,
        "enter_s": round(enter_f / float(fps), 3),
        "settle_s": round(settle_f / float(fps), 3),
    }


def review_marks(timeline):
    """整条 timeline -> review 块（写进 timeline["review"]）。

    只依赖 scenes[].start/end 与 fps，因此可以在 layout 末尾无条件调用：
    旧 timeline（没有 review 块）照样能跑，重新 layout 一次就补上。
    """
    fps = int((timeline or {}).get("fps") or 30)
    scenes = list((timeline or {}).get("scenes") or [])
    marks = []
    for i, sc in enumerate(scenes):
        m = review_frames_for_scene(sc.get("start", 0.0), sc.get("end", 0.0), fps, i)
        m["id"] = sc.get("id")
        marks.append(m)
    return {
        "fps": fps,
        "enter_sec": ENTER_SEC,
        "settle_back_sec": SETTLE_BACK_SEC,
        "marks": marks,
        # 评审时要"每段两张"：这里先算总数，评审包拿它核对有没有漏段
        "expect_frames": len(marks) * 2,
    }


def marks_for_scenes(scenes, fps):
    """便利入口：给评包用（不进 timeline 也能量）。"""
    return review_marks({"fps": fps, "scenes": scenes})


# ---------------------------------------------------------------- 音轨偏移
def offset_seconds_from_script(script, fps=None):
    """从 film.json 读输出偏移；三种写法都认，MV 缺省 0（不改既有行为）。

    1) mux.audio_offset_ms   数字（毫秒，最直白）
    2) mux.audio_offset_frames  数字（帧）
    3) audio.out_offset_frames  数字（帧，旧口径）
    读到不认识的值一律报错（不静默当 0）：这个参数的坏值会让整片音画错位，
    "以为在补偿，其实没补"是门禁里最难受的一类。
    """
    err = []
    ms = frames = None
    mux = script.get("mux") if isinstance(script.get("mux"), dict) else {}
    aud = script.get("audio") if isinstance(script.get("audio"), dict) else {}
    for key, box, conv in (
            ("audio_offset_ms", mux, "ms"),
            ("audio_offset_frames", mux, "f"),
            ("out_offset_frames", aud, "f")):
        if box.get(key) is None:
            continue
        try:
            val = float(box[key])
        except (TypeError, ValueError):
            err.append("%s 必须是数字，收到 %r" % (key, box[key]))
            continue
        if conv == "ms":
            ms = val
        else:
            frames = val
    if err:
        raise SystemExit("[audio] film.json 偏移写错：" + "；".join(err))
    if ms is not None and frames is not None:
        raise SystemExit("[audio] mux.audio_offset_ms 与 audio_offset_frames 只能写一个")
    if ms is not None:
        return ms / 1000.0
    if frames is not None:
        f = int(fps or 30)
        return float(frames) / float(f)
    return 0.0


def offset_samples(seconds, sr):
    """秒 -> 采样点。四舍五入到整数，避免半采样点的两套口径。"""
    try:
        secs = float(seconds or 0.0)
    except (TypeError, ValueError):
        secs = 0.0
    return int(round(secs * float(sr)))


def shift_signal(x, samples):
    """把一条总线整体**推迟** samples 个采样点（末尾截断，长度不变）。

    为什么推迟而不是提前：mux 之后音频比视频"晚听到"时才需要把它提前；
    但提前要补零到开头会吃掉开头的声音。实际做法交给调用方按探针实测符号定，
    这里只提供一个方向明确、可单测的基础操作。

    samples == 0 原样返回**同一个对象**（不复制）：默认路径零开销，
    大数组不会被无意多拷一份。
    """
    n = int(samples or 0)
    if n == 0 or x is None:
        return x
    total = len(x)
    if abs(n) >= total:
        import numpy as np
        return np.zeros_like(x)
    import numpy as np
    out = np.zeros_like(x)
    if n > 0:                                   # 推迟：前面补 n 个 0
        out[n:] = x[:total - n]
    else:                                       # 提前：掐掉开头 |n| 个采样
        out[:total + n] = x[-n:]
    return out
