# -*- coding: utf-8 -*-
"""reviewbrief.py — 评审任务单（REVIEWER-BRIEF.md）的唯一真源。

为什么单独一个模块而不是塞进 reviewpacket.py：
  * reviewpacket.py 产的是「给评审者看的清单」（PACKET.md）；
  * 本模块产的是「给派评审的人看的操作单」（范围 / 5 问 / 输出格式 / 时限）。
两者受众不同，混在一起时最容易腐化的就是「时限与范围」——那是唯一能让评审
按时收尾的东西，实测没有它时评审者会逐张看完 40 张帧、40 分钟不返回。

规则来源：v3.1.0 前的真实会话。两个干净上下文评审者，一个 45 分钟零输出被中断，
一个收敛到「联系表 + 4 张关键帧 + 5 个问题」后 15 分钟给出完整结论。
把那次奏效的收敛写死成产物，下一次不用再赌。
"""
from __future__ import annotations

BRIEF_BODY = """## 你要看的（就这些，不要逐张扫 40 张）

1. `_sheet.png` —— 每行一段，左 enter / 右 settle，一张图看全片
2. 再从 `frames/` 挑 4 张细看：开场 settle、数字卡 settle、章节卡 settle、收尾 settle
3. `out/QC-REPORT.md` —— 机器门禁矩阵（pass / fail / skipped 三档）
4. 剧本原文（`film.json`）—— 核对旁白说的和画面写的是不是一回事

## 必须回答的 5 问（每条都要给帧文件名或报告行做证据）

- Q1 画面：有没有文字出画、互相压盖、字幕带被正文占用？
- Q2 一致性：同一章节内版式是否真的在换？有没有同质化到分不清段？
- Q3 可读性：最小字号在目标宽度下可读吗？主次清楚吗？
- Q4 门禁：有 fail 吗？skipped 是哪些、能不能接受（**skipped 不等于通过**）？
- Q5 事实：片上每个数字/年份，能否在给定出处里找到？逐条写「有 / 无」。

## 输出格式（必须按这个回）

```text
结论：可交付 / 有阻断项（二选一）
Q1..Q5 各一段，每条附证据
阻断项清单（每条：证据 + 要改什么）
无法验证的项（每条：缺什么证据）
```

缺证据的项写「无法验证」并说明缺什么。不要写「整体感觉不错」。
时限建议：15 分钟。超时先交结论，把没看的项写进「无法验证」。
"""


def brief_markdown(manifest=None):
    """生成 REVIEWER-BRIEF.md 正文（给派评审的人的操作单）。"""
    m = manifest or {}
    L = []
    L.append("# 评审任务单 — %s" % (m.get("title") or "（未命名）"))
    L.append("")
    L.append("> 把 `PACKET.md` 与本文件一起发给评审者；只给它这个目录，")
    L.append("> 不要给它制作过程、不要给它辩解。")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 内容指纹 | %s |" % (m.get("contentHash") or "-"))
    L.append("| 段数 | %d |" % int(m.get("shotCount") or 0))
    L.append("| 验收帧 | %d 张（每段 enter / settle 各一张） |" % int(m.get("reviewFrameCount") or 0))
    L.append("| 建议时限 | 15 分钟 |")
    L.append("")
    L.append(BRIEF_BODY.rstrip())
    L.append("")
    return "\n".join(L)
