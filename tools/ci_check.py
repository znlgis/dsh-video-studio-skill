# -*- coding: utf-8 -*-
"""ci_check.py — 仓库级体检（不需要 ffmpeg / 字体 / 网络，可在任何机器和 CI 上跑）。

跑法：python tools/ci_check.py [-v]

它检查的是**这个仓库作为"可分发的技能插件"的完整性**，而不是视频能不能出片
（出片要靠 selfcheck.py + 真跑一遍 render.py，见 SKILL.md §14）：

  1  语法：所有 .py 能编译、index.js 能通过 node --check
  2  JSON：所有 .json 能被解析（locale / marketplace-entry / assets / package.json）
  3  技能清单：SKILL.md 的 frontmatter 有 name/description，name 与目录名一致
  4  依赖白名单：scripts/*.py 与 assets/*.py 只允许 stdlib + numpy/PIL/cv2/scipy/edge_tts/playwright
     （技能是"整目录拷走就能跑"的，偷偷引入一个三方依赖会在用户机器上炸）
  5  打包卫生：npm pack 的内容里不能有 __pycache__ / *.pyc / 临时目录
  6  文档引用：SKILL.md / references 里引用的 scripts/xxx.py、assets/xxx 必须真的存在
  7  Python 3.9 语法兼容（THIRD_PARTY_NOTICES 写的是 3.9+；这里只验语法，运行期 API 仍要真机实测）
  8  Markdown 围栏成对（不平衡会让整篇渲染成代码块）
  9  GitHub Actions workflow 里 step 名的裸冒号（会让整份 workflow 静默不跑）
  10  体积：单个文件不超过 400 KB（技能要能塞进上下文与包里）

退出码：0 全过；1 有 FAIL。每一项都打印证据，不打"可能有问题"。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "video-studio"
SCRIPTS = SKILL / "scripts"
ASSETS = SKILL / "assets"
REFS = SKILL / "references"

# 技能允许的三方依赖（与 README「环境前提」一一对应；加东西必须先改文档）
ALLOWED_THIRD_PARTY = {"numpy", "PIL", "cv2", "scipy", "edge_tts", "playwright"}
# 同目录同级模块（拷走整个目录后仍能互相 import）
LOCAL_MODULES = {"filmkit", "sfxkit", "scorekit", "webengine", "qa_frames", "render", "selfcheck",
                 "stylekit", "voicekit", "scenes", "scenes_demo",
                 # v0.6.0：门禁账本 / 交付报告 / 签核、阶段新鲜度守卫、film.json 契约校验器
                 "gates", "stagewatch", "filmcheck"}
MAX_FILE_BYTES = 400 * 1024

_results: list[tuple[bool, str, str]] = []


def check(name: str, fn):
    """跑一项检查；返回 (ok, name, detail)。任何异常都算 FAIL 并保留消息。"""
    try:
        detail = fn() or ""
        _results.append((True, name, str(detail)))
        return True
    except AssertionError as e:
        _results.append((False, name, str(e)))
        return False
    except Exception as e:                                      # noqa: BLE001
        _results.append((False, name, "%s: %s" % (type(e).__name__, e)))
        return False


# ---------------------------------------------------------------- 1 语法
def _py_files() -> list[Path]:
    out = sorted(SCRIPTS.glob("*.py")) + sorted(ASSETS.glob("*.py"))
    return [p for p in out if p.name != "__init__.py"]


def t_syntax_py():
    bad = []
    for p in _py_files():
        src = p.read_text(encoding="utf-8")
        try:
            compile(src, str(p), "exec")
        except SyntaxError as e:
            bad.append("%s:%s %s" % (p.name, e.lineno, e.msg))
    assert not bad, "；".join(bad)
    return "%d 个 .py 全部可编译" % len(_py_files())


def t_syntax_js():
    node = _which("node")
    if node is None:
        return "跳过（没装 node）"
    js = [p for p in ROOT.glob("*.js")] + sorted((ASSETS / "web").glob("*.js"))
    for p in js:
        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, "%s: %s" % (p.name, (r.stderr or r.stdout).strip()[:300])
    return "%d 个 .js 全部通过 node --check" % len(js)


def _which(exe: str):
    from shutil import which
    return which(exe)


# ---------------------------------------------------------------- 2 JSON
def t_json():
    files = [ROOT / "package.json", ROOT / "marketplace-entry.json"]
    files += sorted((ROOT / "locale").glob("*.json"))
    files += sorted(ASSETS.glob("*.json"))
    for p in files:
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                                  # noqa: BLE001
            raise AssertionError("%s: %s" % (p.relative_to(ROOT), e))
    return "%d 个 .json 全部可解析" % len(files)


# ---------------------------------------------------------------- 3 技能清单
def t_manifest():
    md = SKILL / "SKILL.md"
    assert md.is_file(), "缺 skills/video-studio/SKILL.md"
    txt = md.read_text(encoding="utf-8")
    assert txt.startswith("---"), "SKILL.md 必须以 YAML frontmatter 开头"
    end = txt.find("\n---", 3)
    assert end > 0, "SKILL.md frontmatter 没有闭合的 ---"
    fm = txt[3:end]
    name = re.search(r"^name:\s*(\S+)\s*$", fm, re.M)
    desc = re.search(r"^description:\s*(.+)$", fm, re.M)
    assert name, "frontmatter 缺 name"
    assert desc and len(desc.group(1).strip()) >= 20, "frontmatter 缺 description 或太短"
    assert name.group(1) == "video-studio", "frontmatter name=%r，应为目录名 video-studio" % name.group(1)
    return "name=video-studio / description %d 字" % len(desc.group(1).strip())


# ---------------------------------------------------------------- 4 依赖白名单
def t_deps():
    stdlib = set(sys.stdlib_module_names)
    bad = []
    for p in _py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if top in stdlib or top in ALLOWED_THIRD_PARTY or top in LOCAL_MODULES:
                    continue
                bad.append("%s: import %s（不在白名单）" % (p.name, top))
    assert not bad, "；".join(sorted(set(bad)))
    return "白名单：stdlib + %s" % "/".join(sorted(ALLOWED_THIRD_PARTY))


# ---------------------------------------------------------------- 5 打包卫生
def t_pack():
    npm = _which("npm")
    if npm is None:
        return "跳过（没装 npm）"
    r = subprocess.run([npm, "pack", "--dry-run", "--json"], cwd=str(ROOT),
                       capture_output=True, text=True, shell=(os.name == "nt"))
    assert r.returncode == 0, "npm pack --dry-run 失败：%s" % (r.stderr or r.stdout)[:300]
    try:
        meta = json.loads(r.stdout)[0]
    except Exception as e:                                      # noqa: BLE001
        raise AssertionError("解析 npm pack 输出失败：%s" % e)
    files = [f["path"] for f in meta.get("files", [])]
    # 垃圾文件可能出现在**包内任意位置**（实测踩过：队友的临时目录建在 skills/ 下，
    # 于是 skills/tmp_scratch_x/webtest/*.mp4 被一起打进包，体量翻倍）。
    junk_suffix = (".pyc", ".pyo", ".log", ".tmp", ".orig", ".bak", ".mp4", ".wav", ".mkv", ".tgz")
    junk_parts = ("__pycache__", "tmp_scratch_", "node_modules", ".pytest_cache", ".cordis")
    junk = [f for f in files
            if f.endswith(junk_suffix) or any(p in f for p in junk_parts)
            or f.startswith(("work/", "out/", ".team/"))]
    assert not junk, ("包内不该有这些文件（临时产物/日志/媒体，属于打包污染）：%s"
                      % ", ".join(junk[:6]))
    must = ["index.js", "cordis.patch.yml", "icon.svg", "package.json",
            "skills/video-studio/SKILL.md", "skills/video-studio/scripts/filmkit.py"]
    missing = [m for m in must if m not in files]
    assert not missing, "包内缺文件：%s" % ", ".join(missing)
    return "%d 个文件 / %.0f KB" % (len(files), meta.get("size", 0) / 1024.0)


# ---------------------------------------------------------------- 6 文档引用
def t_doc_refs():
    md_files = [SKILL / "SKILL.md", ROOT / "README.md"] + sorted(REFS.glob("*.md"))
    missing = []
    for md in md_files:
        if not md.is_file():
            continue
        txt = md.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:scripts|assets|references)/[A-Za-z0-9_.\-/]+", txt):
            rel = m.group(0).rstrip(".,;:）)")
            # 文档里的相对路径有多种写法（<SKILL_DIR>/scripts/x.py、references/y.md），两种都试
            cands = [SKILL / rel, SKILL / "references" / rel, ROOT / rel]
            if not any(c.exists() for c in cands):
                missing.append("%s 里引用 %s（不存在）" % (md.name, rel))
    assert not missing, "；".join(sorted(set(missing))[:8])
    return "%d 篇文档的路径引用全部存在" % sum(1 for m in md_files if m.is_file())


# ---------------------------------------------------------------- 7 体积
def t_size():
    big = []
    for p in [p for p in ROOT.rglob("*") if p.is_file()
              and ".git" not in p.parts and "node_modules" not in p.parts
              and not str(p.relative_to(ROOT)).startswith("tmp_scratch_")
              and p.suffix != ".png"]:
        n = p.stat().st_size
        if n > MAX_FILE_BYTES:
            big.append("%s %.0f KB" % (p.relative_to(ROOT), n / 1024.0))
    assert not big, "；".join(big)
    return "所有文本文件 < %.0f KB" % (MAX_FILE_BYTES / 1024.0)



# ---------------------------------------------------------------- 8 兼容 Python 3.9（README/THIRD_PARTY 的承诺）
def t_py39():
    bad = []
    for p in _py_files():
        src = p.read_text(encoding="utf-8")
        try:
            ast.parse(src, filename=str(p), feature_version=(3, 9))
        except SyntaxError as e:
            bad.append("%s:%s %s" % (p.name, e.lineno, e.msg))
    assert not bad, "；".join(bad)
    return "%d 个 .py 通过 3.9 语法解析（运行期兼容仍需真 3.9 实测）" % len(_py_files())



# ---------------------------------------------------------------- 9 Markdown 围栏平衡
def t_md_fences():
    """每篇 .md 的 \`\`\` 必须成对：围栏不平衡会让后面整篇渲染成代码块，人眼未必看得出。"""
    bad = []
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "THIRD_PARTY_NOTICES.md",
             SKILL / "SKILL.md"] + sorted(REFS.glob("*.md"))
    for p in files:
        if not p.is_file():
            continue
        n = sum(1 for line in p.read_text(encoding="utf-8").splitlines()
                if line.lstrip().startswith("\`\`\`"))
        if n % 2:
            bad.append("%s: %d 个围栏（奇数）" % (p.name, n))
    assert not bad, "；".join(bad)
    return "%d 篇 Markdown 围栏都成对" % len([p for p in files if p.is_file()])


# ---------------------------------------------------------------- 10 CI workflow 基本语法
def t_ci_workflow():
    """GitHub Actions workflow 的「裸冒号」陷阱。

    「- name: selftest: xxx」里第二个冒号会让整份 YAML 解析失败，而 GitHub 上的表现是
    「workflow 根本没跑」——没有任何报错提示，最容易被忽略（本项目真踩过）。
    不引第三方 YAML 库（CI 里不一定装了 pyyaml），只查这一类高频错误。
    """
    wf = ROOT / ".github" / "workflows"
    if not wf.is_dir():
        return "跳过（没有 .github/workflows）"
    bad = []
    files = sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml"))
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^(\s*)-\s+name:\s*(.+)$", line)
            if not m:
                continue
            val = m.group(2).strip()
            if val[:1] in ("'", '"'):
                continue
            if ": " in val:
                bad.append("%s:%d 的 step 名含未加引号的冒号（YAML 会解析失败，整份 workflow 不跑）"
                           % (f.name, i))
    assert not bad, "；".join(bad[:4])
    return "%d 个 workflow 的 step 名没有裸冒号" % len(files)

# ---------------------------------------------------------------- 12 film.json 契约
def t_film_contract():
    """用技能自带的 filmcheck.py 校验模板，并把 schema 与脚本对齐成回归。

    为什么要进 CI：film.json 是唯一内容真源，而未知键以前是**静默忽略**的
    （hold 写成 holds、kind 拼错、cue 的 at 指向不存在的句子，全都"跑得通但效果没了"）。
    这里不重写校验逻辑，直接调技能里的 filmcheck.py——校验规则只允许有一份。
    """
    import subprocess
    scripts = ROOT / "skills" / "video-studio" / "scripts"
    film = ROOT / "skills" / "video-studio" / "assets" / "film.json"
    schema = ROOT / "schemas" / "film.schema.json"
    assert schema.exists(), "缺 schemas/film.schema.json（film.json 的契约）"
    try:
        json.loads(schema.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AssertionError("schemas/film.schema.json 不是合法 JSON：%s" % e) from e
    checker = scripts / "filmcheck.py"
    if not checker.exists():
        raise AssertionError("缺 skills/video-studio/scripts/filmcheck.py")
    p = subprocess.run([sys.executable, str(checker), str(film)],
                       cwd=str(scripts), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 0, ("模板 film.json 没过契约校验（exit=%d）：\n%s"
                               % (p.returncode, out.strip()[-600:]))
    return "模板 film.json 通过契约校验；schema %d 字节" % len(schema.read_text(encoding="utf-8"))

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="仓库级体检（不需要 ffmpeg / 字体 / 网络）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每一节的详细证据")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

    checks = [
        ("1  Python 语法", t_syntax_py),
        ("2  JS 语法", t_syntax_js),
        ("3  JSON 可解析", t_json),
        ("4  技能清单 frontmatter", t_manifest),
        ("5  三方依赖白名单", t_deps),
        ("6  打包卫生（npm pack）", t_pack),
        ("7  文档路径引用", t_doc_refs),
        ("8  Python 3.9 语法兼容", t_py39),
        ("9  Markdown 围栏平衡", t_md_fences),
        ("10 CI workflow 语法", t_ci_workflow),
        ("11 单文件体积", t_size),
        ("12 film.json 契约", t_film_contract),
    ]
    print("ci_check  %s" % ROOT)
    print("-" * 66)
    for name, fn in checks:
        ok = check(name, fn)
    for ok, name, detail in _results:
        print("  %s %-24s %s" % ("PASS" if ok else "FAIL", name, detail if (ok or a.verbose) else ""))
    fails = [r for r in _results if not r[0]]
    print("-" * 66)
    print("PASS %d / FAIL %d" % (len(_results) - len(fails), len(fails)))
    if fails:
        print("\n失败项：")
        for ok, name, detail in fails:
            print("  - %s：%s" % (name, detail))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())