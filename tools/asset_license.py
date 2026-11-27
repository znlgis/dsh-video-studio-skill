#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""asset_license.py — 外部素材授权台账：把「这个音效/BGM 从哪来、能不能商用、怎么署名」钉死。

为什么需要它：sfxkit 是现场合成的，天然没有授权问题；但真实项目常还要用外部素材
（Freesound 的环境音、素材站的 BGM、别人给的配乐）。这些素材的风险不在「好不好听」，
而在**交付之后才被发现**：不知道出处、不知道授权范围、不知道该不该署名。
片子已经发出去，再撤回改片尾不是改代码能解决的。

所以这里做三件事：
  1. 登记：每个外部素材必须写清来源/作者/授权（缺项直接 fail，不给「以后补」）；
  2. 查商用：按授权标识判能否商用、要不要署名；不认识的就 fail，不默认放行；
  3. 出署名：按片子实际用到的素材生成片尾 CREDITS 与 markdown 台账。

口径与 F5（素材溯源）一致：**没出处就不能交付**。区别是 F5 查素材线 sources，
这里查音频（sfx/bgm）——两处都查才完整。

用法：
    python tools/asset_license.py --film film.json --check
    python tools/asset_license.py --film film.json --credits out/CREDITS.txt
    python tools/asset_license.py --film film.json --json

证照写在 film.json 的 assets 块：

    "assets": {"audio": [{"path": "assets/sfx/rain.wav", "title": "Rain",
        "author": "someone", "source": "https://freesound.org/s/123456/",
        "license": "CC-BY-4.0"}]}

license 用 SPDX 风格短名（CC0-1.0 / CC-BY-4.0 / CC-BY-NC-4.0 / owned …）。
不认识的就写 other:<说明>，此时必须显式写 commercial，否则按不可交付处理。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 授权 -> (能否商用, 是否必须署名)
LICENSES = {
    "CC0-1.0": (True, False),
    "CC-BY-4.0": (True, True),
    "CC-BY-3.0": (True, True),
    "CC-BY-SA-4.0": (True, True),
    "CC-BY-NC-4.0": (False, True),
    "CC-BY-NC-SA-4.0": (False, True),
    "CC-BY-ND-4.0": (True, True),
    "MIT": (True, False),
    "Apache-2.0": (True, False),
    "public-domain": (True, False),
    "owned": (True, False),
    "commissioned": (True, False),
    "youtube-audio-library": (True, False),
}

NONCOMMERCIAL = {k for k, (c, _s) in LICENSES.items() if not c}
REQUIRED = ("path", "title", "author", "source", "license")


def norm_license(raw):
    """规范化授权标识（大小写/空格/下划线差异都归一）。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.lower().startswith("other:"):
        return "other:" + s[len("other:"):].strip()
    compact = s.lower().replace(" ", "-").replace("_", "-")
    for k in LICENSES:
        if s.lower() == k.lower() or compact == k.lower():
            return k
    return s


def check_entry(entry, idx=0):
    """一条素材登记 -> (errors, warnings)。"""
    errors, warns = [], []
    if not isinstance(entry, dict):
        return ["#%d 不是对象（要 dict）" % idx], []
    for k in REQUIRED:
        if not str(entry.get(k) or "").strip():
            errors.append("#%d 缺 %s" % (idx, k))
    lic = norm_license(entry.get("license"))
    if lic.startswith("other:"):
        if entry.get("commercial") is None:
            errors.append("#%d 授权是 other:<说明>，必须同时写 commercial: true/false" % idx)
        else:
            warns.append("#%d 自定义授权 %s（按声明 commercial=%s 处理）"
                         % (idx, lic, entry.get("commercial")))
    elif lic and lic not in LICENSES:
        errors.append("#%d 不认识的授权 %r（可用：%s；自定义写 other:<说明>）"
                      % (idx, entry.get("license"), "、".join(sorted(LICENSES))))
    elif lic in NONCOMMERCIAL and entry.get("commercial") is True:
        errors.append("#%d 授权 %s 不允许商用，但登记写了 commercial=true" % (idx, lic))
    src = str(entry.get("source") or "")
    if src and not (src.startswith("http://") or src.startswith("https://")):
        warns.append("#%d source 不是链接（%r）：离线素材写路径或说明也行"
                     % (idx, src[:40]))
    return errors, warns


def needs_credit(entry):
    lic = norm_license(entry.get("license"))
    if lic in LICENSES:
        return bool(LICENSES[lic][1]) or bool(entry.get("credit_required"))
    return bool(entry.get("credit_required", True))


def can_commercial(entry):
    lic = norm_license(entry.get("license"))
    if lic in LICENSES:
        return bool(LICENSES[lic][0])
    return bool(entry.get("commercial"))


def check_film(film, base_dir=""):
    """整个 film 的音频素材台账。没写 assets.audio 视为没有外部素材（pass）。

    base_dir：film.json 所在目录。素材路径按它解析——**不能**按 cwd 解析：
    剧本里写 `assets/sfx/rain.wav` 是相对工程目录的，而从仓库根跑工具是常态，
    按 cwd 解析会把每条都误报成「文件不存在」。
    """
    assets = (film or {}).get("assets") or {}
    audio = assets.get("audio") or []
    if not isinstance(audio, list):
        return dict(ok=False, errors=["assets.audio 必须是数组"], warnings=[],
                    entries=[], needs_credit=[], files=[])
    errors, warns, ok_entries, creds, files = [], [], [], [], []
    for i, e in enumerate(audio):
        errs, w2 = check_entry(e, i)
        errors += errs
        warns += w2
        if errs:
            continue
        ok_entries.append(e)
        p = str(e.get("path") or "")
        if p:
            files.append(p)
            real = p if os.path.isabs(p) else os.path.join(str(base_dir or ""), p)
            if not os.path.exists(real):
                warns.append("#%d 文件不存在：%s（登记了但没落地，交付前必须补齐）" % (i, p))
        if needs_credit(e):
            creds.append(e)
    return dict(ok=not errors, errors=errors, warnings=warns, entries=ok_entries,
                needs_credit=creds, files=files)


def credits_text(result, film=None):
    """片尾署名纯文本（一行一条：片尾卡放得下，也便于人工核对）。"""
    rows = result.get("needs_credit") or []
    if not rows:
        return ""
    title = str((film or {}).get("title") or "").strip()
    out = []
    out.append((title + " · 素材署名") if title else "素材署名")
    out.append("")
    for e in rows:
        out.append("%s — %s（%s）" % (str(e.get("title") or "").strip(),
                                      str(e.get("author") or "").strip(),
                                      norm_license(e.get("license"))))
        src = str(e.get("source") or "").strip()
        if src:
            out.append("  " + src)
    return "\n".join(out)


def ledger_markdown(result, film=None):
    rows = result.get("entries") or []
    out = []
    out.append("# 外部音频素材台账")
    out.append("")
    out.append("> 机器生成（tools/asset_license.py）。每年至少复核一次授权：CC 条款会变，站点也会下架。")
    out.append("")
    if not rows:
        out.append("本片没有登记外部音频素材（全部由 sfxkit / musicgen 现场合成）。")
        return "\n".join(out)
    out.append("| 标题 | 作者 | 授权 | 可商用 | 要署名 | 来源 |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for e in rows:
        out.append("| %s | %s | %s | %s | %s | %s |" % (
            str(e.get("title") or ""), str(e.get("author") or ""),
            norm_license(e.get("license")),
            "是" if can_commercial(e) else "否",
            "是" if needs_credit(e) else "否",
            str(e.get("source") or "")))
    if result.get("warnings"):
        out.append("")
        out.append("## 待处理")
        out.append("")
        for w in result["warnings"]:
            out.append("- " + w)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="外部音频素材授权台账（检查 + 生成署名）")
    ap.add_argument("--film", required=True, help="film.json 路径")
    ap.add_argument("--check", action="store_true", help="只检查（不可交付时 exit 1）")
    ap.add_argument("--credits", default="", help="署名文本写到这个文件")
    ap.add_argument("--ledger", default="", help="台账 markdown 写到这个文件")
    ap.add_argument("--json", action="store_true", help="机器可读结果")
    a = ap.parse_args(argv)
    try:
        with open(a.film, encoding="utf-8") as fh:
            film = json.load(fh)
    except Exception as e:                          # noqa: BLE001
        print("[失败] film.json 读不了：%s" % e)
        return 2
    res = check_film(film, base_dir=os.path.dirname(os.path.abspath(a.film)))
    if a.json:
        print(json.dumps(dict(ok=res["ok"], errors=res["errors"],
                              warnings=res["warnings"], n=len(res["entries"]),
                              needs_credit=[e.get("title") for e in res["needs_credit"]]),
                         ensure_ascii=False))
    else:
        print("外部音频素材: %d 条" % len(res["entries"]))
        for w in res["warnings"]:
            print("  [WARN] " + w)
        if res["errors"]:
            for e in res["errors"]:
                print("  [FAIL] " + e)
        else:
            print("  授权检查通过（需署名 %d 条）" % len(res["needs_credit"]))
    if a.credits:
        txt = credits_text(res, film)
        d = os.path.dirname(os.path.abspath(a.credits))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.credits, "w", encoding="utf-8") as fh:
            fh.write(txt)
        print("署名文本:", a.credits, "(%d 字)" % len(txt))
    if a.ledger:
        md = ledger_markdown(res, film)
        d = os.path.dirname(os.path.abspath(a.ledger))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.ledger, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("台账:", a.ledger)
    if not res["ok"]:
        print("[结论] 不可交付：外部素材授权有缺项（与 F5 同口径：没出处不交付）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
