/**
 * @dsh-gis/dsh-video-studio-skill — DeepSeek Harness 技能插件
 *
 * 用代码从零生成中文视频：剧本 → edge-tts 旁白 → 实测时长排时间轴 →
 * 逐帧渲染 → ffmpeg 编码 → numpy 采样级混音 → 自动质检。
 *
 * 启动时把包内 skills/ 挂成 bundled 技能根（rank 600），不改动
 * ~/.dsh/skills，也不写任何用户目录；卸载插件即整体消失。
 */
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { apply as applyFilesystemProvider } from "@deepseek-ai/dsh-skill-filesystem";

/** 包内技能根：<pkg>/skills/<name>/SKILL.md */
const bundledSkillDir = fileURLToPath(new URL("./skills/", import.meta.url));
/** 技能名（与 skills/video-studio/SKILL.md 的 frontmatter name 一致） */
const SKILL_NAME = "video-studio";

/** Cordis 插件名（与 cordis.patch.yml 的 row id 一致）。 */
export const name = "video-studio";
/** 依赖 DSH 技能注册表。 */
export const inject = ["skills"];

/**
 * 注册 bundled 技能根。
 * @param ctx - 携带 skills 服务的 Cordis 上下文。
 */
export function apply(ctx) {
  const manifest = join(bundledSkillDir, SKILL_NAME, "SKILL.md");
  if (!existsSync(manifest)) {
    ctx.logger?.warn?.(
      `@dsh-gis/dsh-video-studio-skill: 找不到 ${manifest}，本次不挂载技能。`,
    );
    return;
  }

  applyFilesystemProvider(ctx, {
    providerName: "video-studio",
    includeDefaultRoots: false,
    bundledSkillDir,
    watch: false,
  });
}
