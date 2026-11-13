# -*- coding: utf-8 -*-
"""intake.py — 出片前的一次性问诊：把"只有用户能决定的事"一次问完。

为什么要有这一层
----------------
提案层（propose.py）解决的是"外观由谁拍板"，但它默认**已经知道**题材、时长、气质、
声音这些输入。实际会话里这些信息常常只存在于用户的半句话里，agent 要么不问直接猜，
要么问一句挤一句 —— 结果是"有时一个不问，有时只问一两个"，用户到成片才第一次看到
自己的片子长什么样，返工全落在渲染之后。

所以把问诊固定成**一张表**：六个问题，一次问完，每个问题都带推荐项（推荐项 = 明亮、
活泼、生动的方向），用户的回答写进 work/intake.json，propose.py 读它合成 brief。
脚本自己不弹窗（CLI 里没有 UI），它只负责"问什么、怎么问、答案放哪、怎么变成 brief"，
真正的提问由会话里的 ask_user_question 照生成的 JSON 执行。

三个产物
--------
    work/intake.json          题目 + 答案 + 是否已确认（唯一真源）
    work/intake.brief.txt     由答案合成的 --brief 文本（可直接喂 propose.py）
    stdout                    人读的题目单 / 已填答案单

用法
----
    # 1) 生成题目（给 agent 拿去问；--json 机读）
    python intake.py --work work --questions
    python intake.py --work work --questions --json

    # 2) 收集答案（每个 --set 一次；id:value，value 用逗号分隔多选）
    python intake.py --work work --set topic:"AI 大模型的三年" --set duration:45s \
        --set tone:bright-lively --set voice:female-warm --set music:upbeat \
        --set format:1920x1080 --set note:"结尾放一个二维码"

    # 3) 全部答完 -> 校验并合成 brief
    python intake.py --work work --finalize        # 写 intake.brief.txt
    python intake.py --work work --check           # 只校验，退出码 2 = 还有没答的

    # 4) 什么都没答也要能干活（明确 --defaults 才会用推荐项补齐，绝不静默）
    python intake.py --work work --defaults
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SCHEMA = 1
INTAKE_FILE = "intake.json"
BRIEF_FILE = "intake.brief.txt"

# 六个问题就是"六个决策"。顺序固定：
#   先定"给谁看什么"（题材 / 时长），再定"长什么样"（气质 / 画幅），
#   最后定"听着像什么"（声线 / 配乐）—— 从内容到形式，复述时用户能顺着读一遍。
#
# 每个选项的 brief 字段：propose._brief_of 认的是自然语言关键词，
# 所以 value 直接写成它认得的中文说法（例如"深蓝""女声""硬切"），
# 不要在这里另造一套代号再翻译一次——两套映射迟早漂移。
QUESTIONS = (
    {
        "id": "topic",
        "header": "题材",
        "question": "这条片子讲什么？（一句话：题材 + 受众；例如「AI 大模型的三年，讲给普通观众」）",
        "kind": "text",
        "required": True,
        "options": [],
        "hint": "越具体越好：写清题材、讲给谁、想让人记住什么。",
    },
    {
        "id": "duration",
        "header": "时长",
        "question": "成片大约多长？",
        "kind": "single",
        "required": True,
        "default": "45s",
        "options": [
            {"value": "30s", "label": "30 秒（推荐：口播 6–8 句，最稳）", "brief": "30秒"},
            {"value": "45s", "label": "45 秒（推荐：起承转合齐全）", "brief": "45秒"},
            {"value": "60s", "label": "1 分钟", "brief": "1分钟"},
            {"value": "90s", "label": "1 分半", "brief": "一分半"},
            {"value": "120s", "label": "2 分钟", "brief": "2分钟"},
            {"value": "auto", "label": "按台词实测长度（不设目标）", "brief": ""},
        ],
        "hint": "只写目标；成片长度仍由旁白实测时长决定，不会截断台词。",
    },
    {
        "id": "tone",
        "header": "主题气质",
        "question": "想要什么气质？（决定了配色明暗、活力与装饰密度）",
        "kind": "single",
        "required": True,
        "default": "bright-lively",
        # 顺序就是推荐顺序：默认项在最前，且**任何题材的推荐项都是明亮活泼**。
        "options": [
            {"value": "bright-lively", "label": "明亮活泼（推荐）：浅底、高饱和、有活力", "brief": "明亮活泼"},
            {"value": "bright-clean", "label": "明亮干净：浅底、低饱和、留白多", "brief": "明亮"},
            {"value": "bright-warm", "label": "明亮温暖：暖色浅底，亲和", "brief": "明亮，暖纸"},
            {"value": "deep-focus", "label": "深色专注：暗底、克制（发布会/技术向）", "brief": "深色"},
            {"value": "auto", "label": "你替我挑（仍会先给方案看）", "brief": ""},
        ],
        "hint": "选「你替我挑」也会先出候选方案，不会跳过看图。",
    },
    {
        "id": "format",
        "header": "画幅",
        "question": "画幅与帧率？",
        "kind": "single",
        "required": False,
        "default": "1080p30",
        "options": [
            {"value": "1080p30", "label": "1920×1080 / 30fps（推荐）", "brief": ""},
            {"value": "1080p24", "label": "1920×1080 / 24fps（电影感）", "brief": ""},
            {"value": "720p24", "label": "1280×720 / 24fps（更快、更省）", "brief": ""},
        ],
        "hint": "只做 16:9 横屏、只能下采样；选完写进 film.json 的 video 块。",
    },
    {
        "id": "voice",
        "header": "旁白声线",
        "question": "谁来念？（声线决定整片的语气）",
        "kind": "single",
        "required": True,
        "default": "female-bright",
        "options": [
            {"value": "female-bright", "label": "明亮女声（推荐）：轻快、有精神", "brief": "女声"},
            {"value": "male-bright", "label": "明亮男声：青年、清爽", "brief": "男声"},
            {"value": "female-warm", "label": "温暖女声：柔、慢一点", "brief": "女声"},
            {"value": "male-steady", "label": "沉稳男声：纪录片口吻", "brief": "男声"},
            {"value": "auto", "label": "你替我挑", "brief": ""},
        ],
        "hint": "具体音色由设计里的 voice 决定；这里只定方向（性别/语气）。",
    },
    {
        "id": "music",
        "header": "配乐",
        "question": "配乐要多热闹？",
        "kind": "single",
        "required": True,
        "default": "upbeat",
        "options": [
            {"value": "upbeat", "label": "轻快上扬（推荐）：BPM ~118，收束明亮", "brief": "上扬"},
            {"value": "hopeful", "label": "温暖向上：BPM ~92，叙事感", "brief": "希望"},
            {"value": "steady", "label": "稳稳推进：BPM ~104，中性", "brief": "稳"},
            {"value": "driving", "label": "强驱动：BPM ~124，节奏感强", "brief": "推进"},
            {"value": "quiet", "label": "克制留白：BPM ~78，配乐很轻", "brief": "平静"},
            {"value": "auto", "label": "你替我挑", "brief": ""},
        ],
        "hint": "配乐由 musicgen 按参数现场编排，没有现成曲风可选。",
    },
    {
        "id": "note",
        "header": "补充要求",
        "question": "还有别的硬要求吗？（可留空：必须出现/不要出现的东西，例如「不要放射线」「结尾放二维码」）",
        "kind": "text",
        "required": False,
        "options": [],
        "hint": "「不要 X」会进 avoid，「要 X」会进 must_have；认不出的词会被原样提示，不会静默丢弃。",
    },
)

_BY_ID = {q["id"]: q for q in QUESTIONS}


def _setup_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                     # noqa: BLE001
            pass


def intake_path(work):
    return os.path.join(str(work), INTAKE_FILE)


def brief_path(work):
    return os.path.join(str(work), BRIEF_FILE)


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        raise SystemExit("[intake] %s 不是合法 JSON：%s" % (path, e)) from e


def _atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def question_payload():
    """给会话层用的题目包：id / question / options(label,value) / default。"""
    rows = []
    for q in QUESTIONS:
        row = {"id": q["id"], "header": q.get("header") or "", "question": q["question"],
               "multi_select": False, "required": bool(q.get("required")),
               "hint": q.get("hint") or ""}
        if q.get("options"):
            row["options"] = [{"label": o["label"], "value": o["value"]} for o in q["options"]]
        if q.get("default"):
            row["default"] = q["default"]
        rows.append(row)
    return rows


def _default_answers():
    return {q["id"]: q["default"] for q in QUESTIONS
            if q.get("default") and q["kind"] != "text"}


def _norm_value(q, raw):
    """把用户答案规整成题目的取值：认选项 value，也认选项 label 的原话。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if q["kind"] == "text":
        return text
    for o in q.get("options") or ():
        if text == o["value"]:
            return o["value"]
    low = text.lower()
    for o in q.get("options") or ():
        if low == str(o["value"]).lower() or text == o["label"]:
            return o["value"]
    # 也接受用户的原话（"明亮活泼" / "女声"）——落到第一个 brief 命中该词的选项。
    for o in q.get("options") or ():
        if o.get("brief") and o["brief"] and o["brief"] in text:
            return o["value"]
    raise SystemExit("[intake] %s=%r 不是合法选项；可选：%s"
                     % (q["id"], raw, " / ".join(o["value"] for o in q.get("options") or ())))


def _apply(data, qid, raw):
    q = _BY_ID.get(qid)
    if q is None:
        raise SystemExit("[intake] 没有这个问题：%r；只有 %s"
                         % (qid, " / ".join(q["id"] for q in QUESTIONS)))
    val = _norm_value(q, raw)
    data.setdefault("answers", {})[qid] = val
    return val


def _missing(data):
    """必答题里还没答（或答成空）的项。题目上的 required 才是判据，别在别处再抄一份。"""
    answers = data.get("answers") or {}
    return [q["id"] for q in QUESTIONS
            if q.get("required") and not str(answers.get(q["id"]) or "").strip()]


def brief_text(data):
    """答案 -> 一句 --brief 文本（propose._brief_of 解析的输入）。

    规则：题材原样保留（它是 topic）；有 brief 片段的选项按"题材，片段，片段…"拼；
    补充要求原样追加（含「不要 X」时 _brief_of 会归到 avoid）。
    """
    answers = data.get("answers") or {}
    parts = []
    topic = str(answers.get("topic") or "").strip()
    if topic:
        parts.append(topic)
    for q in QUESTIONS:
        if q["id"] in ("topic", "note"):
            continue
        val = str(answers.get(q["id"]) or "").strip()
        if not val:
            continue
        for o in q.get("options") or ():
            if o["value"] == val:
                if o.get("brief"):
                    parts.append(o["brief"])
                break
    note = str(answers.get("note") or "").strip()
    if note:
        parts.append(note)
    return "，".join(p for p in parts if p)


def save(work, data):
    data["schema"] = SCHEMA
    _atomic_write(intake_path(work), json.dumps(data, ensure_ascii=False, indent=1))
    return intake_path(work)


def _human(data, work):
    answers = data.get("answers") or {}
    print("== 问诊单（%s）" % intake_path(work))
    for q in QUESTIONS:
        val = str(answers.get(q["id"]) or "").strip()
        mark = "OK " if val else ("?? " if q.get("required") else "-- ")
        show = val or "（未答）"
        if q.get("options") and val:
            for o in q.get("options") or ():
                if o["value"] == val:
                    show = "%s [%s]" % (o["label"], val)
                    break
        print("  %s%-8s %s" % (mark, q["id"] + ":", show))
    miss = _missing(data)
    print("  -> %s" % ("还有 %d 项没答：%s" % (len(miss), " / ".join(miss))
                       if miss else "已答完，可以 --finalize 合成 brief"))
    print("  brief: %s" % (brief_text(data) or "（空：题材都没给）"))


def main(argv=None):
    _setup_stdio()
    ap = argparse.ArgumentParser(description="video-studio 出片前问诊（一次问完，答案落盘）")
    ap.add_argument("--work", default="work")
    ap.add_argument("--questions", action="store_true", help="打印题目（给 ask_user_question 用）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（stdout）")
    ap.add_argument("--set", action="append", default=[], metavar="ID:VALUE",
                    help="记录一条答案，可重复：--set topic:\"AI 大模型\" --set tone:bright-lively")
    ap.add_argument("--defaults", action="store_true",
                    help="用推荐项补齐未答的选项题（明确调用才生效，绝不静默）")
    ap.add_argument("--finalize", action="store_true", help="校验并写 intake.brief.txt")
    ap.add_argument("--check", action="store_true", help="只校验：还没答完退 2")
    ap.add_argument("--show", action="store_true", help="看当前答案")
    a = ap.parse_args(argv)

    try:
        if a.questions:
            payload = {"schema": SCHEMA, "work": os.path.abspath(a.work),
                       "instructions": ("把 questions 里的每一题都用 ask_user_question 一次问完；"
                                        "多选题不要拆成多轮。答完用 --set 写回，再 --finalize。"),
                       "questions": question_payload()}
            if a.json:
                print(json.dumps(payload, ensure_ascii=False, indent=1))
            else:
                print("把下面每一题都用 ask_user_question **一次**问完（不要分多轮）：")
                for i, q in enumerate(question_payload(), 1):
                    print("\n%d) [%s] %s" % (i, q["id"], q["question"]))
                    if q.get("hint"):
                        print("   %s" % q["hint"])
                    for o in q.get("options") or ():
                        star = "  <- 推荐" if q.get("default") == o["value"] else ""
                        print("     - %s%s" % (o["label"], star))
            return 0

        data = _load(intake_path(a.work)) or {"schema": SCHEMA, "work": os.path.abspath(a.work),
                                              "answers": {}, "status": "draft"}
        if a.set:
            for item in a.set:
                if ":" not in item:
                    raise SystemExit("[intake] --set 要用 ID:VALUE，收到 %r" % (item,))
                qid, raw = item.split(":", 1)
                _apply(data, qid.strip(), raw)
            print("[intake] 已记录 %d 条答案" % len(a.set))
        if a.defaults:
            got = 0
            for q in QUESTIONS:
                if q["kind"] == "text" or not q.get("default"):
                    continue
                if not str((data.get("answers") or {}).get(q["id"]) or "").strip():
                    _apply(data, q["id"], q["default"])
                    got += 1
            print("[intake] 用推荐项补齐了 %d 项（--defaults）" % got)
        miss = _missing(data)
        data["missing"] = miss
        data["brief"] = brief_text(data)
        data["status"] = "complete" if (not miss and str(data.get("answers", {}).get("topic") or "").strip()) else "draft"
        save(a.work, data)

        if a.check:
            if data["status"] != "complete":
                print("[intake] 还没答完（%s）：%s"
                      % (len(miss), " / ".join(miss) or "缺题材"), file=sys.stderr)
                return 2
            if a.json:
                print(json.dumps(data, ensure_ascii=False, indent=1))
            return 0
        if a.finalize:
            if data["status"] != "complete":
                print("[intake] 不能定稿：还没答完（%s）：%s"
                      % (len(miss), " / ".join(miss) or "缺题材"), file=sys.stderr)
                return 2
            txt = data["brief"]
            _atomic_write(brief_path(a.work), txt)
            data["briefPath"] = brief_path(a.work)
            save(a.work, data)
            if a.json:
                print(json.dumps(data, ensure_ascii=False, indent=1))
            else:
                print("== 已定稿")
                print("  brief: %s" % txt)
                print("  -> 下一步：propose.py --brief \"%s\" --script film.json --work %s"
                      % (txt, a.work))
            return 0
        if a.json:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        elif a.show or a.set or a.defaults:
            _human(data, a.work)
        else:
            _human(data, a.work)
        return 0
    except SystemExit:
        raise
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit("[intake] %s: %s" % (type(e).__name__, e)) from e


if __name__ == "__main__":
    sys.exit(main())
