# 更新日志

版本号规则：`主.次.修订`；技能正文（SKILL.md / references / scripts / assets）有实质改动才升次级。

## 0.1.0

- 首个版本：把 `dsh-v/02-技能包-video-studio` 打包成 DSH 技能插件。
- `index.js` 通过 `@deepseek-ai/dsh-skill-filesystem` 把包内 `skills/` 挂成 bundled 技能根（rank 600），不写用户目录、不受手工技能目录污染。
- `cordis.patch.yml` 插入 `video-studio` 行；`locale/en.json`、`locale/zh.json` 提供插件页标题与描述；`icon.svg` 为原创图标。
- 技能正文 10 个文件与源包 MD5 一致，仅两处必要修订：
  - 五步上手补 `<SKILL_DIR>` 的解析说明（避免模型猜路径）；
  - 第 10 节标题与开头改为"作者机器实测基准"，并给出换机 5 分钟重测清单。
