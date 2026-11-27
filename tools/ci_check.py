# -*- coding: utf-8 -*-
"""ci_check.py — 仓库级体检（不需要 ffmpeg / 字体 / 网络，可在任何机器和 CI 上跑）。

跑法：python tools/ci_check.py [-v]

它检查的是**这个仓库作为"可分发的技能插件"的完整性**，而不是视频能不能出片
（出片要靠 selfcheck.py + 真跑一遍 render.py，见 SKILL.md §13）：

  1  语法：scripts/ + assets/ + tools/ 下的 .py 全部能编译（SyntaxWarning 也算失败）
  2  JS 语法：根目录与 assets/web/ 的 .js 通过 node --check；再真跑 plugin_entry_check（入口 / apply 配置面 /
     diagnose 真包）与 diagnose_selftest（好包 + 五类坏包的诊断回归）
  3  JSON：所有 .json 能被解析（locale / marketplace-entry / assets / schemas / package.json），
     schemas/ 的契约文件必须有以 http 开头的 $schema 方言，且 package.json 的 version 与 README 徽章一致
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
  11  体积：单个受检文本文件不超过 400 KB（媒体资产与缓存目录不计）
  12  film.json 契约：schemas/film.schema.json + scripts/filmcheck.py --json 校验夹具（skipped=true 也算失败）
  13  footage.json 契约：schemas/footage.schema.json 用 jsonschema 真校验回归样例与模板，
      再 exec footagekit.load_footage 载入模板（缺 jsonschema / numpy / Pillow 时记 SKIP ——
      那次没执行，不等于通过；包内本地模块导入失败则 FAIL）

三档结论（与技能自己的门禁账本同口径）：PASS = 这次真的查了且通过；FAIL = 查了没过；
SKIP = **这次没执行**（比如本地没装 node / npm），**不等于通过**。

退出码：0 = 无 FAIL；1 = 有 FAIL。加 --strict 时 SKIP 也算失败（本地环境缺工具时用它自证"真的全查了"）。
每一项都打印证据，不打"可能有问题"。
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
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
                       # 剪映草稿导出（可选依赖；缺了 jianying.py 降级为 draft_plan.json）
                       "pyJianYingDraft",
                       # ASR 回读门禁（可选，缺了 qaasr 自动降级到 ffmpeg whisper / skipped）
                       "faster_whisper"}
# 同目录同级模块（拷走整个目录后仍能互相 import）
LOCAL_MODULES = {"filmkit", "sfxkit", "scorekit", "webengine", "qa_frames", "render", "selfcheck",
                 # 独立终检（评审包）、验收帧/音轨偏移、节拍网格：纯函数模块，不引第三方
                 "reviewkit", "reviewpacket", "audio_offset", "beatgrid",
                 "shotcards", "editlist", "jianying", "asset_license",
                 "qatext",
                 "stylekit", "voicekit", "scenes", "scenes_demo",
                 # 门禁账本 / 交付报告 / 签核、阶段新鲜度守卫、film.json 契约校验器
                 "gates", "stagewatch", "filmcheck",
                 # 渲染前资源闸门、全片 VMAF/SSIM 门禁、ASR 回读门禁
                 "preflight", "qavmaf", "qaasr",
                 # 场景语法（内容 -> 构图选型）。纯标准库，可被 scenes.py 反向 import
                 "grammar",
                 # 画布/安全区单一真源（filmkit 从这里取 W/H/SAFE/SUB_BAND）
                 "layoutkit",
                 # 设计 DSL / 离线预览 / 提案器 / 出片前问诊
                 "designkit", "previewkit", "propose", "intake",
                 # 没有 presetkit（随预设目录一起移除）；这里不能再列它，
                 # 否则 ci_check 会去 import 一个不存在的模块
                 # 参数化配乐编排器（不提供曲风表，按参数现场编排）
                 "musicgen",
                 # 素材剪辑生产线（footage 模式）：取材层 / 剪辑层 / 专属门禁 / 入口
                 "footagekit", "footageedit", "footageqa", "footage"}
MAX_FILE_BYTES = 400 * 1024
# frontmatter description 的硬上限（理由见 t_manifest）：这行文字**每一轮**都进上下文，
# 而它的读者只有“要不要触发技能”这一个判断。能力细节属于正文，不属于这一行。
DESC_MAX_CHARS = 400
# 本文件的检查项数（main() 里那张 checks 表的长度）。文档与分发页写的"仓库体检 N 项"
# 要与它一致；改这里必须同批改 CHECKS 表，否则这项自检会先失败——这是故意的。
CI_CHECK_COUNT = 18
# 体积检查忽略的目录后缀：.team 是本地调研/自测工作区（gitignore），不随包发布；
# 媒体文件本来就该大（样例片/参考视频），不该拿"能不能塞进上下文"的尺子量。
# 缓存目录（.ruff_cache 等）以前会被算进来：它们随工具链生成、随时变化，既不是仓库内容
# 也不进包（打包卫生那一项按 npm 实际内容判），拿它们的单个文件大小判仓库卫生是错口径。
SIZE_SKIP_DIRS = {".git", "node_modules", ".team", "work", "out", "__pycache__", ".venv", "venv",
                  ".ruff_cache", ".pytest_cache", ".mypy_cache", ".tmpcheck"}
# 二进制后缀统一排除：这条查的是"仓库里的文本内容会不会太大"，不是媒体资产。
# 以前只排除 .png，.jpg/.jpeg 却参与判定——同一类派生帧图（联系表）两种口径，实测量到
# 回归联系表 182 KB 时没事，稍大一档就会报"文本文件太大"却找不到大文本文件。
SIZE_SKIP_EXT = {".mp4", ".mp3", ".wav", ".mov", ".webm", ".gif",
                 ".jpg", ".jpeg", ".png", ".webp", ".zip", ".tgz"}

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
    # glob 匹配为空时不能算 PASS：文件被误删/路径写错时，"0 个 .js 全部通过" 是假绿
    # （实测把全部 .js 删掉后本项仍报 PASS）。至少要有插件入口与页面示例这两类。
    assert js, "没找到任何 .js（index.js / assets/web/*.js）：路径写错或文件被删，本项等于没查"
    for p in js:
        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, "%s: %s" % (p.name, (r.stderr or r.stdout).strip()[:300])
    # 语法过了不等于能挂上技能：DSH 0.2.x 换成了 ctx.skills.registerProvider，
    # 而旧入口仍只走 0.1.x 的 dsh-skill-filesystem.apply()——在 0.2.2-rc.5 上
    # import 解析不到那个包，apply 只打一条日志就返回（registerProvider 调用 0 次），
    # 于是技能一个都没挂上，而 node --check / selfcheck / 插件行全绿。实测复现。
    # 这里用 tools/plugin_entry_check.mjs 拿最小 ctx 桩真的跑一遍 apply，
    # 断言 provider 确实注册且 list/get 能拿到 SKILL.md。
    smoke = ROOT / "tools" / "plugin_entry_check.mjs"
    assert smoke.is_file(), "缺 tools/plugin_entry_check.mjs（插件入口冒烟：防止技能静默挂不上）"
    r = subprocess.run([node, str(smoke)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=120)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    assert r.returncode == 0, "插件入口冒烟失败（技能可能挂不上）：%s" % out[-500:]
    # 诊断入口（P0-4）的坏包回归：好包全绿只是"没崩"，要证明诊断真在查，必须造坏包——
    # 删 references 后 danglingRefs 要点名、frontmatter 非法时 degraded 要给 reason。
    # 这个脚本只读仓库、在系统临时目录造夹具，不写工作树。
    diagnose_selftest = ROOT / "tools" / "diagnose_selftest.mjs"
    assert diagnose_selftest.is_file(), "缺 tools/diagnose_selftest.mjs（诊断入口的坏包回归）"
    d = subprocess.run([node, str(diagnose_selftest)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=120)
    d_out = ((d.stdout or "") + (d.stderr or "")).strip()
    assert d.returncode == 0, "诊断自检失败（diagnose 对坏包的判断不对）：%s" % d_out[-500:]
    return ("%d 个 .js 全部通过 node --check；插件入口冒烟通过（provider 注册 + list/get + "
            "apply 配置面 / 非法配置报错）；诊断自检通过（%s）" % (len(js), d_out.splitlines()[-1] if d_out else ""))


def _which(exe: str):
    from shutil import which
    return which(exe)


# ---------------------------------------------------------------- 2 JSON
def t_json():
    files = [ROOT / "package.json", ROOT / "marketplace-entry.json"]
    files += sorted((ROOT / "locale").glob("*.json"))
    files += sorted(ASSETS.glob("*.json"))
    # schemas/ 以前**不在**解析清单里：把 design.schema.json 改成坏 JSON 之后，这项仍然报
    # "11 个 .json 全部可解析"——契约文件坏了却没人查，正是本仓库最怕的"看着在查，其实没查"。
    # 契约文件还多一条硬要求：必须有以 http 开头的 $schema 方言声明（draft-07 与 2020-12
    # 的语义不同，没有方言声明时校验器只能猜）。
    schema_files = sorted((ROOT / "schemas").glob("*.json"))
    files += schema_files
    parsed = {}
    for p in files:
        try:
            parsed[p] = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                                  # noqa: BLE001
            raise AssertionError("%s: %s" % (p.relative_to(ROOT), e)) from None
    assert schema_files, "schemas/ 下没有任何 .json：契约目录缺失或路径写错"
    bad_dialect = []
    for p in schema_files:
        doc = parsed[p]
        dialect = doc.get("$schema") if isinstance(doc, dict) else None
        if not isinstance(dialect, str) or not dialect.startswith("http"):
            bad_dialect.append("%s: $schema=%r（契约文件必须写以 http 开头的方言 URL）"
                               % (p.relative_to(ROOT), dialect))
    assert not bad_dialect, "；".join(bad_dialect)
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
    # 英文入口页（README.en.md）的徽章与互链也必须跟得上：
    # 只查中文版会让英文页静默停在旧版本——用户按英文页判断"这版有没有某个功能"，
    # 一旦漂移就是错误信息，比内部文档漂移更直接伤到人。
    readme_en = ROOT / "README.en.md"
    assert readme_en.is_file(), "缺 README.en.md（多语言入口页；中文版为主，英文版必须存在）"
    en_text = readme_en.read_text(encoding="utf-8")
    en_badges = re.findall(r"badge/version-(\d+\.\d+\.\d+)-", en_text)
    assert en_badges, "README.en.md 里找不到 version 徽章"
    assert all(b == ver for b in en_badges), (
        "README.en.md 徽章版本 %s 与 package.json version %s 不一致"
        % ("/".join(b for b in en_badges if b != ver), ver))
    # 互相链接：两页都要能从对方跳过去，且中文版要声明"英文版是摘要、中文版为准"，
    # 否则读者可能以为两版等价、照着英文页的删节内容行事。
    assert "README.en.md" in readme, "README.md 里没有指向 README.en.md 的语言切换链接"
    assert "README.md" in en_text, "README.en.md 里没有指回 README.md 的链接"
    # CHANGELOG 的第一条标题也必须是当前版本：以前只查 README 徽章，于是"发了新版本但
    # CHANGELOG 顶部还是上一版"没人发现（真发生过）。
    changelog = ROOT / "CHANGELOG.md"
    assert changelog.is_file(), "缺 CHANGELOG.md"
    m = re.search(r"^##\s+(\d+\.\d+\.\d+)", changelog.read_text(encoding="utf-8"), re.M)
    assert m, "CHANGELOG.md 里找不到 \"## X.Y.Z\" 形式的版本标题"
    assert m.group(1) == ver, ("CHANGELOG 顶部版本 %s 与 package.json version %s 不一致"
                               "（发版时两处必须同时改）" % (m.group(1), ver))
    return ("%d 个 .json 全部可解析（含 schemas/ %d 个，均带 $schema 方言）；"
            "version=%s 与 README 徽章 / CHANGELOG 顶部一致"
            % (len(files), len(schema_files), ver))


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
    # 上限和下限一样重要：description 出现在**每个会话的每一轮**系统提示里（不是只在触发时），
    # 一份 700 字的说明就是持续的上下文税。
    d_len = len(desc.group(1).strip())
    assert d_len <= DESC_MAX_CHARS, (
        "frontmatter description 太长（%d 字 > %d）：它每轮都在上下文里，"
        "触发场景 + 一句能力边界就够，流程细节写进正文" % (d_len, DESC_MAX_CHARS))
    assert name.group(1) == "video-studio", "frontmatter name=%r，应为目录名 video-studio" % name.group(1)
    # frontmatter 必须是**合法 YAML**，不只是"正则能抓到 name/description"。
    # 真实事故：description 里写了 ASCII 冒号+空格（"Two pipelines for Chinese videos: footage..."），
    # 块映射的裸标量里 ": " 非法 —— 官方 dsh-skill-filesystem 用真 YAML 解析，会把整个技能
    # 判成 invalid frontmatter 并**静默跳过**；而本插件自带解析器与 ci_check 的正则都读得懂，
    # 于是本仓库全绿、换到官方 provider 就挂不上。这里用标准库做一条最小但有效的守卫：
    # 未加引号的标量值不许含 ": "（加引号/块标量则合法，这里不拦）。
    bad_scalar = []
    for line in fm.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        if s[0] in " \t":
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9_-]*:", s):
            continue
        val = s.split(":", 1)[1]
        if not val.strip():
            continue
        head = val.lstrip()[:1]
        if head in ("|", ">", "[", "{", '"', "'", "&", "*", "!"):
            continue
        if ": " in val:
            bad_scalar.append(s.split(":", 1)[0])
    assert not bad_scalar, (
        "frontmatter 不是合法 YAML：未加引号的标量里含 \": \"（%s）。"
        "官方 YAML 解析器会判 invalid frontmatter 并静默跳过该技能；"
        "改成中文冒号/破折号，或给整行值加引号。"
        % " / ".join(bad_scalar))
    return "name=video-studio / description %d 字（上限 %d）" % (d_len, DESC_MAX_CHARS)


# ---------------------------------------------------------------- 4 依赖白名单
# 判据只有一处：_is_stdlib()（逐个 import 名问解释器）。这里以前还有
# _stdlib_names() / _stdlib_roots() / _stdlib_names_by_search() 三个"扫目录枚举标准库"
# 的辅助函数：全仓 0 调用，且与 _is_stdlib 的 docstring 明确否定的方案重复——
# 留着只会让下一个人以为标准库判定有两套实现。已删除（2026-11 审查）。
def _is_stdlib(name):
    """name（import 的顶层名）是否属于标准库。

    判据：① 解释器自报的 stdlib_module_names / builtin（3.10+ 快路径）；
          ② 否则 importlib.util.find_spec 实际解析：origin 在 Python 安装前缀内、
             且不在 site-packages/dist-packages 里 → 标准库。
    内置/冻结模块（origin 是 "built-in"/"frozen"）也算。

    为什么不用"扫 stdlib 目录"的做法：Windows 的扩展在 <prefix>/DLLs、
    Linux 的在 <prefix>/lib-dynload —— 两者都在 prefix 内但不在 stdlib 内，
    任何目录枚举都得为每个平台写一遍特例，而 find_spec 直接问解释器，一次到位。
    """
    top = str(name or "").split(".")[0]
    if not top:
        return False
    if top in sys.builtin_module_names:
        return True
    known = getattr(sys, "stdlib_module_names", None)
    if known is not None and top in known:
        return True
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError, AttributeError, TypeError):
        return False
    if spec is None:
        return False
    origin = getattr(spec, "origin", None) or ""
    if origin in ("built-in", "frozen"):
        return True
    raw_paths = [origin]
    raw_paths.extend(list(getattr(spec, "submodule_search_locations", None) or []))
    prefix_roots = []
    for base in (getattr(sys, "base_prefix", "") or "", getattr(sys, "prefix", "") or ""):
        if base and os.path.isdir(base):
            prefix_roots.append(os.path.abspath(base))
    site_parts = ("site-packages", "dist-packages")
    for raw in raw_paths:
        if not raw:
            continue
        try:
            ap = os.path.abspath(raw)
        except (TypeError, ValueError):
            continue
        if any(sep in ap for sep in site_parts):
            return False
        if any(ap == r or ap.startswith(r + os.sep) for r in prefix_roots):
            return True
    return False


def t_deps():
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
                if (top in ALLOWED_THIRD_PARTY or top in LOCAL_MODULES
                        or _is_stdlib(top)):
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
        raise AssertionError("解析 npm pack 输出失败：%s" % e) from None
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
    # 必须全部进包。手写 must 列表对新增文件天然免疫：gates.py / stagewatch.py /
    # preflight.py / qavmaf.py / qaasr.py / tools/golden_frames.py 都曾被漏在列表外——
    # "必需文件必须在包里"这条检查对**新增文件天然免疫**，等于只保护了第一版写下的那些路径。
    # 注意 "docs"：现在仓库里**没有**这个目录（`if not base.is_dir(): continue` 直接跳过），
    # 列进来是给将来放效果图用的。**加 docs/ 的那次提交必须同时把 "docs" 写进 package.json 的
    # files**，否则本项会红且提示"这些文件在仓库里但没进包"——那不是误报，是这条派生检查在
    # 提醒你"文档站资源也该随包分发"（或者你根本不该把它放进仓库）。
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
    # README.en.md 也扫：英文页里的路径/链接同样会腐烂，而它最容易被人忽略
    md_files = ([SKILL / "SKILL.md", ROOT / "README.md", ROOT / "README.en.md"]
                + sorted(REFS.glob("*.md")))
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
    # <REPO> = 包根（仓库根）。注意**不能**把它们当成"当前工作目录"：把技能解到 myfilm/ 下面后，
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
    # 第四种（反向）：references/ 下的文件必须被 SKILL.md 正文点名。
    # 前三种都只查"写出来的路径存不存在"，查不到"存在的参考资料有没有被目录表漏掉"——
    # 正文瘦身后最容易留下的形态正是"材料还在、目录表没登记"，模型永远不会去读它。
    # 判据取"文件名主干是否出现在正文里"，宁松不误伤。
    skill_md = SKILL / "SKILL.md"
    orphan_refs = []
    if skill_md.is_file():
        body = skill_md.read_text(encoding="utf-8")
        for f in sorted(REFS.glob("*.md")):
            stem = f.stem
            if (("references/%s" % f.name) in body or ("references/%s" % stem) in body
                    or stem in body):
                continue
            orphan_refs.append("references/%s" % f.name)
    assert not orphan_refs, ("这些 references 文件存在但 SKILL.md 一次都没提到（目录表漏登记）：%s"
                             % "、".join(orphan_refs[:8]))
    assert not ph_bad, "；".join(sorted(set(ph_bad))[:8])
    assert not link_bad, "；".join(sorted(set(link_bad))[:8])
    assert not missing, "；".join(sorted(set(missing))[:8])
    return ("%d 篇文档的路径引用 + 正文相对链接全部存在；references/"
            "%d 篇全部被 SKILL.md 点名" % (sum(1 for m in md_files if m.is_file()), len(list(REFS.glob("*.md")))))


# ---------------------------------------------------------------- 7 体积
def t_size():
    big = []
    checked = 0
    for p in [p for p in ROOT.rglob("*") if p.is_file()
              and not (set(p.parts) & SIZE_SKIP_DIRS)
              and not str(p.relative_to(ROOT)).startswith("tmp_scratch_")
              and p.suffix.lower() not in SIZE_SKIP_EXT]:
        checked += 1
        n = p.stat().st_size
        if n > MAX_FILE_BYTES:
            big.append("%s %.0f KB" % (p.relative_to(ROOT), n / 1024.0))
    assert not big, "；".join(big)
    # 文案必须与判定口径一致：旧文案写"所有文本文件"，实际却把 .jpg 也算进来，于是读者看到
    # "文本文件太大"却找不到大文本文件。"受检"= 跳过缓存目录与媒体后缀之后剩下的文件。
    return ("%d 个受检文本文件 < %.0f KB（媒体资产与缓存目录不计）"
            % (checked, MAX_FILE_BYTES / 1024.0))



# ---------------------------------------------------------------- 8 兼容 Python 3.9（README/THIRD_PARTY 的承诺）
# 3.10+ 才有、但**语法上**能通过 3.9 解析的 API —— ast.parse(feature_version=(3,9)) 看不见它们。
# 这正是"门禁看着干净、其实没在查"的典型形态：zip(..., strict=True) 在 3.9 上是 TypeError
# （zip() takes no keyword arguments），而语法检查一路绿灯。
# 只查**确定**不兼容且容易误用的这几个；不要按 BinOp|BitOr 一律报错（numpy 位或会刷假失败）。
# 这两个节点类型在旧解释器上不存在（TryStar 3.11+ / Match 3.10+）。
# 先 getattr 落成模块级常量，下面的 isinstance 才能在本文件**自己**跑在 3.9 上时也安全。
_AST_TRyStar = getattr(ast, "TryStar", None)
_AST_MATCH = getattr(ast, "Match", None)


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
        # 注意：TryStar / Match 这两个 AST 节点类型本身在 3.9 上**不存在**。
        # 直接写 isinstance(node, ast.TryStar) 会让整个检查在 3.9 上抛
        # AttributeError: module 'ast' has no attribute 'TryStar' ——
        # 而这正是"Python 3.9 兼容检查"这条断言自己的工作，等于它从没在 3.9 上跑过
        # （py39 job 从 v1.3.0 起一直红，红的原因就是它自己崩了）。
        elif _AST_TRyStar is not None and isinstance(node, _AST_TRyStar):
            out.append((node.lineno, "except* 需要 Python 3.11+"))
        elif _AST_MATCH is not None and isinstance(node, _AST_MATCH):
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

    早期这里写的是 startswith("\\`\\`\\`")——那是一个无效转义，Python 把反斜杠原样保留，
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
    # 同 t_syntax_js：0 个 workflow 不能算通过（删掉 ci.yml 后本项曾报"0 个 workflow…"PASS）。
    assert files, "没找到任何 workflow（.github/workflows/*.yml）：目录存在但没有文件，本项等于没查"
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
    调用必须带 --json 并检查 skipped：filmcheck 遇到"不是 film.json 的产物形态"（顶层
    没有 title/lines，比如配乐报告）时会打印 [SKIP]、退出码仍是 0；只看退出码会让这项在
    夹具变形之后照样 PASS——契约项在空转，而 PASS 会让所有人以为查过了。
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
    # --json 有第二个用处：机读输出里带 skipped 字段。skipped=true 表示 filmcheck 认为
    # 夹具根本不是 film.json（没有 title/lines），此时它 rc=0 但契约校验一次都没跑。
    # 这里必须把它当失败：夹具换成别的产物形态，等于第 12 项静默失效。
    p = subprocess.run([sys.executable, str(checker), str(film), "--json"],
                       cwd=str(scripts), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 0, ("夹具 film.json 没过契约校验（exit=%d）：\n%s"
                               % (p.returncode, out.strip()[-600:]))
    try:
        report = json.loads(p.stdout or "")
    except json.JSONDecodeError as e:
        raise AssertionError("filmcheck --json 的 stdout 不是可解析 JSON（%s）：\n%s"
                             % (e, out.strip()[-600:])) from None
    assert not report.get("skipped"), (
        "filmcheck 判定夹具不是 film.json（skipped=true）：夹具变成了别的产物形态，"
        "第 12 项等于空转。夹具必须含 title + lines（契约见 schemas/film.schema.json）")
    return ("夹具 film.json 通过契约校验（schema %d 字节，skipped=false）"
            % len(schema.read_text(encoding="utf-8")))


# ---------------------------------------------------------------- 13 footage.json 契约
def t_footage_contract():
    """素材剪辑线（footage.json）的契约同样只留一份真源。

    校验分两层：
      1) schemas/footage.schema.json 用 jsonschema 真校验两个真实 JSON
         （assets/footage/regression/footage.json 与 assets/footage/footage.template.json）。
         为什么必须真跑：schema 曾经与仓库里**唯一**真实样例互斥（根不允许 _comment、
         sources 不允许 burned_subs / watermark），而 footageqa 明确消费这两个字段做
         F2/F3 几何判定——契约文件把"合法配置"判成非法，等于把回归样例排除在契约之外。
         缺 jsonschema 时记 SKIP（那次没执行，不等于通过）。
      2) exec footagekit 再调 load_footage（结构硬错误由它自己报），但**只有**缺三方依赖
         白名单里的包才记 SKIP；包内本地模块（filmkit 之类）导入失败说明包坏了，必须
         FAIL——以前任何 ModuleNotFoundError 都记 SKIP，实测把 import filmkit 改坏后
         仍然打印"SKIP 跳过（缺 filmkit_xxx）"，指引完全错误（坏的是包，不是环境）。

    **为什么要单独一项**：footagekit 顶层 import filmkit，而 filmkit 要 numpy + Pillow。
    合成在 film 那项里时，任何**没装这两个可选依赖**的环境（裸 python、3.9 job）都会把
    "契约"整项测成 FAIL——测出来的其实是"这台机器没装 numpy"，不是"契约坏了"（v2.1.0
    CI 三红的根因之一）。拆开后缺依赖只让**这一项**记 SKIP：SKIP 会单列打印原因、绝不
    写成 PASS；CI 的 repo job 装了 numpy/Pillow/jsonschema，那里这一项是真跑。
    """
    footage_schema = ROOT / "schemas" / "footage.schema.json"
    footage_tpl = ROOT / "skills" / "video-studio" / "assets" / "footage" / "footage.template.json"
    footage_reg = (ROOT / "skills" / "video-studio" / "assets" / "footage"
                   / "regression" / "footage.json")
    assert footage_schema.exists(), "缺 schemas/footage.schema.json（footage.json 的契约）"
    try:
        schema_doc = json.loads(footage_schema.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AssertionError("schemas/footage.schema.json 不是合法 JSON：%s" % e) from None
    assert footage_tpl.exists(), "缺 footage 模板：assets/footage/footage.template.json"
    assert footage_reg.exists(), "缺 footage 回归样例：assets/footage/regression/footage.json"
    # jsonschema 是**仓库体检的校验工具**，不是技能运行期依赖，所以不能写进脚本白名单
    # （第 5 项会把它当成"偷偷引入的三方依赖"）。这里用 importlib 动态取模块而不是
    # 顶层 import：t_deps 扫的是 ast.Import/ImportFrom 节点，动态导入不会污染白名单判据；
    # 缺包时记 SKIP（那次没执行，绝不写成 PASS）。
    try:
        importlib.util.find_spec("jsonschema")
        jsonschema = importlib.import_module("jsonschema")
    except (ImportError, ValueError):
        return "跳过（缺 jsonschema，无法校验契约）"
    validator = jsonschema.Draft202012Validator(schema_doc)
    bad = []
    for sample in (footage_reg, footage_tpl):
        try:
            doc = json.loads(sample.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            bad.append("%s 不是合法 JSON：%s" % (sample.relative_to(ROOT), e))
            continue
        for err in sorted(validator.iter_errors(doc),
                          key=lambda e: tuple(str(x) for x in e.absolute_path)):
            path = "/".join(str(x) for x in err.absolute_path) or "<root>"
            bad.append("%s: %s: %s" % (sample.relative_to(ROOT), path, err.message))
    assert not bad, "footage schema 判真实样例非法：" + "；".join(bad[:8])
    kit = SCRIPTS / "footagekit.py"
    assert kit.exists(), "缺 scripts/footagekit.py（素材剪辑线核心库）"
    try:
        spec = importlib.util.spec_from_file_location("_ci_footagekit", str(kit))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as e:
        # 只有"这台机器缺可选三方依赖"才允许 SKIP；包内本地模块导入失败 = 包坏了。
        # 判据复用第 5 项的三方依赖白名单，而不是再手写一份（白名单只有一个真源）。
        top = str(e.name or "").split(".")[0]
        if top in ALLOWED_THIRD_PARTY:
            return ("跳过（缺三方依赖 %s；schema 已用 jsonschema 校验两个样例，"
                    "但 footagekit 的运行时契约没跑）" % top)
        raise AssertionError(
            "footagekit 导入失败：找不到本地模块 %r（不在三方依赖白名单 %s 里）。"
            "这是包坏了（文件被删/改名），不是这台机器缺可选依赖"
            % (e.name, "/".join(sorted(ALLOWED_THIRD_PARTY)))) from None
    try:
        mod.load_footage(str(footage_tpl))          # 有硬错误会直接 SystemExit
    except SystemExit as e:
        raise AssertionError("footage 模板没过 load_footage：%s" % (e.code or "")) from None
    return ("schema %d 字节：回归样例 + 模板均 0 error；footage 模板通过 load_footage"
            % len(footage_schema.read_text(encoding="utf-8")))

# ---------------------------------------------------------------- 14 断言条数一致
def t_cli_flags():
    """文档里 `python xxx.py --flag` 的开关必须真在该脚本里存在。

    为什么需要：文档就是 Agent 的行动依据。把 A 脚本的开关写到 B 脚本的命令行上、
    参数改名后文档没跟、开关被删了文档还留着——这三类都会让"照文档抄"直接 argparse 报错，
    而第 7 项只查路径存在、第 14 项只查条数，没有任何门禁会因此变红。

    口径（宁漏不误伤）：按行切段——一行里每次出现 `python ... <name>.py` 之后、到下一个
    命令之前的 `--flag` 归该脚本；只要求这个开关字符串在脚本源码里出现过（注册、help、
    常量都算）。实测全仓 308 处引用 0 误报；正文里举例提到别的脚本开关不会误伤。
    """
    docs = ([SKILL / "SKILL.md", ROOT / "README.md", ROOT / "README.en.md"]
            + sorted(REFS.glob("*.md")))
    cmd_re = re.compile(r"python[0-9.]*\s+(?:<SKILL_DIR>/|<REPO>/)?(?:skills/video-studio/|scripts/)?([a-z_]+)\.py")
    flag_re = re.compile(r"(?<!\w)(--[a-z][a-z0-9-]*)")
    bad = []
    checked = 0
    for doc in docs:
        if not doc.is_file():
            continue
        for ln_no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            hits = list(cmd_re.finditer(line))
            for i, m in enumerate(hits):
                name = m.group(1)
                src = SCRIPTS / (name + ".py")
                if not src.is_file():
                    continue
                seg = line[m.end():hits[i + 1].start() if i + 1 < len(hits) else len(line)]
                src_text = src.read_text(encoding="utf-8")
                for flag in sorted(set(flag_re.findall(seg))):
                    checked += 1
                    if ("\"%s\"" % flag) not in src_text and ("'%s'" % flag) not in src_text:
                        bad.append("%s:%d 给 %s.py 用了 %s（该脚本里没有这个开关）"
                                   % (doc.name, ln_no, name, flag))
    assert not bad, "；".join(sorted(set(bad))[:6])
    return "%d 处文档命令行开关都能在对应脚本里找到（按行切段归属，0 误报口径）" % checked


def t_surface_complete():
    """README 的文件结构块必须把仓库里的脚本/工具/契约/参考文档全部列出来。

    为什么需要（v2.7.2 实测）：README 的 `## 文件结构` 是用户与 Agent 认识仓库的唯一地图，
    而它当时漏了 7 个脚本（qatext/stylekit/compose/footage 四件套）、4 个工具、5 篇 references
    和 4 项资产。第 7 项只能查"写出来的路径存不存在"，查不到"存在的文件有没有被写出来"——
    地图缺块这件事没有任何东西会变红。这里补上：结构块里必须出现每个受管文件的名字。

    口径：只在 README 的 ``` 代码块内找，按 `scripts/<name>`、`tools/<name>` 等前缀匹配；
    允许只写目录（如 `assets/web/`）表示该目录下的非 Python 资产不逐个列。
    """
    readme = ROOT / "README.md"
    assert readme.is_file(), "缺 README.md"
    text = readme.read_text(encoding="utf-8")
    block = ""
    inside = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            block += line + "\n"
    assert "scripts/" in block, "README 的代码块里找不到文件结构块（scripts/ 一处都没有）"
    bad = []
    groups = [
        (SCRIPTS.glob("*.py"), "scripts/%s"),
        ((ROOT / "tools").glob("*.py"), "tools/%s"),
        ((ROOT / "tools").glob("*.mjs"), "tools/%s"),
        ((ROOT / "schemas").glob("*.json"), "schemas/%s"),
    ]
    for files, pat in groups:
        for f in sorted(files):
            if (pat % f.name) not in block:
                bad.append(pat % f.name)
    # references 在结构块里是「一行连排」写法（proposal · scene-grammar · …），
    # 不带 .md 后缀；先确认结构块提到了 references/ 这一节，再按文件名主干逐个核。
    refs = sorted(REFS.glob("*.md"))
    covered_refs = 0
    if "references/" in block:
        for f in refs:
            stem = f.stem
            if ("references/%s" % f.name) in block or ("references/%s" % stem) in block or stem in block:
                covered_refs += 1
            else:
                bad.append("references/%s" % f.name)
    else:
        bad.append("references/（整节没提）")
    assert not bad, ("README 文件结构块漏列 %d 项：%s"
                     % (len(bad), "、".join(bad[:10])))
    return "文件结构块覆盖 %d 个脚本 / %d 个工具 / %d 份契约 / %d 篇参考文档" % (
        len(list(SCRIPTS.glob("*.py"))), len(list((ROOT / "tools").glob("*"))),
        len(list((ROOT / "schemas").glob("*.json"))), covered_refs)


_ORDINAL_RE = re.compile(r"第\s*\d+(?:\s*[、,，/]\s*\d+)*(?:\s*[–\-~至]\s*\d+)?\s*(?:条|项)")


def _strip_ordinals(line):
    """把「第 54 条」「第 52–55 条」「第 71、72 条」这类序数引用换成占位。

    旧实现只在序数紧贴数字（「第54条」）时排除；文档普遍写成带空格的
    「第 54 条」，而 references/ 与 studio.py 一旦纳入扫描，这些序数就会
    被当成「总数写错」报假红。
    """
    return _ORDINAL_RE.sub("第N条", line)


def t_case_count():
    """文档里写的"N 条断言"必须等于 selfcheck.CASES 的真实长度。

    这条检查是给一次真实事故收的尾：CHANGELOG 记着编号表"落后过两次"
    （停在 43 而 CASES 已 51、停在 51 而 CASES 已 58），ci.yml 的注释也一度写 70
    而 CASES 是 72。数字写错不会让片子出问题，但会让"我查过了"这类说法贬值——
    读者发现一处对不上，就会怀疑其余每一处。

    口径：只扫**断言类**句子（同一行里同时出现 selfcheck/回归护栏/针对真实缺陷/断言
    与"N 条"），CHANGELOG 里"17 条反模式"这种无关计数不会被误伤；CHANGELOG 是历史
    记录，本来就会保留旧数字，不参与这项检查。
    数字错一律 FAIL，**不再放过 <10 的值**：旧逻辑把"85 条改成 9 条"当成编号忽略，
    于是错数字可以一路 PASS。序数误伤由正则的第-lookbehind 兜住（"第 54 条"不命中）。
    另外 SKILL.md / README.md / marketplace-entry.json 三篇必查文档各至少命中一次——
    把整句改写成中文数字（"共八十五条"）或干脆删掉时正则一处都不命中，这项检查会在
    那份文档上静默失效，所以"没写条数"本身也要 FAIL。
    """
    src = SCRIPTS / "selfcheck.py"
    assert src.is_file(), "缺 scripts/selfcheck.py"
    # 不 import：selfcheck 会 import numpy 等模块，ci_check 要能在裸机器上跑。
    tree = ast.parse(src.read_text(encoding="utf-8"))
    cases = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CASES" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                cases = len(node.value.elts)
            break
    assert cases is not None, "selfcheck.py 顶层找不到 CASES = [...]（断言器的主清单）"

    # 分发页文案也算"文档"：marketplace-entry.json 曾写着"自检 69 条、仓库体检 12 项"，
    # 而 CASES 已经是 72 —— 用户看到的简介与仓库真实状态不一致，比内部文档漂移更伤。
    docs = [SKILL / "SKILL.md", ROOT / "README.md", ROOT / "README.en.md",
            ROOT / ".github" / "workflows" / "ci.yml",
            ROOT / "marketplace-entry.json"]
    docs += sorted((ROOT / "locale").glob("*.json"))
    # references/ 是 Agent 真正按需加载的说明书，studio.py 的 check 子命令描述也写死过条数：
    # 两处都不在旧清单里，于是 v2.7.2 实测仍有 77 / 85 / 97 三个过时数字在流通。
    # 扫描它们，过期数字才会被这条检查当场抓住，而不是靠人翻文档发现。
    docs += sorted((SKILL / "references").glob("*.md"))
    docs.append(SCRIPTS / "studio.py")
    # 只认**总数句式**："72 条全绿 / 72 条断言 / 自检 72 条"。
    # "selfcheck 第 35/36 条" 这种序数引用不能算总数——第一版正则就是这样误伤了 4 处。
    # 分发页文案（marketplace）写的是"自检 69 条"，所以带上这个句式。
    # 两种口径分别匹配："N 条全绿/断言/..." 与 "自检 N 条"。
    # 特意**不**把 "、，。（" 加进 lookahead：那会把"批量 ≤500 条""清单 20 条"这类
    # 完全无关的数量一起抓进来（第一版就误报了 4 处）。
    claim_re = re.compile("(?<![第\\d/])(\\d{2,3})\\s*条(?=全绿|断言|回归护栏|针对真实缺陷)")
    selfcheck_re = re.compile("自检\\s*(\\d{2,3})\\s*条")
    # 一位数的补充正则：主正则只认 2~3 位数字，于是"85 条断言"被改成"9 条断言"时
    # 一处都不命中——错数字反而比正确写法更"安全"。这里只放宽位数，**lookahead 与
    # 第-lookbehind 原样保留**（"第 5 条"这类序数依旧不算总数）；英语句式同样加这个
    # lookbehind，防止把 "85" 里的 "5" 当成一条一位数声明（实测 marketplace-entry 的
    # "85 self-check assertions" 会这样误命中）。
    claim1_re = re.compile("(?<![第\\d/])(\\d)\\s*条(?=全绿|断言|回归护栏|针对真实缺陷)")
    selfcheck1_re = re.compile("自检\\s*(\\d)\\s*条")
    # "仓库体检 12 项" 指的是本文件的检查项数，口径不同，单独核。
    ci_re = re.compile("仓库体检\\s*(\\d{1,2})\\s*项")
    # 英文口径同样要核：marketplace-entry.json 的英文简介曾长期写着"69 self-check
    # assertions and 12 repo checks"，而中文简介已是 76/13——旧正则只看中文句式，
    # 于是同一份分发页里中英两半各说各话，检查却一路 PASS。
    # 「仓库级体检（15 项」/「ci_check.py -v  # 15 项」/「CI_CHECK_COUNT（当前 15 项」
    # 也是同一口径，旧正则只认「仓库体检 N 项」一种说法，于是 15→16 时这几处静默漂移。
    # 序数（「第 12 项」）已在上面的 _strip_ordinals 里剥掉，不会再被当成总数。
    ci_alt_re = re.compile(r"(?:仓库级?体检|ci_check)[^\n]{0,40}?(\d{1,2})\s*项")
    claim_en_re = re.compile("(\\d{2,3})\\s*self-check assertions?")
    claim_en1_re = re.compile("(?<![第\\d/])(\\d)\\s*self-check assertions?")
    ci_en_re = re.compile("(\\d{1,2})\\s*repo checks?")
    bad = []
    hit_docs = set()
    for doc in docs:
        if not doc.is_file():
            continue
        for ln_no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            line = _strip_ordinals(line)
            claims = (list(claim_re.finditer(line)) + list(selfcheck_re.finditer(line))
                      + list(claim_en_re.finditer(line)) + list(claim1_re.finditer(line))
                      + list(selfcheck1_re.finditer(line)) + list(claim_en1_re.finditer(line)))
            if claims:
                hit_docs.add(doc)
            for m in claims:
                n = int(m.group(1))
                # 命中即必须是真总数。旧写法用 n>=10 过滤，把"85 条"改成"9 条"后 9 被当成
                # 编号忽略，于是错数字照样 PASS；序数（"第 54 条"）由第-lookbehind 排除，
                # 不需要再靠大小来猜。
                if n != cases:
                    bad.append("%s:%d 写 %d 条（CASES 实际 %d 条）"
                               % (doc.name, ln_no, n, cases))
            for m in (list(ci_re.finditer(line)) + list(ci_en_re.finditer(line))
                      + list(ci_alt_re.finditer(line))):
                n = int(m.group(1))
                if n != CI_CHECK_COUNT:
                    bad.append("%s:%d 写仓库体检 %d 项（实际 %d 项）"
                               % (doc.name, ln_no, n, CI_CHECK_COUNT))
    # 断言总数还有两种写法上面查不到：「全 PASS（77 条）」与「（当前 85 条）」。
    # 这里用更宽但更难绕过的口径：一行只要在讲 selfcheck / 自检 / 回归，又出现 2~3 位数 + 条，
    # 那个数就必须是 CASES 真值。序数已剥掉，不会误伤「第 71、72 条」这类引用。
    ctx_re = re.compile(r"(?<![第\d/、,，])(\d{2,3})\s*条")
    for doc in docs:
        if not doc.is_file():
            continue
        for ln_no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            line = _strip_ordinals(line)
            if not any(k in line for k in ("selfcheck", "自检", "回归")):
                continue
            for m in ctx_re.finditer(line):
                n = int(m.group(1))
                if n != cases:
                    bad.append("%s:%d 在 selfcheck 语境里写 %d 条（CASES 实际 %d 条）"
                               % (doc.name, ln_no, n, cases))
    assert not bad, "；".join(sorted(set(bad))[:6])
    # "数字写错"能查，"根本不写"查不到：把条数整句删掉或写成"共八十五条"后正则一处都不
    # 命中，这项检查就在那份文档上静默失效。所以三篇必查文档每篇至少命中一次。
    # CHANGELOG 不在必查里（它本来就保留历史数字）；ci.yml / locale 也不要求写条数。
    required = [SKILL / "SKILL.md", ROOT / "README.md", ROOT / "marketplace-entry.json"]
    no_hit = [d.name for d in required if d not in hit_docs]
    assert not no_hit, (
        "%s 没写可核对的断言条数（写成中文数字或删掉都算）：等于这项检查在这份文档上没生效"
        % "、".join(no_hit))
    return ("CASES=%d 条 / ci_check=%d 项，与文档、CI 注释、分发页文案里的口径一致"
            "（SKILL.md / README.md / marketplace-entry.json 各至少 1 处）"
            % (cases, CI_CHECK_COUNT))


def t_budget_and_form_routing():
    """素材预算与 hybrid 默认形态：代码与文档口径必须一致（v2.7.0 收到的一次真实事故）。

    事故回顾：一部 6 分 25 秒的片子在问诊里答了 hybrid，但 intake 的产线路由指向
    "素材段 + 图文段按段拼接"，agent 照着命令做出一版整屏主题卡；同时全片只用了
    2 个素材源，F1..F7 一条都没有关心"画面种类够不够"。

    这一项不跑出片，只做**静态一致性**检查（裸机器可跑，不需要 ffmpeg/numpy）：
      1) footagekit 有 source_budget / source_usage，且常量 = 推荐 3/分钟、硬下限 2/分钟、绝对 3；
      2) footageqa 的 GATES 里注册了 f8_source_variety（注册漏了等于门禁没执行）；
      3) footage.py 有 --allow-sparse-sources / --allow-missing-sources 两个逃生口与 source_budget 调用；
      4) compose.py 有叠加预算审计（OVL_AREA_FAIL / audit_overlay）；
      5) intake.py 的 hybrid 路由文案指向 compose（不是 concat），且注册了 --hybrid-form；
      6) SKILL.md 的 §0.1 明确写"默认形态 3 / 形态 1 显式"。
    """
    scripts = SKILL / "scripts"
    kit = (scripts / "footagekit.py").read_text(encoding="utf-8")
    for name in ("def source_budget(", "def source_usage("):
        assert name in kit, "footagekit.py 缺 %s（素材预算的单一真源）" % name
    for const, want in (("SOURCE_PER_MIN_RECOMMENDED = 3", "推荐密度应为 3/分钟"),
                        ("SOURCE_PER_MIN_MINIMUM = 2", "硬下限应为 2/分钟"),
                        ("SOURCE_ABS_MINIMUM = 3", "绝对下限应为 3")):
        assert const in kit, "footagekit.py 的常量不对：%s" % want

    qa = (scripts / "footageqa.py").read_text(encoding="utf-8")
    assert "f8_source_variety" in qa, "footageqa.py 没有 F8 门禁函数"
    _gates = qa[qa.index("GATES = ["):qa.index("]", qa.index("GATES = ["))].replace(" ", "")
    assert "GATES=[" in _gates or "GATES=[" in qa.replace(" ", ""), "找不到 GATES 注册表"
    assert "f8_source_variety" in _gates, \
        "F8 没注册进 GATES（函数写了但没执行，等于没这门禁）"
    # 门禁清单里"最后一项"最容易漏挂：新加 F9 时只改函数不改 GATES，注册就会静默失效。
    for _need in ("f9_subtitle_fit",):
        assert _need in qa, "footageqa.py 没有 %s 门禁函数" % _need
        assert _need in qa[qa.index("GATES = ["):qa.index("]", qa.index("GATES = ["))], \
            "%s 没注册进 GATES（函数写了但没执行，等于没这门禁）" % _need

    fp = (scripts / "footage.py").read_text(encoding="utf-8")
    for flag in ("--allow-sparse-sources", "--allow-missing-sources"):
        assert flag in fp, "footage.py 缺逃生口 %s（门禁没有显式旁路就等于一刀切）" % flag
    assert "source_budget" in fp, "footage.py 没在 EDL 阶段用素材预算"

    rp = (scripts / "render.py").read_text(encoding="utf-8")
    assert "def _qa_text_layout_gate(" in rp, "render.py 没有 G13 版式冲突门禁函数"
    assert "_qa_text_layout_gate(lg" in rp, \
        "G13 没被调用（函数写了但没执行，等于没这门禁）"
    assert (scripts / "qatext.py").exists(), "缺 qatext.py（G13 的判据模块）"
    assert "text_qa_start" in (scripts / "..").joinpath("assets", "scenes.py").read_text(encoding="utf-8"), \
        "scenes.py 缺文字框采集钩子（G13 会降级成 skipped）"

    cp = (scripts / "compose.py").read_text(encoding="utf-8")
    for name in ("OVL_AREA_FAIL", "def audit_overlay(", "def layer_coverage("):
        assert name in cp, "compose.py 缺叠加预算审计部件：%s" % name

    it = (scripts / "intake.py").read_text(encoding="utf-8")
    assert "--hybrid-form" in it, "intake.py 没注册 --hybrid-form（形态 1 就无法显式选择）"
    hybrid_block = it[it.find("MODE_HYBRID: ("):]
    hybrid_block = hybrid_block[:hybrid_block.find("}")]
    assert "compose.py" in hybrid_block, \
        "hybrid 的默认走法必须指向 compose.py（素材打底 + 小面积叠加）"
    assert "concat" not in hybrid_block, \
        "hybrid 的默认走法不该是按段拼接（那是 --hybrid-form 1 的显式选择）"

    md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "--hybrid-form 1" in md, "SKILL.md 没写「形态 1 需显式 --hybrid-form 1」"

def t_layout_gate_registered():
    """G13 版式冲突 / F9 字幕排版：门禁必须真的挂上，采集钩子必须在。

    为什么单独一项：这一次的返工全部来自"门禁查不到的东西"——
    整片字压字、字幕横穿画面，而 12 道门禁全绿。新增的门禁如果只写函数不注册、
    或 scenes.py 没有采集钩子（门禁会静默降级成 skipped），等于没做。
    静态检查能在不渲染的情况下把这两类"假修复"拦住。
    """
    scripts = SKILL / "scripts"
    qa = (scripts / "footageqa.py").read_text(encoding="utf-8")
    assert "def f9_subtitle_fit(" in qa, "footageqa.py 没有 F9 字幕排版门禁"
    gates_src = qa[qa.index("GATES = ["):qa.index("]", qa.index("GATES = ["))]
    assert "f9_subtitle_fit" in gates_src, "F9 没注册进 GATES（函数写了但没执行）"

    rp = (scripts / "render.py").read_text(encoding="utf-8")
    assert "def _qa_text_layout_gate(" in rp, "render.py 没有 G13 版式冲突门禁"
    assert "_qa_text_layout_gate(lg" in rp, "G13 没被调用（函数写了但没执行）"

    assert (scripts / "qatext.py").exists(), "缺 qatext.py（G13 判据模块）"
    scenes = (SKILL / "assets" / "scenes.py").read_text(encoding="utf-8")
    for hook in ("def text_qa_start(", "def text_qa_take(", "def text_qa_role("):
        assert hook in scenes, "scenes.py 缺 %s（G13 没有数据来源）" % hook
    assert "_TEXT_QA_BOXES.append" in scenes, "_draw_text 没有真的登记文字框"
    assert "def _text_flow_kicker_y(" in scenes, "缺公共 kicker 定位（十种构图会各写一套）"
    return "G13/F9 已注册；采集钩子与公共 kicker 定位都在；qatext.py 存在"


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
        ("13 footage.json 契约", t_footage_contract),
        ("14 断言条数一致", t_case_count),
        ("15 素材预算与 hybrid 形态路由", t_budget_and_form_routing),
        ("16 版式文字冲突门禁（G13/F9 注册与采集钩子）", t_layout_gate_registered),
        ("17 包面清单完整（README 文件结构不漏文件）", t_surface_complete),
        ("18 文档命令行开关一致（--flag 真存在）", t_cli_flags),
    ]
    print("ci_check  %s" % ROOT)
    print("-" * 66)
    assert len(checks) == CI_CHECK_COUNT, (
        "checks 表有 %d 项，但 CI_CHECK_COUNT=%d：改表必须同批改常量，否则文档里的\"仓库体检 N 项\"口径就错了" % (len(checks), CI_CHECK_COUNT))
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