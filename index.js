/**
 * @dsh-gis/dsh-video-studio-skill — DeepSeek Harness 技能插件
 *
 * 用代码从零生成中文视频：剧本 → edge-tts 旁白 → 实测时长排时间轴 →
 * 逐帧渲染 → ffmpeg 编码 → numpy 采样级混音 → 自动质检。
 *
 * 启动时把包内 skills/ 挂成技能提供方（provider），不改动 ~/.dsh/skills，
 * 也不写任何用户目录；卸载插件即整体消失。
 *
 * ## 为什么自带 provider，而不是复用 @deepseek-ai/dsh-skill-filesystem
 *
 * DSH 0.2.x 的技能注册接口是 ctx.skills.registerProvider(create)：create 收到
 * { signal, invalidate }，同步返回一个 { name, list, get } 形式的 provider。
 * 老版本（0.1.x）才提供 dsh-skill-filesystem 的 apply(ctx, config) 形态。
 *
 * 以前这里只走老形态，而且是拿一个可选 peer 去调它。在 DSH 0.2.2-rc.5 上实测：
 * 插件包所在目录解析不到那个包（运行时自带的那份在 app.asar 里，插件看不到），
 * 于是 import 抛 ERR_MODULE_NOT_FOUND -> 打一条「缺少可选依赖」的日志后静默降级：
 * registerProvider 调用次数 0、技能一个都没挂上，而插件行看上去完全正常。
 *
 * 所以现在：
 *   1) 有 ctx.skills.registerProvider 就注册自带 provider（0.2.x，主路径）；
 *   2) 没有才回退到老的 filesystem provider（0.1.x，兼容路径）。
 * 主路径不 import 任何 @deepseek-ai/* 运行时包，装到哪种 DSH 都能自己跑起来。
 *
 * 自带 provider 的语义对齐官方 dsh-skill-filesystem 的 bundled 根：
 *   - list 只读每个 SKILL.md 的 YAML frontmatter（正文按需在 get 里读）；
 *   - rank = 600（BUNDLED_SKILL_RANK：打包技能根的标准优先级）；
 *   - resourceBase = 技能目录，保证 SKILL.md 里的相对引用能解析；
 *   - frontmatter 非法只告警并跳过该技能，绝不抛错——挂不上技能是降级，不是崩溃。
 */
import { readFile, readdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** 包内技能根：<pkg>/skills/<name>/SKILL.md */
const bundledSkillDir = fileURLToPath(new URL("./skills/", import.meta.url));
/** 老版本（0.1.x）才需要的 provider 包；缺失不影响 0.2.x 主路径。 */
const LEGACY_PROVIDER_PKG = "@deepseek-ai/dsh-skill-filesystem";
/** 打包技能根的 rank（与 dsh-skill 的 BUNDLED_SKILL_RANK 同值）。 */
const BUNDLED_SKILL_RANK = 600;
/** 技能名语法（与 @deepseek-ai/dsh-skill 的 SKILL_NAME 同口径；注册表按它校验候选）。 */
const SKILL_NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
/** 本 provider 在 ctx.skills 里的名字（"runtime" 是注册表保留名，不能用）。 */
const PROVIDER_NAME = "video-studio";

/** Cordis 插件名（与 cordis.patch.yml 的 row id 一致）。 */
export const name = "video-studio";
/** 依赖 DSH 技能注册表。 */
export const inject = ["skills"];

/**
 * 注册 bundled 技能根。
 *
 * 任何一步失败都只打日志、不抛错——插件本身没有「必须成功」的业务，
 * 挂不上技能是降级，不是崩溃。
 *
 * @param ctx - 携带 skills 服务的 Cordis 上下文。
 */
export function apply(ctx) {
  const registry = ctx?.skills;
  if (typeof registry?.registerProvider === "function") {
    try {
      registry.registerProvider(() => createBundledProvider(ctx));
    } catch (error) {
      warn(ctx, "注册技能 provider 失败（本次只是空行）：" + errorText(error));
    }
    return;
  }
  // 0.1.x 形态：没有 registerProvider 时才走老的 filesystem provider。
  // apply 必须同步返回，所以异步工作挂到浮空 Promise 上，并保证 reject 不会变成
  // unhandledRejection（Node >=15 默认让进程退出，"挂不上技能只是降级"就变成崩进程）。
  applyLegacy(ctx).catch((error) => {
    warn(ctx, "回退到老版 filesystem provider 失败（本次只是空行）：" + errorText(error));
  });
}

/**
 * 自带 provider：发现并加载 package 内 skills/ 下的技能。
 *
 * 发现阶段只读 frontmatter，get 阶段才读全文——与官方 provider 同口径，
 * 保证 list 在会话启动时不会因为技能正文很大而变慢。
 *
 * @param ctx - Cordis 上下文（只用于日志）。
 * @returns 符合 dsh-skill SkillProvider 契约的对象。
 */
function createBundledProvider(ctx) {
  return {
    name: PROVIDER_NAME,
    async list(options) {
      options?.signal?.throwIfAborted();
      const candidates = [];
      let entries;
      try {
        entries = await readdir(bundledSkillDir, { withFileTypes: true });
      } catch (error) {
        warn(ctx, "读不到技能根 " + bundledSkillDir + "：" + errorText(error) + "（本次不提供技能）");
        return candidates;
      }
      for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        const dir = join(bundledSkillDir, entry.name);
        const file = join(dir, "SKILL.md");
        let parsed;
        try {
          parsed = parseSkillFile(await readFile(file, "utf8"));
        } catch (error) {
          // 技能目录被裁剪/漏打包时 ENOENT 属正常，静默跳过；其它错误（权限、坏编码）才告警。
          if (error?.code !== "ENOENT") {
            warn(ctx, "跳过技能 " + entry.name + "：" + errorText(error));
          }
          continue;
        }
        if (parsed === undefined) {
          warn(ctx, "跳过技能 " + entry.name + "：SKILL.md 缺少合法 frontmatter"
            + "（需要合法 name（小写 kebab-case）与 description；invocation 策略的取值也要合法）");
          continue;
        }
        if (parsed.name !== entry.name) {
          // 注册表按 name 寻址；两者不一致时，加载会拿到「另一个名字的技能」。
          warn(ctx, "跳过技能 " + entry.name + '：frontmatter name="' + parsed.name + '" 与目录名不一致');
          continue;
        }
        candidates.push({
          name: parsed.name,
          description: parsed.description,
          invocation: parsed.invocation,
          source: "bundled",
          provider: PROVIDER_NAME,
          rank: BUNDLED_SKILL_RANK,
          locator: file,
          path: file,
          resourceBase: { kind: "directory", path: dir },
        });
      }
      return candidates;
    },
    async get(candidate, options) {
      options?.signal?.throwIfAborted();
      const file = typeof candidate?.locator === "string" ? candidate.locator : undefined;
      if (file === undefined) {
        throw new Error('video-studio: candidate "' + candidate?.name + '" 没有可读的 locator');
      }
      let raw;
      try {
        raw = await readFile(file, "utf8");
      } catch (error) {
        // 契约：发现之后文件消失应返回 undefined，而不是抛错（上层据一次「找不到」处理）。
        if (error?.code === "ENOENT") return undefined;
        throw error;
      }
      const parsed = parseSkillFile(raw);
      if (parsed === undefined) {
        throw new Error("video-studio: " + file + " 的 frontmatter 已失效"
          + "（需要合法 name（小写 kebab-case）与 description；invocation 策略的取值也要合法）");
      }
      if (parsed.name !== candidate.name) {
        throw new Error(
          "video-studio: " + file + ' 现在声明 name="' + parsed.name + '"，但发现时是 "' + candidate.name + '"',
        );
      }
      return {
        name: parsed.name,
        description: parsed.description,
        invocation: parsed.invocation,
        source: "bundled",
        provider: PROVIDER_NAME,
        resourceBase: { kind: "directory", path: dirname(file) },
        path: file,
        content: parsed.content,
      };
    },
  };
}

/** 双引号标量里的单字符转义。 */
const SIMPLE_ESCAPES = { n: "\n", t: "\t", r: "\r", b: "\b", f: "\f", v: "\v", "0": "\0", '"': '"', "\\": "\\", "/": "/" };

/**
 * 把双引号标量还原成真实字符。
 *
 * @param text - 去掉两端引号后的文本。
 * @returns 还原后的字符串。
 */
function unescapeDoubleQuoted(text) {
  // 必须**单趟从左到右**扫：先做 \\uXXXX 替换再处理反斜杠的写法有个真 bug——
  // YAML 里的 "\\\\u0041"（字面量 \\u0041）会先被 \\u 规则命中变成 \\A，再被单字符规则吃掉，
  // 结果既不是字面量也不是 \u0041。逐字符消费就天然正确：遇到 \\\\ 先吃掉转义反斜杠，
  // 后面的 u0041 就是普通文本，不会再被当成一次转义。
  let out = "";
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch !== "\\") {
      out += ch;
      continue;
    }
    const next = text[i + 1];
    if (next === "u" || next === "x") {
      const hexLen = next === "u" ? 4 : 2;
      const hex = text.slice(i + 2, i + 2 + hexLen);
      if (hex.length === hexLen && /^[0-9A-Fa-f]+$/.test(hex)) {
        out += String.fromCharCode(parseInt(hex, 16));
        i += 1 + hexLen;
        continue;
      }
    }
    if (next !== undefined && Object.hasOwn(SIMPLE_ESCAPES, next)) {
      out += SIMPLE_ESCAPES[next];
      i += 1;
      continue;
    }
    out += ch;
  }
  return out;
}

/**
 * 解析一个 YAML 标量：裸标量 / 单引号 / 双引号。
 *
 * @param value - "key:" 之后的部分（行尾空白已在调用方去掉）。
 * @returns 字符串值；块标量（| >）与流式集合（[ {）返回 undefined（本解析器不支持）。
 */
function parseScalar(value) {
  const text = value.trim();
  if (text === "") return "";
  const head = text[0];
  if (head === "|" || head === ">" || head === "[" || head === "{") return undefined;
  if (head === '"') {
    if (text.length < 2 || !text.endsWith('"')) return undefined;
    return unescapeDoubleQuoted(text.slice(1, -1));
  }
  if (head === "'") {
    if (text.length < 2 || !text.endsWith("'")) return undefined;
    return text.slice(1, -1).replace(/''/g, "'");
  }
  return text;
}

/**
 * 把 frontmatter 的顶层标量读成对象。
 *
 * 为什么只支持「顶层 key: 标量」这一小撮：插件要在用户机器上、没有 node_modules
 * 的环境里直接运行，引一个 YAML 库就多一种装不上的可能；而 SKILL.md 的 frontmatter
 * 只需要 name / description（本技能没写 invocation 键）。超出能力的写法一律返回
 * undefined，由调用方按「frontmatter 非法」告警跳过——宁可不挂，也不猜错描述。
 *
 * @param yaml - frontmatter 的 YAML 文本。
 * @returns 顶层标量映射，或 undefined（说明超出本解析器能力）。
 */
function parseFrontmatterScalars(yaml) {
  const data = {};
  for (const rawLine of yaml.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (line.trim() === "" || line.trimStart().startsWith("#")) continue;
    if (line[0] === " " || line[0] === "\t" || line[0] === "-") return undefined; // 缩进块 / 列表：不支持
    const colon = line.indexOf(":");
    if (colon <= 0) return undefined;
    const key = line.slice(0, colon).trim();
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(key)) return undefined;
    const scalar = parseScalar(line.slice(colon + 1));
    if (scalar === undefined) return undefined;
    data[key] = scalar;
  }
  return data;
}

/**
 * 解析 SKILL.md：YAML frontmatter -> { name, description, invocation, content }。
 *
 * 与官方 dsh-skill-filesystem 的 parseSkillFile 同口径：frontmatter 必须以 --- 开头、
 * 以单独一行的 --- 结束；缺 name / description、name 不是小写 kebab-case、或
 * invocation 取值读不懂，一律视为非法（返回 undefined）。
 *
 * 为什么 name 语法也要在这里拦：注册表对非法 name 是 **抛错**（validateCandidate），
 * 不是跳过该技能——一个不合法的名字会让整个 provider 的 list() 失败，全部自带技能消失。
 *
 * @param raw - SKILL.md 全文。
 * @returns 解析结果；不合规时返回 undefined。
 */
export function parseSkillFile(raw) {
  // 先剥 UTF-8 BOM：Windows 编辑器（记事本"UTF-8 带 BOM"）保存过的 SKILL.md 会以 \uFEFF 开头，
  // 不剥的话 startsWith("---") 判负，整个技能被判"缺 frontmatter"并静默跳过。
  const text = String(raw ?? "").replace(/^\uFEFF/, "");
  if (!text.startsWith("---")) return undefined;
  const lines = text.split("\n");
  if (lines[0].replace(/\r$/, "") !== "---") return undefined;
  let closeIndex = -1;
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i].replace(/\r$/, "") === "---") {
      closeIndex = i;
      break;
    }
  }
  if (closeIndex < 0) return undefined;
  const data = parseFrontmatterScalars(lines.slice(1, closeIndex).join("\n"));
  if (data === undefined) return undefined;
  const name = typeof data.name === "string" ? data.name.trim() : "";
  const description = typeof data.description === "string" ? data.description.trim() : "";
  if (name === "" || description === "" || !SKILL_NAME_RE.test(name)) return undefined;
  const invocation = parseInvocation(data);
  if (invocation === undefined) return undefined;
  return {
    name,
    description,
    invocation,
    content: lines.slice(closeIndex + 1).join("\n").trim(),
  };
}

/**
 * 读 invocation 策略。
 *
 * 与官方 dsh-skill-filesystem 的 parseInvocationPolicy 同口径：只认标准键
 * disable-model-invocation / user-invocable，缺省 = 模型与用户都可调用。
 * 但值读不懂（例如 true/false/yes/no/on/off/1/0 之外的字符串）或用了旧版驼峰键时，
 * 返回 undefined（= frontmatter 非法、跳过该技能并告警），**不能**静默退回「都可调用」：
 * 那会把一条写错的 disable-model-invocation 悄悄变成放行。
 *
 * @param data - 顶层标量映射。
 * @returns 调用策略对象；取值非法时返回 undefined。
 */
function parseInvocation(data) {
  // 旧版驼峰键在官方 provider 里是显式拒绝的（rejectLegacyInvocationKey）；这里同样不当默认值忽略。
  for (const legacy of ["disableModelInvocation", "modelInvocable", "userInvocable"]) {
    if (Object.hasOwn(data, legacy)) return undefined;
  }
  const asBoolean = (value) => {
    const text = String(value).trim().toLowerCase();
    if (text === "true" || text === "yes" || text === "on" || text === "1") return true;
    if (text === "false" || text === "no" || text === "off" || text === "0") return false;
    return undefined;
  };
  const disableModelInvocation = Object.hasOwn(data, "disable-model-invocation")
    ? asBoolean(data["disable-model-invocation"])
    : undefined;
  const userInvocable = Object.hasOwn(data, "user-invocable")
    ? asBoolean(data["user-invocable"])
    : undefined;
  if ((Object.hasOwn(data, "disable-model-invocation") && disableModelInvocation === undefined)
      || (Object.hasOwn(data, "user-invocable") && userInvocable === undefined)) {
    return undefined;
  }
  return {
    modelInvocable: disableModelInvocation !== true,
    userInvocable: userInvocable !== false,
  };
}

/**
 * 0.1.x 兼容路径：交给老的 filesystem provider 挂 bundled 根。
 *
 * 只有 ctx.skills.registerProvider 不存在时才走到这里；0.2.x 永不执行。
 *
 * @param ctx - Cordis 上下文。
 */
async function applyLegacy(ctx) {
  let applyFilesystemProvider;
  try {
    ({ apply: applyFilesystemProvider } = await import(LEGACY_PROVIDER_PKG));
  } catch (error) {
    warn(
      ctx,
      "缺少 " + LEGACY_PROVIDER_PKG + "，本次不挂载技能。当前 DSH 既没有 " +
        "ctx.skills.registerProvider，也解析不到该包：升级 DSH，或 " +
        "dsh plugin --profile <profile> add " + LEGACY_PROVIDER_PKG + "（" + errorText(error) + "）",
    );
    return;
  }
  try {
    // 必须 await：provider 的 apply 允许返回 Promise，不 await 时这里只能拦住同步 throw。
    await applyFilesystemProvider(ctx, {
      providerName: PROVIDER_NAME,
      includeDefaultRoots: false,
      bundledSkillDir,
      watch: false,
    });
  } catch (error) {
    warn(
      ctx,
      "挂载 bundled 技能根失败（本次只是空行，不影响 profile 里其它插件）：" + errorText(error) +
        "；检查 " + LEGACY_PROVIDER_PKG + " 的版本与 " + bundledSkillDir + " 是否可读",
    );
  }
}

/**
 * 取错误文本（带 code，便于区分「缺包」与「包坏了」）。
 *
 * @param error - 捕获到的任意值。
 * @returns 一行可读文本。
 */
function errorText(error) {
  return error?.code ?? error?.message ?? String(error);
}

/**
 * 打一条降级日志。
 *
 * Cordis 的 logger 是可选注入（不同版本形态不同：有的是 logger 服务、有的只有
 * console 桥接），这里按能力探测，永不因为「日志打不出来」而让插件加载失败。
 *
 * @param ctx - Cordis 上下文。
 * @param message - 面向用户的说明（中文，带下一步动作）。
 */
function warn(ctx, message) {
  const text = "[video-studio] " + message;
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
