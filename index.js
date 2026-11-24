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
 *
 * ## 配置面：apply(ctx, config)
 *
 * 可选第二参 config = { mode, optionalModules, strictGates }（见 README「插件配置」）：
 *   - mode: "auto" | "footage" | "draw"，缺省 auto = 按问诊第 2 题落地（行为与不传配置一致）；
 *   - optionalModules: string[]，声明 sfxkit / scorekit / webengine / qa_frames 在本会话可用；
 *   - strictGates: boolean，交付前是否把 skipped 当失败（--strict-gates / --require-proposal）。
 * 配置以**追加块**的形式拼进 get() 返回的技能正文（不落盘、不改 SKILL.md 源文件），
 * 卸载插件即整体消失。没传 config 时返回的内容与未配置版本逐字节相同（向后兼容有测试守着）；
 * 配置非法（未知 mode / 未知 module / 类型不对 / 未知键）直接抛错，不静默忽略。
 *
 * ## 诊断入口：diagnose()
 *
 * 一个只读、不联网、不写盘的导出函数，回答「技能为什么没出现」：
 * 技能数 / 可见数 / 缺失 / 降级原因 / 正文与 references 之间的悬空引用 /
 * 技能目录的 token 估算 / 宿主版本提示 / healthy。入口脚本见 tools/diagnose.mjs
 * （npm run diagnose，healthy=false 时退出码 1）。
 */
import { access, readFile, readdir } from "node:fs/promises";
import { dirname, join, relative, sep } from "node:path";
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

/** apply(ctx, config) 允许的配置键。未知键一律抛错：写错的配置静默忽略等于没配。 */
const CONFIG_KEYS = ["mode", "optionalModules", "strictGates"];
/** 产线模式：auto = 按问诊第 2 题落地（默认，行为与未配置版本一致）。 */
const MODES = {
  auto: "按问诊第 2 题「制作方式」落地（默认；显式选择永远优先于推荐）",
  footage: "只走素材剪辑线（footage.json + scripts/footage.py）；有素材/能下载就先走它，别自己画帧；提案层不适用",
  draw: "只走零素材线（film.json + scripts/render.py + work/design.json）；确实没有画面素材时用它",
};
/** optionalModules 允许的名字：都在本包内，声明的是"本会话可用"，不是"要加载"。 */
const OPTIONAL_MODULES = {
  sfxkit: "音效轨（--stage sfx；sfxkit.py 物理建模配方）",
  scorekit: "配乐合成原语（--stage score 的底层；配乐编排器是 musicgen.py）",
  webengine: "浏览器逐帧渲染（--engine web；缺 playwright/Chromium 时不可用）",
  qa_frames: "帧级深检（--stage qa 的闪烁/空窗/音画同步；qa_frames.py）",
};
/** 打包里"应该存在"的技能名（缺了要在 diagnose 的 missing 里点名）。 */
const EXPECTED_SKILLS = ["video-studio"];

/** Cordis 插件名（与 cordis.patch.yml 的 row id 一致）。 */
export const name = "video-studio";
/** 依赖 DSH 技能注册表。 */
export const inject = ["skills"];

/**
 * 注册 bundled 技能根。
 *
 * 任何一步失败都只打日志、不抛错——插件本身没有「必须成功」的业务，
 * 挂不上技能是降级，不是崩溃。**唯一的例外是配置非法**：那种情况必须当场报错，
 * 否则"写了 mode: footage 却按 auto 跑"会一路跑到成片才被发现。
 *
 * @param ctx - 携带 skills 服务的 Cordis 上下文。
 * @param config - 可选配置（见文件头「配置面」）；非法配置抛错。
 */
export function apply(ctx, config) {
  // 先校验再注册：配置写错时 provider 一个都不该挂，避免"半配置"状态。
  const normalized = normalizeConfig(config);
  const registry = ctx?.skills;
  if (typeof registry?.registerProvider === "function") {
    try {
      registry.registerProvider(() => createBundledProvider(ctx, normalized));
    } catch (error) {
      warn(ctx, "注册技能 provider 失败（本次只是空行）：" + errorText(error));
    }
    return;
  }
  // 0.1.x 形态：没有 registerProvider 时才走老的 filesystem provider。
  // apply 必须同步返回，所以异步工作挂到浮空 Promise 上，并保证 reject 不会变成
  // unhandledRejection（Node >=15 默认让进程退出，"挂不上技能只是降级"就变成崩进程）。
  if (normalized !== undefined) {
    // 老路径由外部 provider 挂载，没有注入点；不能假装生效——打一条明确的降级日志。
    warn(ctx, "当前 DSH 走 0.1.x 回退路径，apply 配置（mode/optionalModules/strictGates）无法注入技能正文，本次按默认行为挂载");
  }
  applyLegacy(ctx).catch((error) => {
    warn(ctx, "回退到老版 filesystem provider 失败（本次只是空行）：" + errorText(error));
  });
}

/**
 * 校验并归一化 apply(ctx, config) 的配置。
 *
 * 语义：没传（undefined/null）返回 undefined = 完全按未配置版本跑（向后兼容的判据）；
 * 传了就必须逐项合法——未知 mode / 未知 module / 类型不对 / 未知键都抛错。
 * 这些错误发生在加载期，比"配置了 footage 却按 auto 出了一片代码画的图文"便宜得多。
 *
 * @param raw - apply 的第二参。
 * @returns {{mode: string, optionalModules: string[], strictGates: boolean}|undefined} 归一化配置。
 */
function normalizeConfig(raw) {
  if (raw === undefined || raw === null) return undefined;
  if (typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("video-studio: apply(ctx, config) 的 config 必须是对象（收到 "
      + (Array.isArray(raw) ? "数组" : typeof raw) + "）");
  }
  const unknownKeys = Object.keys(raw).filter((key) => !CONFIG_KEYS.includes(key));
  if (unknownKeys.length > 0) {
    throw new Error("video-studio: config 里有未知配置项 " + JSON.stringify(unknownKeys)
      + "（支持 " + CONFIG_KEYS.join(" / ") + "）");
  }
  const mode = raw.mode === undefined ? "auto" : raw.mode;
  if (typeof mode !== "string" || !Object.hasOwn(MODES, mode)) {
    throw new Error("video-studio: config.mode=" + JSON.stringify(raw.mode)
      + " 非法（支持 auto / footage / draw）");
  }
  const strictGates = raw.strictGates === undefined ? false : raw.strictGates;
  if (typeof strictGates !== "boolean") {
    throw new Error("video-studio: config.strictGates 必须是布尔值（收到 " + JSON.stringify(raw.strictGates) + "）");
  }
  let optionalModules = [];
  if (raw.optionalModules !== undefined) {
    if (!Array.isArray(raw.optionalModules)) {
      throw new Error("video-studio: config.optionalModules 必须是字符串数组（收到 " + JSON.stringify(raw.optionalModules) + "）");
    }
    const bad = raw.optionalModules.filter((item) => typeof item !== "string" || !Object.hasOwn(OPTIONAL_MODULES, item));
    if (bad.length > 0) {
      throw new Error("video-studio: config.optionalModules 里有未知模块 " + JSON.stringify(bad)
        + "（支持 " + Object.keys(OPTIONAL_MODULES).join(" / ") + "）");
    }
    optionalModules = [...new Set(raw.optionalModules)];
  }
  return { mode, optionalModules, strictGates };
}

/**
 * 把配置渲染成追加到技能正文末尾的 Markdown 块。
 *
 * 为什么是"追加块"而不是改 SKILL.md：正文是包内只读资产（npm 包内容），
 * 配置是**加载期**的会话状态；拼在 get() 的返回值上，卸载插件后什么都不留，
 * 也不存在"改了源文件忘了改回来"。参考 opengis 的 adaptations 注入做法。
 *
 * @param config - normalizeConfig 的返回值。
 * @returns Markdown 文本（不含前后空行）。
 */
function buildConfigBlock(config) {
  const lines = [
    "## 本会话配置（插件 apply(ctx, config) 在加载时注入；不是 SKILL.md 源文件内容）",
    "",
    "> 这一段由 `@dsh-gis/dsh-video-studio-skill` 在挂载技能时**追加**在正文末尾，用来把本次会话的配置告诉模型。",
    "> SKILL.md 源文件里没有这段；插件没有收到 config 时也不会有这段（行为与未配置版本完全一致）。",
    "> 与正文冲突时，本节只覆盖它点名的那几件事；铁律与门禁命令仍以正文为准。",
    "",
    "- **mode: `" + config.mode + "`** —— " + MODES[config.mode] + "。",
  ];
  if (config.optionalModules.length === 0) {
    lines.push("- **optionalModules: 未声明** —— 可选模块按环境探测；缺失时相关门禁记 `skipped`，"
      + "**skipped 不等于通过**，报告里要说明为什么没跑。");
  } else {
    const detail = config.optionalModules.map((item) => "`" + item + "`（" + OPTIONAL_MODULES[item] + "）");
    lines.push("- **optionalModules** —— 本会话声明可用：" + detail.join("、") + "；"
      + "没声明的模块仍按环境探测。");
  }
  if (config.strictGates) {
    lines.push("- **strictGates: true** —— 交付前用 `--stage qa --strict-gates`（skipped 也算失败），"
      + "并加 `--require-proposal` 让提案闸真的拦；报告里 skipped 项仍要逐条说明原因。");
  } else {
    lines.push("- **strictGates: false / 未声明** —— 沿用默认门禁口径（skipped 只告警，不阻断）。");
  }
  return lines.join("\n");
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
function createBundledProvider(ctx, config) {
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
      // 配置块只拼在**返回给模型的内容**上：SKILL.md 源文件永远保持原样，
      // 也没往磁盘写任何东西（卸载插件后不留被改写的文档）。
      const content = config === undefined
        ? parsed.content
        : parsed.content + "\n\n" + buildConfigBlock(config) + "\n";
      return {
        name: parsed.name,
        description: parsed.description,
        invocation: parsed.invocation,
        source: "bundled",
        provider: PROVIDER_NAME,
        resourceBase: { kind: "directory", path: dirname(file) },
        path: file,
        content,
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

/** 包根（诊断结果里的 from / 相对路径都按它算）。 */
const PACKAGE_ROOT = fileURLToPath(new URL("./", import.meta.url));
/** 正文里指向 references 文件的路径写法；只认包内相对路径，不碰外链。 */
const REFERENCE_REF_RE = /references\/[A-Za-z0-9_.\-\/]+/g;

/**
 * 只读诊断（P0-4）：回答"技能为什么没出现 / 正文里引用是不是悬空"。
 *
 * 契约（与 opengis 共享，见 plugin-analysis/改造契约）：返回**纯 JSON 可序列化**对象，
 * 字段名不许改；不得抛错（内部异常一律转成诊断项）、不得写盘、不得联网。
 * healthy 由其余字段机械推导：missing / degraded / danglingRefs / hashMismatch 全空。
 *
 * 字段：
 *   provider / packageVersion / skills / visible / missing / degraded /
 *   danglingRefs / hashMismatch（本包无 vendored 哈希清单，恒为空数组，占位保持两家同构）/
 *   orphanRefs（**本包多出的字段**：references 里存在、但正文一次都没提到的文件——双向自检的另一半）/
 *   catalogTokens / hostVersionHint / healthy。
 *
 * 注意 healthy 的口径严格遵守契约（missing/degraded/danglingRefs/hashMismatch 全空）；
 * orphanRefs 只是附加信号，不影响 healthy（正文精简后"没被引用到的 reference"不算坏包）。
 *
 * @returns {Promise<object>} 诊断结果（可直接 JSON.stringify）。
 */
export async function diagnose() {
  const result = {
    provider: name,
    packageVersion: null,
    skills: 0,
    visible: 0,
    missing: [],
    degraded: [],
    danglingRefs: [],
    hashMismatch: [],
    orphanRefs: [],
    catalogTokens: 0,
    hostVersionHint: null,
    healthy: false,
  };
  try {
    result.packageVersion = await readPackageVersion();
  } catch (error) {
    result.hashMismatch.push("<io-error>: package.json " + errorText(error));
  }
  let entries = [];
  try {
    entries = await readdir(bundledSkillDir, { withFileTypes: true });
  } catch (error) {
    result.hashMismatch.push("<io-error>: skills/ " + errorText(error));
  }
  const dirs = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
  result.skills = dirs.length;
  // 目录文本估算：只算**当前配置（默认）下对模型可见**的技能，name + description 是常驻目录内容。
  const catalog = [];
  for (const dir of dirs) {
    const file = join(bundledSkillDir, dir, "SKILL.md");
    let parsed;
    try {
      parsed = parseSkillFile(await readFile(file, "utf8"));
    } catch (error) {
      // 技能目录被裁剪/漏打包时 ENOENT 属正常：记一条降级，不是让诊断崩掉。
      if (error && error.code === "ENOENT") {
        result.degraded.push({ skill: dir, reason: "skill-md-missing" });
      } else {
        result.hashMismatch.push("<io-error>: " + packageRelativePath(file) + " " + errorText(error));
      }
      continue;
    }
    if (parsed === undefined) {
      result.degraded.push({ skill: dir, reason: "frontmatter-invalid" });
      continue;
    }
    if (parsed.name !== dir) {
      result.degraded.push({ skill: dir, reason: "name-mismatch" });
      continue;
    }
    if (parsed.invocation.modelInvocable === false) {
      result.degraded.push({ skill: dir, reason: "model-invocation-disabled" });
      continue;
    }
    result.visible += 1;
    catalog.push(parsed.name + " " + parsed.description);
  }
  // "应存在但缺失"：本包只有一个随包分发的技能，缺了要能一眼看到（而不是"目录里 0 个技能"）。
  for (const expected of EXPECTED_SKILLS) {
    if (!dirs.includes(expected)) result.missing.push(expected);
  }
  try {
    result.danglingRefs = await collectDanglingRefs(dirs);
  } catch (error) {
    result.hashMismatch.push("<io-error>: references " + errorText(error));
  }
  try {
    result.orphanRefs = await collectOrphanRefs(dirs);
  } catch (error) {
    result.hashMismatch.push("<io-error>: references " + errorText(error));
  }
  result.catalogTokens = estimateTokens(catalog.join("\n"));
  // 探测失败返回 null（契约允许）；探测本身绝不抛错。
  result.hostVersionHint = await probeHostVersion();
  result.healthy = result.missing.length === 0
    && result.degraded.length === 0
    && result.danglingRefs.length === 0
    && result.hashMismatch.length === 0;
  return result;
}

/**
 * 读 package.json 的 version（诊断字段 packageVersion）。
 *
 * @returns {Promise<string>} 版本号。
 */
async function readPackageVersion() {
  const meta = JSON.parse(await readFile(new URL("./package.json", import.meta.url), "utf8"));
  if (meta && typeof meta.version === "string" && meta.version.trim() !== "") return meta.version.trim();
  throw new Error("package.json 没有 version 字段");
}

/**
 * 估算一段文本的 token 数。
 *
 * 口径：ASCII 按 4 字符/token、非 ASCII（中文为主）按 1 字符/token 的粗估。
 * 只用于"这份目录占多少上下文"的量级判断，不追求与具体 tokenizer 对齐。
 *
 * @param text - 待估算文本。
 * @returns {number} 估算 token 数。
 */
function estimateTokens(text) {
  let ascii = 0;
  let wide = 0;
  for (const ch of String(text ?? "")) {
    if (ch.codePointAt(0) < 0x80) ascii += 1;
    else wide += 1;
  }
  return Math.ceil(ascii / 4 + wide);
}

/**
 * 把绝对路径压成"相对包根、正斜杠分隔"的展示形式。
 *
 * @param file - 绝对路径。
 * @returns {string} 例如 skills/video-studio/SKILL.md。
 */
function packageRelativePath(file) {
  return relative(PACKAGE_ROOT, file).split(sep).join("/");
}

/**
 * 扫描每个技能的 SKILL.md 与 references/*.md，找出指向不存在的 references 文件的引用。
 *
 * 这是本包最需要的一条自检：正文历史上有 36 处 references 引用，删/改名任意一个文件
 * 时正文会留下"点进去 404"的指针，而模型只会照着读、不会报错。
 * 只在包内解析，不跟随外链、不解析网络路径。
 *
 * @param dirs - skills/ 下的技能目录名。
 * @returns {Promise<Array<{from: string, ref: string}>>} 悬空引用（去重、按 from+ref 排序）。
 */
async function collectDanglingRefs(dirs) {
  const found = new Map();
  for (const dir of dirs) {
    const skillDir = join(bundledSkillDir, dir);
    const mdFiles = [join(skillDir, "SKILL.md")];
    let refEntries = [];
    try {
      refEntries = await readdir(join(skillDir, "references"));
    } catch {
      refEntries = []; // 没有 references/ 目录本身是合法结构
    }
    for (const entry of refEntries) {
      if (entry.endsWith(".md")) mdFiles.push(join(skillDir, "references", entry));
    }
    for (const file of mdFiles) {
      let text;
      try {
        text = await readFile(file, "utf8");
      } catch {
        continue;
      }
      const from = packageRelativePath(file);
      for (const match of text.matchAll(REFERENCE_REF_RE)) {
        const ref = match[0].replace(/[.,;:)\]}]+$/, "");
        // 占位符（<SKILL_DIR>/references/{...}）不是真引用，别误报。
        if (ref === "references/" || /[{}*<>]/.test(ref)) continue;
        let exists = true;
        try {
          await access(join(skillDir, ref));
        } catch {
          exists = false;
        }
        if (!exists) found.set(from + "\u0000" + ref, { from, ref });
      }
    }
  }
  return [...found.values()].sort((a, b) => (a.from === b.from ? a.ref.localeCompare(b.ref) : a.from.localeCompare(b.from)));
}

/**
 * 反向自检：references/ 里存在、但该技能的 SKILL.md 一次都没提到的文件。
 *
 * 正向（danglingRefs）查"正文指向的文件在不在"；反向查"文件在但正文没指向"——
 * 正文瘦身时最容易留下的形态是"材料还在、目录表漏登记"，模型永远不会知道该读它。
 * 只在 SKILL.md 里找文件名主干（带不带 .md 都算），不把 references 之间互引当正文引用。
 *
 * @param dirs - skills/ 下的技能目录名。
 * @returns {Promise<string[]>} 未被正文引用的 references 路径（相对技能目录，排序）。
 */
async function collectOrphanRefs(dirs) {
  const orphans = [];
  for (const dir of dirs) {
    const skillDir = join(bundledSkillDir, dir);
    let text = "";
    try {
      text = await readFile(join(skillDir, "SKILL.md"), "utf8");
    } catch {
      continue;
    }
    let refEntries = [];
    try {
      refEntries = await readdir(join(skillDir, "references"));
    } catch {
      continue;
    }
    for (const entry of refEntries) {
      if (!entry.endsWith(".md")) continue;
      const stem = entry.slice(0, -3);
      if (!text.includes("references/" + entry) && !text.includes("references/" + stem) && !text.includes(stem)) {
        orphans.push("references/" + entry);
      }
    }
  }
  return orphans.sort();
}

/**
 * 探测宿主 DSH 版本（只读、不联网）。探不到给 null。
 *
 * 只认**确定性来源**，不从日志/进程名/安装目录名里猜：
 *   1) 环境变量 DSH_VERSION（CI 或宿主可显式注入）；
 *   2) 环境变量 DSH_INSTALL_DIR/package.json 的 version（桌面端这条路径在 app.asar 里，
 *      普通 Node 读不到属正常——读到才用）；
 *   3) 都不成立 → null。
 *
 * 为什么不扫 $DSH_HOME/logs 的 startup-*.log：实测那些日志停在三个月前（0.1.x 时代），
 * 拿它当"当前版本"会报一个**能用但过期**的版本号，比 null 更误导。诊断的诚实优先于填满字段。
 *
 * @returns {Promise<string|null>} 版本号或 null。
 */
async function probeHostVersion() {
  try {
    const envVersion = typeof process.env.DSH_VERSION === "string" ? process.env.DSH_VERSION.trim() : "";
    if (envVersion !== "") return envVersion;
    const installDir = typeof process.env.DSH_INSTALL_DIR === "string" ? process.env.DSH_INSTALL_DIR.trim() : "";
    if (installDir !== "") {
      try {
        const meta = JSON.parse(await readFile(join(installDir, "package.json"), "utf8"));
        if (meta && typeof meta.version === "string" && meta.version.trim() !== "") return meta.version.trim();
      } catch {
        // app.asar 内的路径普通 Node 读不到：当作"探不到"，返回 null。
      }
    }
  } catch {
    // 探测失败不是诊断失败：hostVersionHint 给 null，其余字段照常返回。
  }
  return null;
}
