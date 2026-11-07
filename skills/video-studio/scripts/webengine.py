# -*- coding: utf-8 -*-
"""webengine.py — 浏览器逐帧渲染引擎（v0.3.0）。

用无头浏览器（优先 Microsoft Edge，失败回落 Playwright Chromium）打开 Canvas 页面，
把时间精确跳到「帧号 / FPS」后经 CDP 截 PNG，喂给 ffmpeg 的 image2pipe 编成视频。
分块并行 + 断点续跑 + 显式缺口检测 + 崩溃重开；同一帧号渲染出的画面完全一致。

算法改编自 ai-history-film（MIT）video/render.py（逐帧 seek / 分块并行 / 崩溃重开 / 断点续跑），
代码按本项目 v0.3.0 契约重写；拼接复用 filmkit.concat_segments（绝对路径，v0.2 修过的坑）。

设计要点：
1. 逐帧渲染是无状态的：崩溃后重开浏览器从同一帧继续即可，不需要回滚。
2. 分块按「绝对起始帧 // 每块帧数」命名，重跑时按文件存在性跳过；拼接前显式查缺口，
   缺块直接 raise，不允许「少一段也出片」。
3. 先写 xxx.part.mp4，ffmpeg 正常退出后再 rename 成 xxx.mp4：中断只会留下 .part 垃圾，
   不会让半截文件被误认成已完成的块。
4. 失败要报错，不要静默降级：页面没 ready、DURATION 对不上、块时长异常都直接抛异常。

CLI：
    python webengine.py snap <html> --times 3,9.5 ...     # 截静帧 + 联系表
    python webengine.py cues <html> [--out x.json]        # 导出 cue / 视觉事件（§4.2 schema）
    python webengine.py full <html> --out x.mp4 [--workers 2] [--resume]
    python webengine.py textqa <html> [--from 0 --to 12 --step 0.2]
    python webengine.py selftest                          # 等价于 --selftest
"""
from __future__ import annotations

__version__ = "0.3.0"

import argparse
import asyncio
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------- 常量（冻结契约 §5.3）
FPS = 30                  # 默认 30，可被页面 window.FPS 覆盖
W, H = 1920, 1080
FRAME_TIMEOUT = 90.0      # 单帧截图超时（秒），超时视同崩溃 → 重开浏览器
RETRY = 5                 # 崩溃重开重试次数
PART_SUFFIX = ".part.mp4"

_SCRIPT_DIR = Path(__file__).resolve().parent
_ASSET_DIR = _SCRIPT_DIR.parent / "assets" / "web"
DEFAULT_HTML = _ASSET_DIR / "index.html"

# 浏览器启动参数（同 ai-history-film；GPU/ANGLE、无滚动条、静音、禁后台节流、允许 file:// 读取）
BROWSER_ARGS = [
    "--use-angle=d3d11",
    "--enable-gpu",
    "--ignore-gpu-blocklist",
    "--enable-unsafe-swiftshader",
    "--allow-file-access-from-files",
    "--disable-web-security",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_BROWSER_KIND = ""       # 最近一次 open_page 实际用上的浏览器（msedge / chromium），只用于日志与自检


def _setup_stdout() -> None:
    """管道输出时统一 UTF-8（控制台保持系统编码），避免中文写进日志/CI 后乱码。"""
    try:
        enc = (sys.stdout.encoding or "").lower()
        if not sys.stdout.isatty() and enc not in ("utf-8", "utf8", "cp65001"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _load_filmkit():
    """只读复用 filmkit：ffmpeg 定位 / 联系表 / 绝对路径 concat（绝不修改 filmkit）。"""
    try:
        import filmkit  # type: ignore
        return filmkit
    except Exception:
        pass
    here = str(_SCRIPT_DIR)
    if here not in sys.path:
        sys.path.insert(0, here)
    import filmkit  # type: ignore
    return filmkit


_K = _load_filmkit()
FFMPEG = _K.FFMPEG


# ---------------------------------------------------------------- 页面 / URL
def _to_url(html_path) -> str:
    """把本地 HTML 路径转成 file:// URI；已经是 URL 的原样返回。"""
    s = str(html_path)
    if _SCHEME_RE.match(s):
        return s
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    if not p.is_file():
        raise FileNotFoundError("页面不存在: %s" % p)
    return p.as_uri()


def _playwright():
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - 环境缺依赖时给出可操作提示
        raise RuntimeError(
            "需要 playwright：pip install playwright 且 python -m playwright install chromium"
        ) from exc
    return async_playwright


def _console_log(msg) -> None:
    try:
        if msg.type in ("error", "warning"):
            print("[页面控制台] %s: %s" % (msg.type, msg.text), flush=True)
    except Exception:
        pass


async def open_page(pw, url, width=W, height=1080, log=False):
    """打开页面并等到 window.ready === true，返回 (browser, page, cdp)。

    浏览器优先 Microsoft Edge（channel="msedge"），启动失败回落 Playwright 自带的 Chromium；
    视口固定 1920x1080 / devicePixelRatio=1，截图与画布 1:1 像素。
    """
    global _BROWSER_KIND
    browser = None
    args = list(BROWSER_ARGS)
    try:
        browser = await pw.chromium.launch(channel="msedge", headless=True, args=args)
        _BROWSER_KIND = "msedge"
        if log:
            print("[浏览器] Microsoft Edge（channel=msedge）", flush=True)
    except Exception as exc:
        if log:
            print("[浏览器] Edge 启动失败（%s），回落到 Playwright Chromium" % type(exc).__name__, flush=True)
        browser = await pw.chromium.launch(headless=True, args=args)
        _BROWSER_KIND = "chromium"
        if log:
            print("[浏览器] Playwright Chromium", flush=True)
    page = await browser.new_page(viewport={"width": int(width), "height": int(height)}, device_scale_factor=1)
    if log:
        page.on("console", _console_log)
        page.on("pageerror", lambda exc: print("[页面异常] %s" % exc, flush=True))
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=180_000)
        await page.wait_for_function("window.ready === true", timeout=300_000, polling=250)
    except Exception:
        try:
            await browser.close()
        except Exception:
            pass
        raise
    cdp = await page.context.new_cdp_session(page)
    return browser, page, cdp


async def _grab(page, cdp, f: int) -> bytes:
    """把时间轴精确跳到第 f 帧并截图（PNG）。"""
    await page.evaluate("f => window.renderFrame(f)", int(f))
    reply = await cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
    return base64.b64decode(reply["data"])


async def _page_meta_async(html_path):
    url = _to_url(html_path)
    async with _playwright()() as pw:
        browser, page, _cdp = await open_page(pw, url)
        try:
            duration = float(await page.evaluate("window.DURATION"))
            fps = float(await page.evaluate("window.FPS || 30"))
            return duration, fps
        finally:
            try:
                await browser.close()
            except Exception:
                pass


def page_meta(html_path):
    """打开一次页面，读回 (DURATION, FPS)。"""
    _setup_stdout()
    return asyncio.run(_page_meta_async(html_path))


# ---------------------------------------------------------------- snap
def snap(html_path, times, out_dir, tag="") -> list:
    """截取指定时刻的静帧（PNG）；多帧时额外拼一张联系表。返回帧 PNG 路径列表。"""
    _setup_stdout()
    tlist = [float(t) for t in times]
    if not tlist:
        raise ValueError("snap 至少需要 1 个时刻（times 为空）")
    out = Path(out_dir).expanduser()
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_snap_async(html_path, tlist, out, str(tag)))


async def _snap_async(html_path, times, out: Path, tag: str) -> list:
    url = _to_url(html_path)
    paths = []
    async with _playwright()() as pw:
        browser, page, cdp = await open_page(pw, url, W, H)
        try:
            fps = float(await page.evaluate("window.FPS || 30"))
            for t in times:
                png = await _grab(page, cdp, int(round(t * fps)))
                path = out / ("%st%07.2f.png" % (tag, t))
                path.write_bytes(png)
                paths.append(path)
                print("[snap] %6.2fs → %s" % (t, path), flush=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    if len(paths) >= 2:
        sheet = out / ("%ssheet_%.1f-%.1f.png" % (tag, times[0], times[-1]))
        _K.contact_sheet([str(p) for p in paths], str(sheet), cols=4, thumb_w=480)
        print("[联系表] %s" % sheet.resolve(), flush=True)
    return paths


# ---------------------------------------------------------------- cues / events 导出
def _norm_cue_list(raw, src: str) -> list:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        cue = dict(item)
        if "t" not in cue:
            raise ValueError("cue 缺 t 字段: %r" % (item,))
        if not cue.get("type"):
            raise ValueError("cue 缺 type 字段: %r" % (item,))
        cue["t"] = round(float(cue["t"]), 3)
        cue.setdefault("src", src)
        out.append(cue)
    out.sort(key=lambda c: c["t"])
    return out


def _norm_events(raw) -> list:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        ev = dict(item)
        if "t" not in ev:
            continue
        ev["t"] = round(float(ev["t"]), 3)
        ev.setdefault("kind", "misc")
        out.append(ev)
    out.sort(key=lambda e: e["t"])
    return out


def export_cues(html_path) -> dict:
    """导出页面登记的 cue / 视觉事件，返回可直接写 work/cues.json 的字典（契约 §4.2）。"""
    _setup_stdout()
    return asyncio.run(_export_cues_async(html_path))


async def _export_cues_async(html_path) -> dict:
    url = _to_url(html_path)
    async with _playwright()() as pw:
        browser, page, _cdp = await open_page(pw, url, W, H)
        try:
            duration = float(await page.evaluate("window.DURATION"))
            fps = float(await page.evaluate("window.FPS || 30"))
            cues = await page.evaluate("window.getCues ? window.getCues() : []")
            events = await page.evaluate("window.getEvents ? window.getEvents() : []")
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    return {
        "version": 1,
        "duration": round(duration, 3),
        "fps": fps,
        "width": W,
        "height": H,
        "cues": _norm_cue_list(cues, "web"),
        "events": _norm_events(events),
    }


# ---------------------------------------------------------------- 分块 / 编码
def _encoder_cmd(dst, fps: float, crf: int = 17, preset: str = "slow") -> list:
    """image2pipe(PNG) → libx264；显式 -r FPS、-g 2*FPS（关键帧密集，便于逐段检视）。"""
    return [
        str(FFMPEG), "-y", "-loglevel", "error",
        "-f", "image2pipe", "-c:v", "png", "-framerate", str(fps), "-i", "-",
        "-c:v", "libx264", "-preset", str(preset), "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(int(round(2 * fps))),
        "-bf", "2", "-threads", "2", "-movflags", "+faststart",
        str(dst),
    ]


def _chunk_dir(out_mp4, from_s: float, to_s: float) -> Path:
    out = Path(out_mp4)
    return out.parent / "chunks" / ("%s_%g-%g" % (out.stem, float(from_s), float(to_s)))


def _plan_chunks(chunk_dir, f0: int, f1: int, step: int) -> list:
    """按「绝对起始帧 // 每块帧数」命名块：c000.mp4 = 第 0 帧起、c001.mp4 = 第 step 帧起……"""
    step = max(1, int(step))
    chunks = []
    for a in range(int(f0), int(f1), step):
        b = min(int(f1), a + step)
        chunks.append((a, b, Path(chunk_dir) / ("c%03d.mp4" % (a // step))))
    return chunks


def _check_gaps(chunks) -> None:
    """拼接前显式缺口检测：缺块直接 raise，拒绝「少一段也出片」。"""
    missing = [Path(p).name for _a, _b, p in chunks if not Path(p).is_file()]
    if missing:
        shown = "、".join(missing[:8]) + ("…" if len(missing) > 8 else "")
        raise RuntimeError(
            "分块缺口：缺 %d 个块（%s）。拒绝拼出少一段的片子；重跑 --resume 补齐，"
            "或删掉块目录整段重渲。" % (len(missing), shown)
        )


def _probe_media(path):
    try:
        return _K.probe(str(path))
    except Exception as exc:
        return {"_error": "%s: %s" % (type(exc).__name__, exc)}


def _probe_duration(path):
    info = _probe_media(path)
    try:
        d = info.get("format", {}).get("duration")
        return float(d) if d is not None else None
    except Exception:
        return None


def _check_chunk_durations(chunks, fps: float) -> None:
    """块时长校验：块损坏 / 旧参数残留都会在拼接前被拦下。"""
    bad = []
    for f0, f1, path in chunks:
        got = _probe_duration(path)
        if got is None:
            continue
        want = (f1 - f0) / float(fps)
        if abs(got - want) > max(0.15, 1.5 / float(fps)):
            bad.append("%s 实际 %.3fs / 期望 %.3fs" % (Path(path).name, got, want))
    if bad:
        raise RuntimeError("分块时长异常（块损坏或旧参数渲染）：" + "；".join(bad[:6]))


# ---------------------------------------------------------------- 浏览器会话（崩溃重开）
class _Browser:
    """一个无头浏览器会话；卡住/崩溃时自动关掉重开（逐帧无状态，从同一帧继续即可）。"""

    def __init__(self, pw, url, width=W, height=1080, k=0, log=False):
        self.pw = pw
        self.url = url
        self.width = width
        self.height = height
        self.k = k
        self.log = log
        self.browser = None
        self.page = None
        self.cdp = None

    async def open(self):
        await self.close()
        self.browser, self.page, self.cdp = await open_page(
            self.pw, self.url, self.width, self.height, log=self.log
        )

    async def close(self):
        if self.browser is not None:
            try:
                await asyncio.wait_for(self.browser.close(), 20)
            except Exception:
                pass
        self.browser = None
        self.page = None
        self.cdp = None

    async def frame(self, f: int) -> bytes:
        last = None
        for attempt in range(RETRY):
            try:
                if self.browser is None:
                    await self.open()
                return await asyncio.wait_for(_grab(self.page, self.cdp, f), FRAME_TIMEOUT)
            except Exception as exc:
                last = exc
                print(
                    "  worker %d: 第 %d 帧失败（%s: %s），重开浏览器重试 %d/%d"
                    % (self.k, f, type(exc).__name__, str(exc)[:120], attempt + 1, RETRY),
                    flush=True,
                )
                await self.close()
        raise RuntimeError("第 %d 帧连续失败 %d 次，放弃：%s" % (f, RETRY, last))


async def _write_chunk(br: _Browser, f0: int, f1: int, dst: Path, fps: float,
                       crf: int, preset: str, k: int, state: dict) -> None:
    """渲染一块：逐帧截图 → ffmpeg image2pipe → xxx.part.mp4 → rename xxx.mp4。"""
    dst = Path(dst)
    part = dst.with_name(dst.name[: -len(".mp4")] + PART_SUFFIX)
    if part.exists():
        part.unlink()
    proc = subprocess.Popen(
        _encoder_cmd(part, fps, crf, preset),
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for f in range(f0, f1):
            proc.stdin.write(await br.frame(f))
        proc.stdin.close()
        rc = proc.wait(timeout=300)
        err = (proc.stderr.read() or b"").decode("utf-8", "replace")
    except BaseException:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        if part.exists():
            try:
                part.unlink()
            except Exception:
                pass
        raise
    finally:
        try:
            proc.stderr.close()
        except Exception:
            pass
    if rc != 0:
        if part.exists():
            part.unlink()
        raise RuntimeError("ffmpeg 编码失败（%s，rc=%d）：%s" % (dst.name, rc, err[:600]))
    part.replace(dst)
    state["done"] += f1 - f0
    elapsed = time.perf_counter() - state["t0"]
    ms = elapsed / max(state["done"], 1) * 1000.0
    print(
        "  worker %d: 完成 %s 总进度 %d/%d (%.0f ms/帧)"
        % (k, dst.name, state["done"], state["total"], ms),
        flush=True,
    )


async def _worker(k: int, queue, state: dict, url: str, fps: float, crf: int, preset: str) -> None:
    async with _playwright()() as pw:
        br = _Browser(pw, url, W, H, k=k)
        try:
            while True:
                try:
                    f0, f1, dst = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await _write_chunk(br, f0, f1, dst, fps, crf, preset, k, state)
        finally:
            await br.close()


# ---------------------------------------------------------------- render_full
def render_full(html_path, out_mp4, duration, *, workers=2, chunk_s=30.0,
                resume=True, from_s=0.0, to_s=None, crf=17, preset="slow") -> Path:
    """并行渲染整片（或一段）→ mp4。返回成片绝对路径。

    duration 传 None 时读页面 window.DURATION；传数字时与页面对不上会直接 raise。
    """
    _setup_stdout()
    return asyncio.run(_render_full_async(
        html_path, out_mp4, duration, workers=workers, chunk_s=chunk_s, resume=resume,
        from_s=from_s, to_s=to_s, crf=crf, preset=preset,
    ))


async def _render_full_async(html_path, out_mp4, duration, *, workers=2, chunk_s=30.0,
                             resume=True, from_s=0.0, to_s=None, crf=17, preset="slow") -> Path:
    url = _to_url(html_path)
    out = Path(out_mp4).expanduser()
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    # 先问页面要长度与帧率：这是块切分与成片校验的唯一真值来源
    page_dur, fps = await _page_meta_async(url)
    if duration is None:
        duration = page_dur
    duration = float(duration)
    if abs(duration - page_dur) > 0.05:
        raise ValueError(
            "页面 window.DURATION=%.3f 与传入 duration=%.3f 不一致（先对齐剧本时长）"
            % (page_dur, duration)
        )
    from_s = float(from_s)
    to_s = duration if to_s is None else float(to_s)
    if from_s < 0 or to_s <= from_s or to_s > duration + 1e-6:
        raise ValueError("渲染区间非法：from_s=%.3f to_s=%.3f duration=%.3f" % (from_s, to_s, duration))

    f0 = int(round(from_s * fps))
    f1 = int(round(to_s * fps))
    step = max(1, int(round(float(chunk_s) * fps)))
    chunk_dir = _chunk_dir(out, from_s, to_s)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunks = _plan_chunks(chunk_dir, f0, f1, step)
    if not resume:
        for _a, _b, path in chunks:
            for victim in (path, path.with_name(path.name[: -len(".mp4")] + PART_SUFFIX)):
                if victim.exists():
                    victim.unlink()

    todo = [c for c in chunks if not Path(c[2]).is_file()]
    print(
        "[full] %s\n[full] %s fps，渲染 %.2f–%.2f s（%d 帧），块 %.1fs → %d 块，work=%s"
        % (url, ("%g" % fps), from_s, to_s, f1 - f0, float(chunk_s), len(chunks), chunk_dir),
        flush=True,
    )
    if todo:
        print("[full] 待渲染 %d 块（%.0f 帧），workers=%d" % (len(todo), sum(b - a for a, b, _p in todo), max(1, int(workers))), flush=True)
        queue: asyncio.Queue = asyncio.Queue()
        for item in todo:
            queue.put_nowait(item)
        state = {"done": 0, "total": sum(b - a for a, b, _p in todo), "t0": time.perf_counter()}
        nw = max(1, min(int(workers), len(todo)))
        tasks = [
            asyncio.create_task(_worker(k, queue, state, url, fps, int(crf), str(preset)))
            for k in range(nw)
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        elapsed = time.perf_counter() - state["t0"]
        print("[full] 全部块渲染完成，用时 %.1fs" % elapsed, flush=True)
    else:
        print("[full] 断点续跑：%d 个块已全部存在，跳过渲染" % len(chunks), flush=True)

    # 拼接前两道门：块必须齐、块时长必须对
    _check_gaps(chunks)
    _check_chunk_durations(chunks, fps)
    parts = [Path(p) for _a, _b, p in chunks]
    print("[full] 分块齐备（%d 块），开始无损拼接 → %s" % (len(parts), out), flush=True)
    _K.concat_segments([str(p) for p in parts], str(out), workdir=str(chunk_dir))

    info = _probe_media(out)
    got = info.get("format", {}).get("duration") if isinstance(info, dict) else None
    if got is not None:
        print("[full] 成片时长 %.3fs → %s" % (float(got), out), flush=True)
    return out


# ---------------------------------------------------------------- 浏览器端文字质检
def qa_text(html_path, t0=0.0, t1=None, step=0.2) -> dict:
    """浏览器端文字质检：重叠 / 出画 / 一闪而过 / 从没露面（渲染期间会临时改页面状态）。"""
    _setup_stdout()
    return asyncio.run(_qa_text_async(html_path, t0, t1, step))


async def _qa_text_async(html_path, t0, t1, step) -> dict:
    url = _to_url(html_path)
    async with _playwright()() as pw:
        browser, page, _cdp = await open_page(pw, url, W, H)
        try:
            if t1 is None:
                t1 = float(await page.evaluate("window.DURATION"))
            result = await page.evaluate(
                "([a, b, c]) => window.qaText(a, b, c)", [float(t0), float(t1), float(step)]
            )
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    if not isinstance(result, dict):
        raise RuntimeError("页面 qaText 返回值不是对象：%r" % (result,))
    return result


# ---------------------------------------------------------------- 自检辅助
def _png_stats(data: bytes) -> dict:
    from io import BytesIO
    from PIL import Image

    im = Image.open(BytesIO(data)).convert("L")
    hist = im.histogram()
    total = float(sum(hist)) or 1.0
    mean = sum(i * count for i, count in enumerate(hist)) / total
    top = 0
    for i in range(255, -1, -1):
        if hist[i]:
            top = i
            break
    return {"mean": round(mean, 2), "max": top, "w": im.width, "h": im.height}


def _encode_pngs(pngs, dst, fps: float, crf: int = 17, preset: str = "slow") -> Path:
    """把一组 PNG 字节直接喂 ffmpeg（自检里验证 image2pipe 链路）。"""
    proc = subprocess.Popen(
        _encoder_cmd(dst, fps, crf, preset), stdin=subprocess.PIPE, stderr=subprocess.PIPE
    )
    _out, err = proc.communicate(input=b"".join(pngs), timeout=300)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 编码失败：%s" % err.decode("utf-8", "replace")[:500])
    return Path(dst)


async def _selftest_probe() -> dict:
    """一次浏览器会话内采集自检需要的全部证据（页面只加载一遍 + 末尾重载验证零外链）。"""
    url = _to_url(DEFAULT_HTML)
    out = {"fps": FPS, "duration": 0.0, "browser": ""}
    async with _playwright()() as pw:
        browser, page, cdp = await open_page(pw, url, W, H, log=True)
        try:
            out["browser"] = _BROWSER_KIND
            out["duration"] = float(await page.evaluate("window.DURATION"))
            out["fps"] = float(await page.evaluate("window.FPS || 30"))
            out["ready"] = bool(await page.evaluate("window.ready === true"))

            first = await _grab(page, cdp, 45)
            second = await _grab(page, cdp, 45)
            out["same_bytes"] = first == second
            out["same_len"] = len(first)
            out["same_sha"] = hashlib.sha256(first).hexdigest()[:16]
            other = await _grab(page, cdp, 46)
            out["diff_frames"] = first != other

            pngs = {}
            stats = {}
            for f in (20, 160, 330):
                data = await _grab(page, cdp, f)
                pngs[f] = data
                stats[f] = _png_stats(data)
            out["pngs"] = pngs
            out["frame_stats"] = stats

            seq = []
            for i in range(12):
                seq.append(await _grab(page, cdp, 40 + i))
            out["seq"] = seq

            out["cues"] = await page.evaluate("window.getCues()")
            out["events"] = await page.evaluate("window.getEvents()")
            out["qa"] = await page.evaluate(
                "([a, b, c]) => window.qaText(a, b, c)", [0.0, out["duration"], 0.5]
            )

            # 零外部网络：重载一次，记录页面发出的全部请求
            requests = []
            page.on("request", lambda req: requests.append(req.url))
            await page.reload(wait_until="domcontentloaded", timeout=120_000)
            await page.wait_for_function("window.ready === true", timeout=120_000, polling=100)
            out["requests"] = requests
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    return out


# ---------------------------------------------------------------- 自检
def selftest() -> int:
    """不联网自检：页面契约、确定性（同帧截图 byte 相同）、cue 递增、文字质检、编码链路。"""
    _setup_stdout()
    t_start = time.perf_counter()
    results = []

    def check(title: str, ok: bool, detail: str = "") -> None:
        ok = bool(ok)
        results.append(ok)
        print(("[PASS] " if ok else "[FAIL] ") + title + (("  —— " + detail) if detail else ""), flush=True)

    print("=" * 78)
    print("webengine.py --selftest（v0.3.0）：无头浏览器逐帧渲染 / 确定性 / cue / 文字质检")
    print("=" * 78)

    names = ("index.html", "engine.js", "film.js", "style.css", "qa_text.js")
    missing = [n for n in names if not (_ASSET_DIR / n).is_file()]
    check("assets/web/ 五个文件齐全", not missing,
          str(_ASSET_DIR) if not missing else "缺 " + "、".join(missing))

    net_pat = re.compile(r"https?://|//(?:cdn|unpkg|jsdelivr)|fonts\.googleapis")
    net_bad = []
    for name in names:
        path = _ASSET_DIR / name
        if not path.is_file():
            continue
        hit = net_pat.search(path.read_text(encoding="utf-8", errors="replace"))
        if hit:
            net_bad.append("%s 含 %s" % (name, hit.group(0)))
    check("页面零网络依赖（不连 CDN / 无外链）", not net_bad,
          "只引用同目录本地文件" if not net_bad else "；".join(net_bad[:4]))

    # 纯函数级检查（不开浏览器）
    tmp_root = Path(tempfile.mkdtemp(prefix="webengine_self_"))
    try:
        chunk_dir = tmp_root / "chunks"
        chunks = _plan_chunks(chunk_dir, 0, 900, 900)
        names_ok = [Path(p).name for _a, _b, p in chunks] == ["c000.mp4"]
        check("分块按绝对起始帧命名（c{帧//step:03d}.mp4）", names_ok,
              "、".join(Path(p).name for _a, _b, p in chunks))

        gap_raised = False
        gap_detail = "缺块时抛 RuntimeError"
        try:
            _check_gaps(chunks)
            gap_detail = "缺块没有报错"
        except RuntimeError as exc:
            gap_raised = "缺口" in str(exc)
            gap_detail = "缺块时抛 RuntimeError" if gap_raised else str(exc)[:120]
        except Exception as exc:
            gap_detail = "%s: %s" % (type(exc).__name__, exc)
        check("拼接前显式缺口检测（缺块 raise）", gap_raised, gap_detail)

        chunk_dir.mkdir(parents=True, exist_ok=True)
        for _a, _b, path in chunks:
            path.write_bytes(b"")
        no_gap = True
        try:
            _check_gaps(chunks)
        except Exception:
            no_gap = False
        check("块齐备时不误报缺口", no_gap)

        cmd = _encoder_cmd("probe.mp4", 30, 17, "slow")
        enc_ok = "-r" in cmd and cmd[cmd.index("-r") + 1] == "30" \
            and "-g" in cmd and cmd[cmd.index("-g") + 1] == "60" \
            and "libx264" in cmd
        check("编码参数显式 -r FPS / -g 2*FPS / libx264", enc_ok,
              "fps=30 → -r 30 -g 60")

        # 崩溃重开：用桩替身模拟「前两帧失败、第三次成功」，验证重开重试逻辑（不依赖真实崩溃）
        reopen_ok = False
        reopen_detail = ""
        real_grab = globals()["_grab"]
        try:
            attempts = {"n": 0, "opens": 0, "closes": 0}

            class _StubBrowser(object):
                browser = None
                page = None
                cdp = None
                k = 0

                async def open(self):
                    attempts["opens"] += 1

                async def close(self):
                    attempts["closes"] += 1

            stub = _StubBrowser()
            stub.frame = _Browser.frame.__get__(stub, _StubBrowser)

            async def _flaky_grab(page, cdp, f):
                attempts["n"] += 1
                if attempts["n"] <= 2:
                    raise RuntimeError("模拟页面崩溃")
                return b"png-bytes"

            globals()["_grab"] = _flaky_grab
            got = asyncio.run(stub.frame(7))
            reopen_ok = (got == b"png-bytes") and attempts["n"] == 3 and attempts["closes"] == 2
            reopen_detail = "失败 2 次后第 3 次成功（重开浏览器 %d 次）" % attempts["opens"]
        except Exception as exc:
            reopen_detail = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        finally:
            globals()["_grab"] = real_grab
        check("单帧失败自动重开浏览器重试（崩溃重开）", reopen_ok, reopen_detail)

        probe = {}
        try:
            probe = asyncio.run(_selftest_probe())
        except Exception as exc:
            probe = {"_error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
        perr = probe.get("_error")

        check("启动无头浏览器并等到 window.ready === true",
              bool(probe.get("ready")) and not perr,
              perr or "浏览器=%s，DURATION=%ss，FPS=%s"
              % (probe.get("browser", "?"), probe.get("duration"), probe.get("fps")))

        same = bool(probe.get("same_bytes"))
        check("同一帧两次截图 byte 完全相同", same,
              ("f=45 两次均 %d bytes，sha256=%s…" % (probe.get("same_len", 0), probe.get("same_sha", "")))
              if same else (perr or "两次截图字节不同（页面存在非确定性）"))

        stats = probe.get("frame_stats") or {}
        dark = []
        detail = []
        for f in sorted(stats):
            st = stats[f]
            if not (st["mean"] > 6.0 and st["max"] > 60):
                dark.append(str(f))
            detail.append("f=%d mean=%.1f max=%d" % (f, st["mean"], st["max"]))
        check("三帧非纯黑", (not dark) and len(stats) == 3,
              "；".join(detail) if not perr else perr)

        check("不同帧渲染结果不同", bool(probe.get("diff_frames")),
              "f=45 与 f=46 的 PNG 不同" if probe.get("diff_frames") else (perr or "相邻帧截图相同（时间码没变？）"))

        cues = probe.get("cues") or []
        ts = [float(c.get("t", -1)) for c in cues]
        monotonic = len(ts) >= 2 and all(b > a for a, b in zip(ts, ts[1:]))
        types_ok = all(isinstance(c.get("type"), str) and c.get("type") for c in cues)
        check("getCues() 非空且时间严格递增", bool(cues) and monotonic and types_ok,
              "%d 个 cue，t = %s" % (len(cues), ", ".join("%g" % t for t in ts[:8])) if cues else (perr or "空列表"))

        events = probe.get("events") or []
        ev_mono = all(float(b.get("t", -1)) >= float(a.get("t", -1)) for a, b in zip(events, events[1:]))
        check("getEvents() 非空且时间递增", bool(events) and ev_mono,
              "%d 个视觉事件" % len(events) if events else (perr or "空列表"))

        qa = probe.get("qa") or {}
        qa_keys = all(k in qa for k in ("overlaps", "offscreen", "brief", "never"))
        qa_clean = qa_keys and not qa.get("overlaps") and not qa.get("offscreen")
        check("qaText() 跑通且模板页无重叠 / 出画", qa_clean,
              perr or "重叠 %d、出画 %d、一闪而过 %d、从没露面 %d"
              % (len(qa.get("overlaps") or []), len(qa.get("offscreen") or []),
                 len(qa.get("brief") or []), len(qa.get("never") or [])))

        reqs = probe.get("requests") or []
        external = [u for u in reqs if not u.startswith(("file:", "data:", "blob:", "about:"))]
        check("页面加载零外部网络请求", (not external) and not perr,
              perr or ("%d 个请求全部本地（file://）" % len(reqs) if not external else "外链：" + "、".join(external[:3])))

        enc_ok2 = False
        enc_detail = perr or "无截图数据"
        if probe.get("seq"):
            try:
                fps_now = float(probe.get("fps") or FPS)
                mp4 = tmp_root / "selftest_12f.mp4"
                _encode_pngs(probe["seq"], str(mp4), fps_now)
                info = _probe_media(mp4)
                streams = [s for s in (info.get("streams") or []) if s.get("codec_type") == "video"]
                vs = streams[0] if streams else {}
                nbf = int(vs.get("nb_frames") or 0)
                dur = float(info.get("format", {}).get("duration") or 0.0)
                enc_ok2 = (vs.get("width") == W and vs.get("height") == H and nbf == 12
                           and abs(dur - 12.0 / fps_now) < 0.2)
                enc_detail = "%dx%d、%d 帧、%.3fs、%s fps" % (
                    vs.get("width"), vs.get("height"), nbf, dur, vs.get("r_frame_rate"))
            except Exception as exc:
                enc_detail = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        check("ffmpeg image2pipe(PNG) → libx264 编码烟测（12 帧）", enc_ok2, enc_detail)

        sheet_ok = False
        sheet_detail = perr or "无截图数据"
        if probe.get("pngs"):
            try:
                pngs = probe["pngs"]
                files = []
                for f in sorted(pngs):
                    p = tmp_root / ("f%03d.png" % f)
                    p.write_bytes(pngs[f])
                    files.append(str(p))
                sheet = tmp_root / "sheet.png"
                _K.contact_sheet(files, str(sheet), cols=2, thumb_w=480)
                sheet_ok = sheet.is_file() and sheet.stat().st_size > 1000
                sheet_detail = "%d bytes" % (sheet.stat().st_size if sheet.is_file() else 0)
            except Exception as exc:
                sheet_detail = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        check("联系表（filmkit.contact_sheet）能生成", sheet_ok, sheet_detail)

        check("页面声明的 DURATION / FPS 有效",
              float(probe.get("duration") or 0) > 0 and float(probe.get("fps") or 0) > 0,
              "DURATION=%s，FPS=%s" % (probe.get("duration"), probe.get("fps")))
    finally:
        shutil.rmtree(str(tmp_root), ignore_errors=True)

    n_pass = sum(1 for ok in results if ok)
    n_fail = len(results) - n_pass
    elapsed = time.perf_counter() - t_start
    print("-" * 78)
    print("自检结果：%d 项，PASS %d，FAIL %d，耗时 %.1fs" % (len(results), n_pass, n_fail, elapsed))
    if n_fail == 0:
        print("结论：全部通过（同一帧两次截图 byte 级一致，浏览器逐帧渲染可复现）")
    else:
        print("结论：有失败项，见上方 [FAIL]")
    return 0 if n_fail == 0 else 1


# ---------------------------------------------------------------- CLI
def _parse_times(text: str) -> list:
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        if part:
            out.append(float(part))
    return out


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="webengine.py",
        description="浏览器逐帧渲染引擎（v0.3.0）：Canvas 页面 → CDP 截图 → ffmpeg → mp4",
    )
    ap.add_argument("--selftest", action="store_true", help="跑自检（不联网，≤60 秒）")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("snap", help="截取指定时刻的静帧并拼联系表")
    s.add_argument("html", nargs="?", default=str(DEFAULT_HTML), help="页面路径（缺省用 assets/web/index.html）")
    s.add_argument("--times", default="", help="逗号分隔的秒数，如 3,9.5,13")
    s.add_argument("--every", type=float, help="等间隔采样（秒）")
    s.add_argument("--from", dest="t_from", type=float, default=0.0)
    s.add_argument("--to", dest="t_to", type=float)
    s.add_argument("--out", default="", help="输出目录（缺省 ./snaps）")
    s.add_argument("--tag", default="", help="文件名前缀")

    c = sub.add_parser("cues", help="导出 cue / 视觉事件（契约 §4.2 schema）")
    c.add_argument("html", nargs="?", default=str(DEFAULT_HTML))
    c.add_argument("--out", default="", help="写到 JSON 文件；缺省打到标准输出")

    f = sub.add_parser("full", help="并行渲染整片（分块 + 断点续跑 + 缺口检测）")
    f.add_argument("html", nargs="?", default=str(DEFAULT_HTML))
    f.add_argument("--out", required=True, help="输出 mp4 路径")
    f.add_argument("--duration", type=float, default=None, help="成片秒数（缺省读页面 DURATION）")
    f.add_argument("--workers", type=int, default=2, help="并行浏览器数（默认 2）")
    f.add_argument("--chunk-s", type=float, default=30.0, help="每块秒数（默认 30）")
    f.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                   help="断点续跑：已存在的块跳过（--no-resume 强制重渲）")
    f.add_argument("--from", dest="t_from", type=float, default=0.0, help="起始秒（预览片段用）")
    f.add_argument("--to", dest="t_to", type=float, default=None, help="结束秒（缺省到片尾）")
    f.add_argument("--crf", type=int, default=17)
    f.add_argument("--preset", default="slow")

    q = sub.add_parser("textqa", help="浏览器端文字质检（重叠 / 出画 / 一闪而过 / 从没露面）")
    q.add_argument("html", nargs="?", default=str(DEFAULT_HTML))
    q.add_argument("--from", dest="t0", type=float, default=0.0)
    q.add_argument("--to", dest="t1", type=float, default=None)
    q.add_argument("--step", type=float, default=0.2)
    q.add_argument("--out", default="", help="把完整结果写到 JSON")

    sub.add_parser("selftest", help="跑自检（等价于 --selftest）")
    return ap


def _json_dump(data, path: str) -> Path:
    dst = Path(path).expanduser()
    if not dst.is_absolute():
        dst = (Path.cwd() / dst).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return dst


def main(argv=None) -> int:
    _setup_stdout()
    args = _build_parser().parse_args(argv)
    if args.selftest or args.cmd == "selftest":
        return selftest()
    if args.cmd is None:
        _build_parser().print_help()
        return 2

    if args.cmd == "snap":
        times = _parse_times(args.times)
        if args.every:
            end = args.t_to if args.t_to is not None else page_meta(args.html)[0]
            k = float(args.t_from)
            while k <= end + 1e-9:
                times.append(round(k, 3))
                k += float(args.every)
        if not times:
            print("[错误] snap 需要 --times 3,9.5 或 --every 2 [--to 12]", flush=True)
            return 2
        out_dir = args.out or str(Path.cwd() / "snaps")
        snap(args.html, times, out_dir, tag=args.tag)
        return 0

    if args.cmd == "cues":
        data = export_cues(args.html)
        if args.out:
            dst = _json_dump(data, args.out)
            print("[cues] %d 个 cue、%d 个视觉事件 → %s"
                  % (len(data["cues"]), len(data["events"]), dst), flush=True)
        else:
            print(json.dumps(data, ensure_ascii=False, indent=1), flush=True)
        return 0

    if args.cmd == "full":
        out = render_full(
            args.html, args.out, args.duration,
            workers=args.workers, chunk_s=args.chunk_s, resume=args.resume,
            from_s=args.t_from, to_s=args.t_to, crf=args.crf, preset=args.preset,
        )
        print("[done] %s" % out, flush=True)
        return 0

    if args.cmd == "textqa":
        result = qa_text(args.html, args.t0, args.t1, args.step)
        print("[textqa] 重叠 %d、出画 %d、一闪而过 %d、从没露面 %d"
              % (len(result.get("overlaps") or []), len(result.get("offscreen") or []),
                 len(result.get("brief") or []), len(result.get("never") or [])), flush=True)
        for item in (result.get("overlaps") or [])[:5]:
            print("  重叠 %.2f-%.2fs ratio=%.2f  %s" % (item["at"], item["to"], item["ratio"], item["pair"]), flush=True)
        for item in (result.get("offscreen") or [])[:5]:
            print("  出画 %.2f-%.2fs  %s  %s" % (item["at"], item["to"], item["el"], item["box"]), flush=True)
        for item in (result.get("never") or [])[:8]:
            print("  从没露面：%s" % item, flush=True)
        if args.out:
            print("[textqa] 详情 → %s" % _json_dump(result, args.out), flush=True)
        bad = bool(result.get("overlaps")) or bool(result.get("offscreen"))
        return 1 if bad else 0

    _build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
