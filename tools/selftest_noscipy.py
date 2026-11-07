# -*- coding: utf-8 -*-
r"""selftest_noscipy.py — 在"没有 scipy"的环境里跑可选模块的自检。

为什么需要它：scipy 在 requirements.txt 里是**可选**依赖，没装 scipy 的用户会走
"分块 FFT 卷积"兜底路径——那是他们真正运行的代码，必须在 CI 里被测到。
本地开发机装了 scipy，所以要用这个脚本把 scipy 屏蔽掉再跑。

为什么用子进程：每个模块都在干净解释器里跑，避免"上一个模块已经把 scipy 用上了"
这种互相污染；同时能保证 sys.modules['scipy'] = None 在 import 之前就生效。

用法：
    python tools/selftest_noscipy.py            # 默认 sfxkit scorekit
    python tools/selftest_noscipy.py sfxkit     # 只跑指定模块

退出码：任一模块自检失败即返回 1。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "video-studio" / "scripts"

# 子进程里先屏蔽 scipy，再 import 目标模块并跑它的 selftest()。
RUNNER = r"""
import sys
sys.modules["scipy"] = None          # 模拟"没装 scipy"：import scipy 抛 ImportError
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
mod = __import__(name)

# 自证屏蔽真的生效：sfxkit 模块级 _sig 必须是 None（它是全仓库唯一的 scipy 入口）。
sfx = getattr(mod, "S", None) if name != "sfxkit" else mod
sig = getattr(sfx, "_sig", None) if sfx is not None else None
if name in ("sfxkit", "scorekit") and sig is not None:
    print("屏蔽失败：%s 仍然拿到了 scipy" % name, file=sys.stderr)
    raise SystemExit(2)

raise SystemExit(mod.selftest())
"""


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    modules = args or ["sfxkit", "scorekit"]
    bad = []
    for name in modules:
        print("=" * 70)
        print("[无 scipy 兜底路径] %s --selftest" % name)
        print("=" * 70, flush=True)
        proc = subprocess.run(
            [sys.executable, "-c", RUNNER, str(SCRIPTS), name],
            cwd=str(SCRIPTS),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if proc.returncode != 0:
            bad.append("%s (exit %d)" % (name, proc.returncode))
    print()
    if bad:
        print("无 scipy 兜底路径自检失败：%s" % ", ".join(bad))
        return 1
    print("无 scipy 兜底路径自检全部通过：%s" % ", ".join(modules))
    return 0


if __name__ == "__main__":
    sys.exit(main())
