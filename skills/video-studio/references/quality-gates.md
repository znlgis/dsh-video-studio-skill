# 质量门禁（G0–G6）

Agent 看不见画面、听不见声音，所以**验收必须脚本化 + 静帧目检**双轨。

## G0 内容门禁：上屏数字可溯源

| 判据 | 做法 |
|---|---|
| 每个上屏的数字/年份对得回编号 | 建 MARK 表：数字 → 出处链接 |
| 无出处不上屏 | 单独黑名单文件，写清"为什么不行、实际是什么" |
| 口径一致 | 同一数字在剧本/字幕/画面里表述完全相同 |
| 因果表述正确 | 人工复核（这类错误最容易被评论区抓） |

真实收益：75 条主张 + 25 条黑名单，抓出 8 处硬伤。

## G1 帧级门禁（渲染期）

| 判据 | 阈值 | 怎么查 |
|---|---|---|
| 确定性 | 0 处 random/time() | grep 统计 |
| 无状态 | 模块级只缓存静态几何 | 代码评审 |
| 单帧预算 | ≤ 150 ms（浏览器）/ ≤ 25 ms（PIL） | 代码里计时并打印 fps |
| 段尾停住 | 最后 2 s 两帧逐像素相同（关掉噪声后） | 断言 |
| 元素存在性 | 该有的标题/淡入都出现 | **最难，见下** |

**"元素存在性"是分水岭**：画面"能出图"时，最难发现的是"该有但没有"。字段改名（`act.t0` → `act.start`）后，全片十幕淡入与图名框静默失效，零报错。解法：改名后必须跑 `snap` 抽帧对比。

## G2 静帧门禁（每段交付前）

```bash
python -c "
import filmkit as K, scenes
from filmkit import snap
for t in (0.6, 3.0, 6.5):
    img = scenes.draw_frame(scene, t, 8.0, t, script, int(t*30))
    img.save('qa/scene_%s.png' % t)
"
```

必须检查四件事：没有空白帧、没有元素出画、字幕没被挡、构图有重心。
交付报告里要写"**已用 read_image 亲眼确认**"+ 预览文件名。这是把"agent 看不见"转成流程约束。

## G3 音频门禁

| 判据 | 阈值 |
|---|---|
| 单条音效 QC | 质心 < 3600 Hz、8k 占比 < 4.5%、2–6k 占比 < 34%、峰均比 < 32、直流 < 1.5e-3 |
| 同 seed 双渲染 | 逐采样相同 |
| 无 NaN/Inf | 0 |
| 循环无缝 | 接缝跳变比 < 1.5 |
| 全程有声 | 每 20 s 窗口 RMS > 0.002 |
| 旁白突出 | 旁白窗 vs 间隙窗 RMS 差 ≥ 12 dB |
| 母带 | -16 ± 0.5 LUFS，真峰 ≤ -1 dBTP |

## G4 成片门禁（可复制）

```python
errors, warns = [], []
pr = K.probe(film); f = pr["format"]

# 容器
assert f["duration"] > 0
v = [s for s in pr["streams"] if s["codec_type"] == "video"][0]
assert (v["width"], v["height"]) == (W, H)
assert any(s["codec_type"] == "audio" for s in pr["streams"]), "无音轨"

# 时长
assert abs(float(f["duration"]) - frames / FPS) < 0.5

# 响度
lo = K.loudness(film)
assert -17.5 <= lo["I"] <= -14.5, lo
assert lo.get("TP", 0) <= -0.5, lo

# 锐度比（编码损失）——比码率下限可靠
q = K.encode_quality(film, ref_png, at_sec=dur*0.3)
assert q["ratio"] >= 0.85, q

# 黑帧（首尾之外）
assert K.detect_black(film) == 0
```

## G5 完整性与一致性

| 判据 | 做法 |
|---|---|
| 分块无缺口 | 按**绝对起始帧**命名，拼接前列出帧号区间找洞 |
| 字幕条数 = 句数 | `len(scenes) == len(script["lines"])` |
| 字幕时间不交叠 | `sub_window` 已保证，但仍要断言 |
| 末条字幕 ≤ 总时长 | 断言 |
| 上屏数字都在 MARK 表 | 解析画面文字与 FACTS 交集 |

## G6 交付清单

```
out/
  film.mp4            成片（CRF18 / -16 LUFS / faststart）
  film.srt            外挂字幕
  stills/             6–8 张剧照
  sheet.png           全片联系表
  QC-REPORT.md        上述所有判据的实测值
```

**交付文档要写实测值，不要写"已完成"**：
```
时长 30.37 s / 1920x1080 / h264+aac / 0.58 Mbps
响度 -16.6 LUFS / LRA 2.4
锐度比 0.93（原始帧 1204 vs 解码 1120）
字幕 4 条 = 剧本 4 句
黑帧 0 / 静音 0
\`\`\`

## 编码质量门禁的实测依据

同一个画面、三种导出方式（本机实测，1080p30，90 帧）：

| 方式 | 耗时 | 速度 | 说明 |
|---|---|---|---|
| PIL → rawvideo 管道 → libx264 | 0.89 s | **100 fps** | 推荐 |
| PIL → cv2.VideoWriter(mp4v) → 重编码 | 1.71 s | 52 fps | 二次有损，慢一倍 |
| PIL → PNG image2pipe → libx264 | 2.76 s | 33 fps | PNG 编码开销大 |

所以默认走 rawvideo 直通；PNG 只在需要无损中间件时用。

## 编码质量门禁的实测依据

同一画面、三种导出方式（本机实测，1080p30，90 帧，payload 为渐变+卡片+文字）：

| 方式 | 耗时 | 速度 | 结论 |
|---|---|---|---|
| PIL → rawvideo 管道 → libx264 | 0.89 s | **100 fps** | 推荐 |
| PIL → cv2.VideoWriter(mp4v) → 重编码 | 1.71 s | 52 fps | 二次有损，慢一倍 |
| PIL → PNG image2pipe → libx264 | 2.76 s | 33 fps | PNG 编码开销大 |

默认走 rawvideo 直通；PNG 只在需要无损中间件时用。

## 码率不是质量指标

暗色简洁内容用 CRF18 出来只有 0.5–1 Mbps，这是正常的——x264 在低细节区域不需要更多比特。真实项目里踩过的坑：一条 1080p30 科普片用 `cv2.VideoWriter` 中间层 + CRF19 重编码，成片 1.1 Mbps，文字明显发软。

**用"锐度比"替代码率下限**：

```python
q = K.encode_quality("out/film.mp4", "work/qa/snap.png", at_sec=12.0)
# {'sharp_ref': 1204.0, 'sharp_dec': 1120.3, 'ratio': 0.9305}
```

- ratio ≥ 0.92：好
- 0.85–0.92：可接受（文字可能略软）
- < 0.85：**必须降 CRF 或提 preset**
