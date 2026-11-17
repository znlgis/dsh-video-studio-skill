# -*- coding: utf-8 -*-
"""layoutkit.py — 画布与安全区的**单一真源**。

为什么单独一个模块
------------------
这些数字以前散落在 `filmkit` 顶部三个常量里（`W, H` / `SAFE` / `SUB_BAND`），
而 `assets/scenes.py`、`preflight`、`qa_frames` 各自 `from filmkit import ...` 复制一份值。
于是"改画布尺寸"不是改一个数，而是**找到所有复制过它的地方**；而"竖屏"恰好就是
改画布尺寸——所以它一直没被做，理由写成"安全区/版式按横屏像素写死"。

这里做的第一件事很小：**把这三个常量收成一份**，默认 profile 的数值与原来逐字节相同。
验收口径就是 `golden_frames --check` 三帧像素一致——不是"看起来一样"。

刻意没做的事（见 references/portrait-and-motion.md 的第三步）
--------------------------------------------------------
* **没有**把 `W/H` 做成运行时可切换：`scenes.py` 用 `from filmkit import W, H`
  在导入期就把值绑死了，要真正支持竖屏得让构图代码接受 profile 参数，而不是改全局。
  在这里假装"已经支持竖屏"只会让后面的人以为几何已经适配好了。
* 10 类构图的**内部比例常量**仍是按 16:9 写的（`W*0.5`、`W-150` 这类）。
  竖屏不是把它们乘以一个系数就对了——`list` 在竖屏该纵向堆叠、`compare` 该改上下对照。

本模块只依赖标准库：任何脚本（含 preflight / qa）都可以安全 import 它。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 横屏 16:9（默认，唯一在用）
# 这些数值**不要单独改**：改画布要连 SAFE / SUB_BAND 一起重算，并且必须重录金帧。
LANDSCAPE = {
    "key": "landscape-16x9",
    "w": 1920,
    "h": 1080,
    # 正文安全区：左右各 64、上 58、下 106（留出字幕带）
    "safe": (64, 58, 1856, 974),
    # 字幕带：两行 + 距画面底部 50px，永不被裁
    "sub_band": (180, 872, 1740, 1030),
    # 单行字幕中文字数上限（超过就折行，折行会改变排版本，属于版式契约）
    "sub_max_chars": 24,
}

# 当前生效的 profile。**运行期不要改它**：改动必须发生在任何 `import scenes` 之前，
# 否则会得到"用 A 尺寸排版、用 B 尺寸绘制"的半新半旧状态（比报错更难查）。
# 这里保留这个名字，是为了让"将来加第二套 profile"有一个明确的落点。
PROFILE = LANDSCAPE

W = int(PROFILE["w"])
H = int(PROFILE["h"])
SAFE = tuple(PROFILE["safe"])
SUB_BAND = tuple(PROFILE["sub_band"])
SUB_MAX_CHARS = int(PROFILE["sub_max_chars"])


def profile(key=None):
    """取一份 profile 的**副本**。

    给副本而不是给本体：调用方改自己的局部变量不该污染全局（`PROFILE` 是指针，
    直接把它交出去等于把单一真源变成"谁都能改的共享状态"）。
    """
    src = PROFILE if key is None else _BY_KEY.get(str(key))
    if src is None:
        raise KeyError("未知 layout profile %r（可用：%s）" % (key, ", ".join(sorted(_BY_KEY))))
    return dict(src)


_BY_KEY = {p["key"]: p for p in (LANDSCAPE,)}


def verify_defaults():
    """自检用：把"本模块导出的值"与"历史常量"逐项比对。

    历史常量写死在这里而不是从 filmkit 读——如果从 filmkit 读，两边一起改就永远
    对得上，这条断言也就永远绿（这正是 selfcheck 第 25 条踩过的坑：拿同一个对象比自己）。
    """
    want = {
        "w": 1920, "h": 1080,
        "safe": (64, 58, 1856, 974),
        "sub_band": (180, 872, 1740, 1030),
        "sub_max_chars": 24,
    }
    got = {"w": W, "h": H, "safe": SAFE, "sub_band": SUB_BAND,
           "sub_max_chars": SUB_MAX_CHARS}
    bad = [k for k in want if got[k] != want[k]]
    if bad:
        raise AssertionError("; ".join("%s: %r != %r" % (k, got[k], want[k]) for k in bad))
    return "landscape-16x9: %dx%d / safe %s / band %s" % (W, H, SAFE, SUB_BAND)
