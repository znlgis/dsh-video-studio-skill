# 兼容性报告（DSH）

> 本文件由 `npm run compat:report`（`tools/compat_report.mjs`）**生成**，不是手写。
> 生成时间：2026-11-27 08:44:49 UTC（UTC+08:00）
> 生成器版本：@dsh-gis/dsh-video-studio-skill 3.0.0
> 宿主版本提示：null（diagnose().hostVersionHint；只认 DSH_VERSION 与 DSH_INSTALL_DIR/package.json，探不到就是 null）

判据与证据分离：package.json 的 engines.dsh 是 npm 侧判据，dsh.compatibility 是证据侧；两者不合并。

## 1 声明：dsh.compatibility

### verified（实测通过，evidence 是真实存在的校验脚本）

| DSH 版本 | 日期 | 结果 | evidence | evidence 是否存在 |
|---|---|---|---|---|
| 0.2.2-rc.5 | 2026-11-24 | pass | tools/plugin_entry_check.mjs | 是 |

### assumed（engines.dsh 声明范围内、未逐一实测的版本）

| 范围 | 原因 |
|---|---|
| >=0.1.0-rc.6 <0.2.2-rc.5 | engines.dsh 声明范围内的旧版本，未逐一实测；0.1.x 走 filesystem provider 回退路径 |
| >=0.2.2-rc.5 <0.3.0-0 | engines.dsh 声明范围内的更新版本，未逐一实测；升级前先跑 node tools/plugin_entry_check.mjs |

## 2 本轮真实执行

| 检查 | 命令 | 退出码 | 耗时 | 结果 | 输出尾行 |
|---|---|---|---|---|---|
| 插件入口 / 配置面 / 诊断冒烟 | node tools/plugin_entry_check.mjs | 0 | 0.13 s | pass | `PASS 插件入口：video-studio provider 注册成功 / list 1 个候选 / get 20807 字 / 配置面 6 类非法配置全部报错 / diagnose healthy=true（目录 219 token）` |
| 仓库体检（18 项） | python tools/ci_check.py | 0 | 6.17 s | pass | `PASS 18 / FAIL 0 / SKIP 0` |

`not-run` = 这台机器跑不了（缺 node / python / DSH 运行时），**不等于通过**；`fail` 才是结论不成立。

## 3 怎么复跑

```bash
npm run compat:report                # 重新生成本文件（覆盖）
node tools/plugin_entry_check.mjs    # 只跑入口/配置面/诊断冒烟
python tools/ci_check.py -v          # 仓库体检 18 项
npm run diagnose                     # 一行诊断（JSON；healthy=false 退 1）
```
