/**
 * @dsh-gis/dsh-video-studio-skill — DeepSeek Harness 技能插件
 *
 * 用代码从零生成中文视频：剧本 → edge-tts 旁白 → 实测时长排时间轴 →
 * 逐帧渲染 → ffmpeg 编码 → numpy 采样级混音 → 自动质检。
 *
 * 启动时把包内 skills/ 挂成 bundled 技能根（rank 600），不改动
 * ~/.dsh/skills，也不写任何用户目录；卸载插件即整体消失。
 *
 * 依赖说明：`@deepseek-ai/dsh-skill-filesystem` 在 package.json 里是**可选 peer**
 * （peerDependenciesMeta.optional）。因此这里**不能**用静态 import——那样在缺依赖的
 * profile 里整个插件行会加载失败，连错误都只能在 loader 日志里看到。
 * 改成 apply 内按需 import：缺依赖时打一条能照着做的日志，本行插件照常加载。
 */
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

/** 包内技能根：<pkg>/skills/<name>/SKILL.md */
const bundledSkillDir = fileURLToPath(new URL("./skills/", import.meta.url));
/** 技能名（与 skills/video-studio/SKILL.md 的 frontmatter name 一致） */
const SKILL_NAME = "video-studio";
/** 提供 bundled 技能根的能力包（可选 peer，缺了本插件只是个空行）。 */
const PROVIDER_PKG = "@deepseek-ai/dsh-skill-filesystem";

/** Cordis 插件名（与 cordis.patch.yml 的 row id 一致）。 */
export const name = "video-studio";
/** 依赖 DSH 技能注册表。 */
export const inject = ["skills"];

/**
 * 注册 bundled 技能根。
 *
 * 三件事任何一件不成立都只打日志、不抛错——插件本身没有"必须成功"的业务，
 * 挂不上技能是降级，不是崩溃：
 *   1. 找不到包内 SKILL.md（打包漏了文件 / 被裁剪）
 *   2. 运行时没有 ctx.skills（profile 没装技能注册表）
 *   3. 可选 peer 没装（只装了本插件）
 *
 * @param ctx - 携带 skills 服务的 Cordis 上下文。
 */
export async function apply(ctx) {
  const manifest = join(bundledSkillDir, SKILL_NAME, "SKILL.md");
  if (!existsSync(manifest)) {
    warn(ctx, `找不到 ${manifest}，本次不挂载技能（检查包内容是否完整）`);
    return;
  }
  if (!ctx?.skills || typeof ctx.skills.registerProvider !== "function") {
    warn(ctx, "运行时没有 skills 服务，本次不挂载技能（profile 需要 @deepseek-ai/dsh-skill）");
    return;
  }

  let applyFilesystemProvider;
  try {
    ({ apply: applyFilesystemProvider } = await import(PROVIDER_PKG));
  } catch (error) {
    warn(
      ctx,
      `缺少可选依赖 ${PROVIDER_PKG}，本次不挂载技能。` +
        `安装（版本需满足 peer 范围）：dsh plugin --profile <profile> add ${PROVIDER_PKG}` +
        `（${error?.code ?? error?.message ?? error}）`,
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

/**
 * 打一条降级日志。
 *
 * Cordis 的 logger 是可选注入（不同版本形态不同：有的是 logger 服务、有的只有
 * console 桥接），这里按能力探测，永不因为"日志打不出来"而让插件加载失败。
 *
 * @param ctx - Cordis 上下文。
 * @param message - 面向用户的说明（中文，带下一步动作）。
 */
function warn(ctx, message) {
  const text = `[video-studio] ${message}`;
  for (const logger of [ctx?.logger, ctx?.root?.logger]) {
    try {
      if (typeof logger?.warn === "function") {
        logger.warn(text);
        return;
      }
    } catch {
      /* 日志失败不归插件管，落到下面的 console 兜底 */
    }
  }
  console.warn(text);
}
