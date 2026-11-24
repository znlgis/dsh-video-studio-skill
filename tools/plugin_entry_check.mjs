/**
 * plugin_entry_check.mjs — 插件入口冒烟测试（不需要 DSH 运行时）。
 *
 * 为什么需要它：DSH 的技能注册接口在 0.2.x 换成了
 * ctx.skills.registerProvider(create)，而本插件曾经只走 0.1.x 的
 * dsh-skill-filesystem.apply()。换版本后那条路在运行期解析不到包，
 * 于是 apply() 只打一条「缺少可选依赖」的日志就返回——技能一个都没挂上，
 * 而插件行、node --check、selfcheck 全都显示正常。这类「静默降级」只能靠
 * 真的把入口跑一遍来发现。
 *
 * 做法：用一个最小 ctx 桩（只有 skills.registerProvider + logger）调 apply，
 * 断言它**确实注册了 provider**，并且 provider 的 list()/get() 能返回
 * skills/video-studio/SKILL.md 的内容。
 *
 * 除了入口本身，这个脚本还守着两条**运行期契约**（都是"静默失效"的高发区）：
 *   - apply(ctx, config)：配置面（P1-1）必须真的把 mode / optionalModules / strictGates
 *     注入技能正文，且非法配置（未知 mode / 未知 module / 类型不对 / 未知键）必须抛错；
 *   - diagnose()：一行诊断（P0-4）在真包上必须 healthy=true——正文里只要有一个悬空的
 *     references 引用，这里就会红，而不是等用户点进 404 的说明书。
 *
 * 用法：node tools/plugin_entry_check.mjs [--verbose]
 * 退出码：0 = 通过；1 = 失败（消息里写明哪一步不成立）。
 */
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

/** 包根（本文件在 tools/ 下）。 */
const ROOT = fileURLToPath(new URL("../", import.meta.url));
/** 预期技能名。 */
const SKILL_NAME = "video-studio";
/** 打包技能根的标准 rank（dsh-skill 的 BUNDLED_SKILL_RANK）。 */
const BUNDLED_SKILL_RANK = 600;

const VERBOSE = process.argv.includes("--verbose");

/**
 * 断言，失败时抛错（由 main 统一打印成一行）。
 *
 * @param {boolean} condition - 必须为真的条件。
 * @param {string} message - 不成立时的说明。
 */
function check(condition, message) {
  if (!condition) throw new Error(message);
  if (VERBOSE) console.log("  ok  " + message);
}

/**
 * 构造最小 Cordis 上下文桩：只提供插件会碰的两样东西。
 *
 * @returns {{ctx: object, registered: object[]}} 桩上下文与注册记录。
 */
function makeContext() {
  const registered = [];
  const warnings = [];
  const ctx = {
    logger: { warn: (line) => warnings.push(String(line)) },
    skills: {
      registerProvider: (create) => {
        registered.push(create);
        return () => {};
      },
    },
  };
  return { ctx, registered, warnings };
}

/**
 * 插件入口冒烟：apply 必须真的注册 provider，且 provider 能列出/加载技能。
 *
 * @returns {Promise<number>} 进程退出码。
 */
async function main() {
  const entry = pathToFileURL(ROOT + "index.js").href;
  const mod = await import(entry);

  check(mod.name === "video-studio", '入口必须导出 name = "video-studio"（cordis.patch.yml 的 row id 靠它对齐）');
  check(Array.isArray(mod.inject) && mod.inject.includes("skills"), '入口必须导出 inject 且包含 "skills"');
  check(typeof mod.apply === "function", "入口必须导出 apply(ctx)");

  const { ctx, registered, warnings } = makeContext();
  await mod.apply(ctx);
  check(
    registered.length === 1,
    "apply 必须注册 1 个技能 provider（实际 " + registered.length + " 个）。" +
      "为 0 通常意味着入口走了某条降级分支：" + (warnings.length > 0 ? warnings.join(" | ") : "无告警"),
  );

  const provider = registered[0]({ signal: new AbortController().signal, invalidate: () => {} });
  check(provider && typeof provider.list === "function", "provider 必须实现 list(options)");
  check(provider && typeof provider.get === "function", "provider 必须实现 get(candidate, options)");

  const candidates = await provider.list({});
  check(Array.isArray(candidates) && candidates.length >= 1, "list() 必须返回至少 1 个候选（skills/ 下的技能目录）");
  // 断言"包含 video-studio"，而不是"恰好 1 个"：入口是枚举 skills/ 的通用 provider，
  // 将来加第二个技能/包里多一个合法目录时插件正常，这条冒烟不该红（失败信息会误导成入口坏了）。
  const candidate = (candidates || []).find((c) => c && c.name === SKILL_NAME);
  check(Boolean(candidate), "list() 里必须包含 " + SKILL_NAME + "（实际：" + (candidates || []).map((c) => c && c.name).join(" / ") + "）");
  check(candidate.name === SKILL_NAME, "候选 name 必须是 " + SKILL_NAME + "（收到 " + candidate.name + "）");
  check(typeof candidate.description === "string" && candidate.description.length > 0, "候选必须带非空 description");
  check(candidate.rank === BUNDLED_SKILL_RANK, "候选 rank 必须是 " + BUNDLED_SKILL_RANK + "（打包技能根的标准优先级）");
  check(candidate.source === "bundled", '候选 source 必须是 "bundled"');
  check(candidate.provider === "video-studio", '候选 provider 必须是 "video-studio"');
  check(
    candidate.resourceBase && candidate.resourceBase.kind === "directory" && typeof candidate.resourceBase.path === "string",
    "候选必须带 directory 型 resourceBase（SKILL.md 里的相对引用靠它解析）",
  );
  check(
    candidate.invocation && candidate.invocation.modelInvocable === true && candidate.invocation.userInvocable === true,
    "缺省 invocation 必须允许模型与用户调用",
  );

  const definition = await provider.get(candidate, {});
  check(definition !== undefined, "get() 必须返回技能定义");
  check(definition.name === SKILL_NAME, "定义 name 必须是 " + SKILL_NAME);
  check(typeof definition.content === "string" && definition.content.length > 1000, "定义必须带 SKILL.md 正文");
  check(!definition.content.startsWith("---"), "正文不能包含 frontmatter（frontmatter 属于元数据，不属于正文）");

  // 正文必须与磁盘上的 SKILL.md 正文一致（frontmatter 之后的部分），防止解析器把分量弄丢。
  const raw = await readFile(ROOT + "skills/" + SKILL_NAME + "/SKILL.md", "utf8");
  const bodyStart = raw.indexOf("\n---", raw.indexOf("\n"));
  const expectedBody = bodyStart >= 0 ? raw.slice(bodyStart + 4).replace(/^\r?\n/, "").trim() : "";
  check(expectedBody.length > 0 && definition.content === expectedBody, "正文必须等于 SKILL.md 中 frontmatter 之后的部分");

  // ---- apply(ctx, config)：配置面（P1-1） ----
  // 带配置时必须"原文 + 追加块"，而不是改写正文；没有配置时正文必须与源文件逐字节一致（上面已断言）。
  const configCtx = makeContext();
  await mod.apply(configCtx.ctx, { mode: "footage", optionalModules: ["sfxkit", "qa_frames"], strictGates: true });
  check(configCtx.registered.length === 1, "带 config 时 apply 仍必须注册 1 个 provider");
  const configProvider = configCtx.registered[0]({ signal: new AbortController().signal, invalidate: () => {} });
  const configCandidate = (await configProvider.list({})).find((c) => c && c.name === SKILL_NAME);
  check(Boolean(configCandidate), "带 config 时 list() 仍要包含 " + SKILL_NAME);
  const configDefinition = await configProvider.get(configCandidate, {});
  check(
    configDefinition.content.startsWith(expectedBody),
    "带 config 时正文必须以 SKILL.md 原文开头（配置只允许追加，不许改写正文）",
  );
  const configNeedles = [
    "mode: `footage`",
    "optionalModules",
    "`sfxkit`",
    "`qa_frames`",
    "strictGates: true",
    "--strict-gates",
    "--require-proposal",
  ];
  for (const needle of configNeedles) {
    check(configDefinition.content.includes(needle), "配置块里必须出现 " + needle);
  }
  // 非法配置必须抛错：静默忽略等于"配了 footage 却按 auto 跑"，一路跑到成片才被发现。
  const badConfigs = [
    [{ mode: "bogus" }, "未知 mode"],
    [{ optionalModules: ["no-such-module"] }, "未知 module"],
    [{ optionalModules: "sfxkit" }, "optionalModules 不是数组"],
    [{ strictGates: "yes" }, "strictGates 不是布尔"],
    [{ unknownKey: 1 }, "未知配置键"],
    ["footage", "config 不是对象"],
  ];
  for (const [bad, why] of badConfigs) {
    let threw = false;
    try {
      await mod.apply(makeContext().ctx, bad);
    } catch {
      threw = true;
    }
    check(threw, "非法配置（" + why + "）必须抛错，不能静默忽略");
  }

  // ---- diagnose()：一行诊断（P0-4） ----
  check(typeof mod.diagnose === "function", "入口必须导出 diagnose()（一行诊断入口）");
  const diag = await mod.diagnose();
  check(
    diag && diag.provider === SKILL_NAME && diag.healthy === true,
    "diagnose() 在真包上必须 healthy=true（实际：" + JSON.stringify({
      healthy: diag && diag.healthy,
      missing: diag && diag.missing,
      degraded: diag && diag.degraded,
      danglingRefs: diag && diag.danglingRefs && diag.danglingRefs.slice(0, 3),
    }) + "）",
  );
  check(typeof diag.catalogTokens === "number" && diag.catalogTokens > 0, "diagnose().catalogTokens 必须 > 0");

  console.log("PASS 插件入口：" + SKILL_NAME + " provider 注册成功 / list 1 个候选 / get " + definition.content.length
    + " 字 / 配置面 6 类非法配置全部报错 / diagnose healthy=true（目录 " + diag.catalogTokens + " token）");
  return 0;
}

main().catch((error) => {
  console.error("FAIL 插件入口：" + (error && error.message ? error.message : String(error)));
  process.exitCode = 1;
});
