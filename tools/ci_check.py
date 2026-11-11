# -*- coding: utf-8 -*-
"""ci_check.py — 仓库级体检（不需要 ffmpeg / 字体 / 网络，可在任何机器和 CI 上跑）。

跑法：python tools/ci_check.py [-v]

它检查的是**这个仓库作为"可分发的技能插件"的完整性**，而不是视频能不能出片
（出片要靠 selfcheck.py + 真跑一遍 render.py，见 SKILL.md §14）：

  1  语法：scripts/ + assets/ + tools/ 下的 .py 全部能编译（SyntaxWarning 也算失败）
  2  JS 语法：根目录与 assets/web/ 的 .js 通过 node --check
  3  JSON：所有 .json 能被解析（locale / marketplace-entry / assets / package.json），
     且 package.json 的 version 与 README 徽章一致
  4  技能清单：SKILL.md 的 frontmatter 有 name/description，name 与目录名一致
  5  依赖白名单：scripts/*.py 与 assets/*.py 只允许 stdlib + numpy/PIL/cv2/scipy/edge_tts/playwright
     （技能是"整目录拷走就能跑"的，偷偷引入一个三方依赖会在用户机器上炸）
  6  打包卫生：npm pack 的内容里不能有 __pycache__ / *.pyc / 临时目录；必需文件必须在包里
  7  文档路径：SKILL.md / references / README 里引用的 scripts/xxx.py、assets/xxx，
     以及正文里的相对 Markdown 链接与图片，必须真的存在
  8  Python 3.9 语法兼容（THIRD_PARTY_NOTICES 写的是 3.9+；这里只验语法，运行期 API 仍要真机实测）
  9  Markdown 围栏成对（不平衡会让整篇渲染成代码块；按围栏状态机判，不是数出现次数）
  10  CI workflow：step 名的裸冒号（会让整份 workflow 静默不跑），
     以及 run 里 python <路径>.py 指向的文件必须存在
  11  体积：单个文件不超过 400 KB（技能要能塞进上下文与包里）
  12  film.json 契约：schemas/film.schema.json + scripts/filmcheck.py 校验模板（规则只留一份）

三档结论（与技能自己的门禁账本同口径）：PASS = 这次真的查了且通过；FAIL = 查了没过；
SKIP = **这次没执行**（比如本地没装 node / npm），**不等于通过**。

退出码：0 = 无 FAIL；1 = 有 FAIL。加 --strict 时 SKIP 也算失败（本地环境缺工具时用它自证"真的全查了"）。
每一项都打印证据，不打"可能有问题"。
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
TOOLS = ROOT / "tools"
REFS = SKILL / "references"

# 技能允许的三方依赖（与 README「环境前提」一一对应；加东西必须先改文档）
ALLOWED_THIRD_PARTY = {"numpy", "PIL", "cv2", "scipy", "edge_tts", "playwright",
                       # v0.8：ASR 回读门禁（可选，缺了 qaasr 自动降级到 ffmpeg whisper / skipped）
                       "faster_whisper"}
# 同目录同级模块（拷走整个目录后仍能互相 import）
LOCAL_MODULES = {"filmkit", "sfxkit", "scorekit", "webengine", "qa_frames", "render", "selfcheck",
                 "stylekit", "voicekit", "scenes", "scenes_demo",
                 # v0.6.0：门禁账本 / 交付报告 / 签核、阶段新鲜度守卫、film.json 契约校验器
                 "gates", "stagewatch", "filmcheck",
                 # v0.8.0：渲染前资源闸门、全片 VMAF/SSIM 门禁、ASR 回读门禁
                 "preflight", "qavmaf", "qaasr",
                 # v0.9.0：场景语法（内容 -> 构图选型）。纯标准库，可被 scenes.py 反向 import
                 "grammar",
                 # v1.0：设计 DSL / 离线预览 / 提案器（D3 改造的第一期）
                 "designkit", "previewkit", "propose",
                 # v1.0.1：presetkit 已删除（连同 presets/ 一起）；这里不能再列它，
                 # 否则 ci_check 会去 import 一个不存在的模块
                 # v1.0 / P2：参数化配乐编排器（替代 9 套 MUSIC_STYLES 表）
                 "musicgen"}
MAX_FILE_BYTES = 400 * 1024
# 体积检查忽略的目录后缀：.team 是本地调研/自测工作区（gitignore），不随包发布；
# 媒体文件本来就该大（样例片/参考视频），不该拿"能不能塞进上下文"的尺子量。
SIZE_SKIP_DIRS = {".git", "node_modules", ".team", "work", "out", "__pycache__", ".venv", "venv"}
SIZE_SKIP_EXT = {".mp4", ".mp3", ".wav", ".mov", ".webm", ".gif", ".webp", ".zip", ".tgz"}

_results: list[tuple[str, str, str]] = []

# 「跳过」的前缀约定：检查函数返回这些开头的字符串表示**这次没有真的查**。
# 以前这里只有 bool 两态，"跳过（没装 node）"会被记成 PASS——正是本仓库反复声明的
# "skipped != 通过"，却在仓库级体检自己的汇总里破了例（本地没装 node 的人会看到
# PASS 12/12，其实第 2、6 项根本没跑）。现在三档：PASS / FAIL / SKIP。
SKIP_PREFIXES = ("跳过", "skip")


def _is_skip(detail: str) -> bool:
    head = str(detail or "").strip().lower()
    return any(head.startswith(p) for p in SKIP_PREFIXES)


def check(name: str, fn):
    """跑一项检查；返回 PASS / FAIL / SKIP 三态，detail 保留证据或原因。"""
    try:
        detail = fn() or ""
        if _is_skip(detail):
            _results.append(("SKIP", name, str(detail)))
            return "SKIP"
        _results.append(("PASS", name, str(detail)))
        return "PASS"
    except AssertionError as e:
        _results.append(("FAIL", name, str(e)))
        return "FAIL"
    except Exception as e:                                      # noqa: BLE001
        _results.append(("FAIL", name, "%s: %s" % (type(e).__name__, e)))
        return "FAIL"


# ---------------------------------------------------------------- 1 语法
def _py_files() -> list[Path]:
    """技能脚本 + 技能资产里的 .py + 仓库工具脚本。

    tools/ 以前不在扫描范围里，于是 ci_check.py 自己的 invalid escape sequence
    （Python 3.12 的 SyntaxWarning）一路带到发布，没人发现——"语法检查"只查了半边。
    """
    out = sorted(SCRIPTS.glob("*.py")) + sorted(ASSETS.glob("*.py")) + sorted(TOOLS.glob("*.py"))
    return [p for p in out if p.name != "__init__.py"]


def t_syntax_py():
    """编译所有 .py；SyntaxWarning 也算失败。

    为什么要警告转错误：invalid escape sequence（反斜杠 + 普通字母，比如 Python 里的"\\d"）在 3.12 起是 SyntaxWarning，
    只打印不失败——脚本照跑，但 warning 会出现在每个用户的第一次导入里，而且掩盖真问题。
    """
    import warnings

    bad = []
    for p in _py_files():
        src = p.read_text(encoding="utf-8")
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", SyntaxWarning)
                compile(src, str(p), "exec")
            warns = [w for w in caught if issubclass(w.category, SyntaxWarning)]
            if warns:
                bad.append("%s:%s SyntaxWarning: %s"
                           % (p.name, warns[0].lineno, warns[0].message))
        except SyntaxError as e:
            bad.append("%s:%s %s" % (p.name, e.lineno, e.msg))
    assert not bad, "；".join(bad)
    return "%d 个 .py 全部可编译（含 tools/，SyntaxWarning 记失败）" % len(_py_files())


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
    # 版本号三处必须一致：package.json / README 徽章 / marketplace-entry 描述里提到的 vX.Y。
    # 这条以前没人查，于是 README 的徽章停在 0.6.0、包已经是 0.7.1 也没人发现（真发生过）。
    ver = json.loads((ROOT / "package.json").read_text(encoding="utf-8")).get("version", "")
    assert re.fullmatch(r"\d+\.\d+\.\d+", ver), "package.json version 不像 semver：%r" % ver
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    badges = re.findall(r"badge/version-(\d+\.\d+\.\d+)-", readme)
    assert badges, "README 里找不到 version 徽章（badge/version-x.y.z）"
    bad_badges = [b for b in badges if b != ver]
    assert not bad_badges, ("README 徽章版本 %s 与 package.json version %s 不一致"
                            % ("/".join(bad_badges), ver))
    # CHANGELOG 的第一条标题也必须是当前版本：以前只查 README 徽章，于是"发了新版本但
    # CHANGELOG 顶部还是上一版"没人发现（历史真发生过，见 v0.7.2 的修复记录）。
    changelog = ROOT / "CHANGELOG.md"
    assert changelog.is_file(), "缺 CHANGELOG.md"
    m = re.search(r"^##\s+(\d+\.\d+\.\d+)", changelog.read_text(encoding="utf-8"), re.M)
    assert m, "CHANGELOG.md 里找不到 \"## X.Y.Z\" 形式的版本标题"
    assert m.group(1) == ver, ("CHANGELOG 顶部版本 %s 与 package.json version %s 不一致"
                               "（发版时两处必须同时改）" % (m.group(1), ver))
    return ("%d 个 .json 全部可解析；version=%s 与 README 徽章 / CHANGELOG 顶部一致"
            % (len(files), ver))


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
    junk_suffix = (".pyc", ".pyo", ".log", ".tmp", ".orig", ".bak", ".mp4", ".wav", ".mkv", ".tgz",
                   # 二进制/可执行文件：技能包里不该有，出现就是某个进程把缓存写进了源码树。
                   # 实测踩过：输入法按 %SystemDrive%\ProgramData\...\*.bin 往 cwd 落缓存，
                   # 当时 cwd 正好是 skills/video-studio/scripts/，两个 172 KB 的 .bin 进了包，
                   # 而旧的 junk 规则只看 .pyc/.log/.mp4 这些后缀，检查照样 PASS。
                   ".bin", ".dll", ".exe", ".so", ".dylib", ".pyd", ".db", ".sqlite")
    junk_parts = ("__pycache__", "tmp_scratch_", "node_modules", ".pytest_cache", ".cordis")
    junk = [f for f in files
            if f.endswith(junk_suffix) or any(p in f for p in junk_parts)
            or f.startswith(("work/", "out/", ".team/"))
            # 路径里带 % 段 = 某个环境变量没被展开（Windows 上的典型坏路径）。
            # 发布包里不可能有合法的含 % 路径段，直接判垃圾，而不是去猜是哪个变量。
            or any("%" in part for part in f.split("/"))]
    assert not junk, ("包内不该有这些文件（临时产物/日志/媒体，属于打包污染）：%s"
                      % ", ".join(junk[:6]))
    # 根文件清单仍然手写：它们少一个插件就装不起来，而且数量少、变动少。
    must = ["index.js", "cordis.patch.yml", "icon.svg", "package.json", "README.md"]
    missing = [m for m in must if m not in files]
    assert not missing, "包内缺文件：%s" % ", ".join(missing)
    # 目录部分改成**派生式**：skills/ schemas/ locale/ tools/ docs/ 下磁盘上有的（除缓存/字节码）
    # 必须全部进包。以前这里是手写 must 列表，于是 v0.6 新增的 gates.py / stagewatch.py、
    # v0.8 新增的 preflight.py / qavmaf.py / qaasr.py / tools/golden_frames.py 都不在列表里——
    # "必需文件必须在包里"这条检查对**新增文件天然免疫**，等于只保护了第一版写下的那些路径。
    shipped_dirs = ("skills", "schemas", "locale", "tools", "docs")
    on_disk = []
    for d in shipped_dirs:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            rel = p.relative_to(ROOT).as_posix()
            # 命中 junk 的文件不参与"必须进包"的比对：它们本来就不该进包，
            # 而"磁盘上有就必须进包"会把垃圾反向当成必需文件（实测：给 package.json 加
            # "!skills/**/*.bin" 排除规则时，这条派生检查反过来 FAIL 了）。
            if rel.endswith(junk_suffix) or any(k in rel for k in junk_parts) \
                    or any("%" in part for part in rel.split("/")):
                continue
            on_disk.append(rel)
    packed_set = {f.replace("\\", "/") for f in files}
    not_packed = [p for p in on_disk if p not in packed_set]
    assert not not_packed, (
        "这些文件在仓库里但没进包（package.json 的 files 漏了？）：%s"
        % ", ".join(not_packed[:8]))
    return "%d 个文件 / %.0f KB；%d 个受管目录文件全部进包" % (
        len(files), meta.get("size", 0) / 1024.0, len(on_disk))


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
    # 正文里的相对 Markdown 链接 / 图片是另一类：上面的正则只覆盖 scripts|assets|references 前缀，
    # [说明](references/foo.md) 之外的写法（比如 [x](../schemas/film.schema.json)）会漏掉。
    link_bad = []
    for md in md_files:
        if not md.is_file():
            continue
        txt = md.read_text(encoding="utf-8")
        for m in re.finditer(r"!?\[([^\]]*)\]\(([^)\s]+)\)", txt):
            target = m.group(2)
            if re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|#)", target):
                continue                                          # 外链 / 纯锚点
            rel = target.split("#", 1)[0]
            if not rel:
                continue
            cands = [md.parent / rel, ROOT / rel, SKILL / rel]
            if not any(c.exists() for c in cands):
                link_bad.append("%s 里链接 %s（不存在）" % (md.name, target))
        # README 的效果图是 HTML <img src="docs/...">，不是 Markdown 语法，单独查一遍。
        for m in re.finditer(r"<img[^>]*\ssrc=[\"']([^\"']+)[\"']", txt):
            target = m.group(1)
            if re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)", target):
                continue
            rel = target.split("?", 1)[0].split("#", 1)[0]
            cands = [md.parent / rel, ROOT / rel]
            if rel and not any(c.exists() for c in cands):
                link_bad.append("%s 里 <img src> 指向 %s（不存在）" % (md.name, target))
    # 第三种：带占位符的路径（<SKILL_DIR>/... 与 <REPO>/...）。ci_check 之前只认 scripts/|assets/|references/
    # 前缀，SKILL.md 里 <SKILL_DIR>/../../../tools/estimate.py 这种写错两级相对层级的命令一直没人查——
    # 实测它指向仓库上一级，是条**跑不通的命令**。语义：<SKILL_DIR> = skills/video-studio/，
    # <REPO> = 包根（仓库根）。注意**不能**把它们当成"当前工作目录"：quickstart 把技能拷进 myfilm/ 后，
    # 那里的 skills 与 tools 都不存在，但文档里的占位符指的是包内位置，仍然应当成立。
    ph_bad = []
    ph_re = re.compile(r"(<SKILL_DIR>|<REPO>)/([A-Za-z0-9_.\-/{}*]+)")
    for md in md_files:
        if not md.is_file():
            continue
        for ln_no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            for m in ph_re.finditer(line):
                base = SKILL if m.group(1) == "<SKILL_DIR>" else ROOT
                rel = m.group(2).rstrip(".,;:")
                if any(ch in rel for ch in "{}*"):
                    rel = rel.split("{")[0].split("*")[0].rsplit("/", 1)[0] if ("{" in rel or "*" in rel) else rel
                if not (base / rel).exists():
                    ph_bad.append("%s:%d 的 %s/%s 不存在" % (md.name, ln_no, m.group(1), rel))
    assert not ph_bad, "；".join(sorted(set(ph_bad))[:8])
    assert not link_bad, "；".join(sorted(set(link_bad))[:8])
    assert not missing, "；".join(sorted(set(missing))[:8])
    return "%d 篇文档的路径引用 + 正文相对链接全部存在" % sum(1 for m in md_files if m.is_file())


# ---------------------------------------------------------------- 7 体积
def t_size():
    big = []
    for p in [p for p in ROOT.rglob("*") if p.is_file()
              and not (set(p.parts) & SIZE_SKIP_DIRS)
              and not str(p.relative_to(ROOT)).startswith("tmp_scratch_")
              and p.suffix.lower() not in SIZE_SKIP_EXT
              and p.suffix != ".png"]:
        n = p.stat().st_size
        if n > MAX_FILE_BYTES:
            big.append("%s %.0f KB" % (p.relative_to(ROOT), n / 1024.0))
    assert not big, "；".join(big)
    return "所有文本文件 < %.0f KB" % (MAX_FILE_BYTES / 1024.0)



# ---------------------------------------------------------------- 8 兼容 Python 3.9（README/THIRD_PARTY 的承诺）
# 3.10+ 才有、但**语法上**能通过 3.9 解析的 API —— ast.parse(feature_version=(3,9)) 看不见它们。
# 这正是"门禁看着干净、其实没在查"的典型形态：zip(..., strict=True) 在 3.9 上是 TypeError
# （zip() takes no keyword arguments），而语法检查一路绿灯。
# 只查**确定**不兼容且容易误用的这几个；不要按 BinOp|BitOr 一律报错（numpy 位或会刷假失败）。
def _py39_runtime_violations(tree):
    """扫 3.10+ 运行期 API。返回 [(行号, 说明)]。"""
    out = []
    has_future_annotations = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(a.name == "annotations" for a in node.names):
                has_future_annotations = True
    ann_nodes = set()
    for node in ast.walk(tree):
        # 收集"注解位置"的节点：只有这里的 X | Y 才是类型联合，函数体里的是 numpy 位或
        for attr in ("annotation", "returns"):
            sub = getattr(node, attr, None)
            if sub is not None:
                for x in ast.walk(sub):
                    ann_nodes.add(id(x))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name == "zip":
                for kw in node.keywords:
                    if kw.arg == "strict":
                        out.append((node.lineno, "zip(..., strict=...) 需要 Python 3.10+"))
            if name == "bit_count":
                out.append((node.lineno, "int.bit_count() 需要 Python 3.10+"))
            if name == "dataclass":
                for kw in node.keywords:
                    if kw.arg in ("slots", "kw_only"):
                        out.append((node.lineno,
                                    "dataclass(slots=/kw_only=) 需要 Python 3.10+"))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "tomllib":
                    out.append((node.lineno, "tomllib 需要 Python 3.11+"))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "tomllib":
                out.append((node.lineno, "tomllib 需要 Python 3.11+"))
        elif isinstance(node, ast.TryStar):
            out.append((node.lineno, "except* 需要 Python 3.11+"))
        elif isinstance(node, ast.Match):
            out.append((node.lineno, "match/case 需要 Python 3.10+"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            if not has_future_annotations and id(node) in ann_nodes:
                out.append((node.lineno,
                            "X | Y 类型注解需要 3.10+（用 Union[X, Y] 或加 "
                            "from __future__ import annotations）"))
    return out


def t_py39():
    bad = []
    for p in _py_files():
        src = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=str(p), feature_version=(3, 9))
        except SyntaxError as e:
            bad.append("%s:%s %s" % (p.name, e.lineno, e.msg))
            continue
        for lineno, why in _py39_runtime_violations(tree):
            bad.append("%s:%s %s" % (p.name, lineno, why))
    assert not bad, "；".join(bad[:6])
    return ("%d 个 .py 通过 3.9 语法解析 + 3.10+ 运行期 API 扫描"
            "（真 3.9 运行仍需实测）" % len(_py_files()))



# ---------------------------------------------------------------- 9 Markdown 围栏平衡
def t_md_fences():
    """每篇 .md 的代码围栏必须成对（按围栏状态机判，不是数出现次数）。

    v0.7.1 之前这里写的是 startswith("\\`\\`\\`")——那是一个无效转义，Python 把反斜杠原样保留，
    于是永远匹配不到真实的 ``` 行，检查恒过（"看着在查，其实查不出"，正是本仓库最怕的形态）。
    现在改成状态机：开围栏记下字符与长度，闭合围栏必须同字符、长度不小于开口、且不带信息串。
    """
    bad = []
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "THIRD_PARTY_NOTICES.md",
             SKILL / "SKILL.md"] + sorted(REFS.glob("*.md"))
    fence_re = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
    for p in files:
        if not p.is_file():
            continue
        opened = None
        close_line = None
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            m = fence_re.match(line)
            if not m:
                continue
            fence, info = m.group(1), m.group(2).strip()
            if opened is None:
                opened, close_line = (fence[0], len(fence)), i
            elif fence[0] == opened[0] and len(fence) >= opened[1] and not info:
                opened = None
        if opened is not None:
            bad.append("%s: 第 %d 行开的 %s 围栏没有闭合" % (p.name, close_line, opened[0] * 3))
    assert not bad, "；".join(bad)
    return "%d 篇 Markdown 围栏都成对（状态机判定）" % len([p for p in files if p.is_file()])


# ---------------------------------------------------------------- 10 CI workflow 基本语法
def t_ci_workflow():
    """GitHub Actions workflow 的两个高频错误。

    1) 「裸冒号」：「- name: selftest: xxx」里第二个冒号会让整份 YAML 解析失败，而 GitHub 上的表现是
       「workflow 根本没跑」——没有任何报错提示，最容易被忽略（本项目真踩过）。
    2) 「run 里指向不存在的脚本」：`run: python tools/xxx.py` 写错路径时，本地 ci_check 看不出来，
       要等 CI 跑红——而那一步本来就该秒级失败，所以在这里先查一遍文件是否存在。
    不引第三方 YAML 库（CI 里不一定装了 pyyaml），只查这两类高频错误。
    """
    wf = ROOT / ".github" / "workflows"
    if not wf.is_dir():
        return "跳过（没有 .github/workflows）"
    bad = []
    files = sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml"))
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^(\s*)-\s+name:\s*(.+)$", line)
            if m:
                val = m.group(2).strip()
                if val[:1] not in ("'", '"') and ": " in val:
                    bad.append("%s:%d 的 step 名含未加引号的冒号（YAML 会解析失败，整份 workflow 不跑）"
                               % (f.name, i))
                continue
            # run 块里的 python <相对路径>.py：工作目录可能是仓库根，也可能是技能 scripts 目录。
            # 只看「行首是 python」的行（含 `run: |` 块里的缩进行），不做 YAML 解析，避免把注释/名称
            # 里的 python 字样当命令（误报比漏报更烦人）。
            if not re.match(r"^\s*(?:-\s+)?(?:run:\s*)?python[0-9.]*\s+", line):
                continue
            for pm in re.finditer(r"python[0-9.]*\s+(?!-)([A-Za-z0-9_./-]+\.py)", line):
                rel = pm.group(1)
                if rel.startswith("/") or ":" in rel:
                    continue                                  # 绝对路径 / URL，不归本检查管
                cands = [ROOT / rel, SCRIPTS / rel, SKILL / rel]
                if not any(c.exists() for c in cands):
                    bad.append("%s:%d 里 python 调用的 %s 不存在" % (f.name, i, rel))
    assert not bad, "；".join(bad[:4])
    return "%d 个 workflow：step 名无裸冒号、run 里的 python 脚本都存在" % len(files)

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
    ap.add_argument("--strict", action="store_true",
                    help="SKIP 也算失败（本地缺 node/npm 时想确认\"真的全查了\"就用它）")
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
        check(name, fn)
    for status, name, detail in _results:
        # PASS 的 detail 只在 -v 下打印（它通常只是"N 个文件全部可编译"）；FAIL/SKIP 一律打印原因
        print("  %s %-24s %s" % (status, name, detail if (status != "PASS" or a.verbose) else ""))
    fails = [r for r in _results if r[0] == "FAIL"]
    skips = [r for r in _results if r[0] == "SKIP"]
    n_pass = len(_results) - len(fails) - len(skips)
    print("-" * 66)
    print("PASS %d / FAIL %d / SKIP %d" % (n_pass, len(fails), len(skips)))
    if fails:
        print("\n失败项：")
        for _status, name, detail in fails:
            print("  - %s：%s" % (name, detail))
    if skips:
        print("\n跳过项（**不等于通过**）：")
        for _status, name, detail in skips:
            print("  - %s：%s" % (name, detail))
    # 退出码口径：默认只把 FAIL 当失败（CI 里 node/npm 一定在，SKIP 不该出现）；
    # --strict 把 SKIP 也算失败——本地没装 node 的人想确认"真的全查了"就用它。
    if a.strict and skips:
        return 1
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())