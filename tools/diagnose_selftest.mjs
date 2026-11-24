/**
 * diagnose_selftest.mjs — diagnose() 的坏包/好包回归（不需要 DSH 运行时）。
 *
 * 为什么单独一个脚本：diagnose() 的价值全在"坏的时候能不能报出来"。
 * 好包全绿只能证明它没崩；要证明它真在查，必须造三种坏包：
 *   1) 删掉一个被正文引用的 references 文件 -> danglingRefs 必须点名；
 *   2) frontmatter 非法 -> degraded 必须带 reason=frontmatter-invalid；
 *   3) frontmatter name 与目录名不一致 -> degraded 必须带 reason=name-mismatch。
 * 另外还守着三条契约：不抛错、不写盘、结果纯 JSON 可序列化（不含 undefined）。
 *
 * 做法：在系统临时目录里复制 index.js 当夹具（只依赖 node 内置，能独立跑），
 * 写最小 package.json + skills/<name>/SKILL.md，再 import 副本调 diagnose()。
 * 副本在 tmpdir，**不碰仓库工作树**；每个用例结束递归删除。
 *
 * 用法：node tools/diagnose_selftest.mjs [--verbose]
 * 退出码：0 = 全部通过；1 = 有断言失败（消息里写清哪条不成立）。
 */
import { spawnSync } from "node:child_process";
import { copyFile, mkdtemp, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** 包根（本文件在 tools/ 下）。 */
const ROOT = fileURLToPath(new URL("../", import.meta.url));
/** 用例计数：打印 PASS n / FAIL m 用。 */
const VERBOSE = process.argv.includes("--verbose");
let passed = 0;

/**
 * 断言，失败时抛错（由 main 统一打印成一行）。
 *
 * @param {boolean} condition - 必须为真的条件。
 * @param {string} message - 不成立时的说明。
 */
function check(condition, message) {
  if (!condition) throw new Error(message);
  passed += 1;
  if (VERBOSE) console.log("  ok  " + message);
}

/**
 * 递归列出目录下所有文件的相对路径 + 大小 + mtime（用于断言"诊断不写盘"）。
 *
 * @param {string} root - 目录。
 * @param {string} [prefix] - 递归内部用。
 * @returns {Promise<string[]>} 排序后的快照行。
 */
async function snapshot(root, prefix = "") {
  const rows = [];
  let entries = [];
  try {
    entries = await readdir(join(root, prefix), { withFileTypes: true });
  } catch {
    return rows;
  }
  for (const entry of entries) {
    const rel = prefix === "" ? entry.name : prefix + "/" + entry.name;
    if (entry.isDirectory()) {
      rows.push("dir:" + rel);
      rows.push(...await snapshot(root, rel));
    } else {
      const info = await stat(join(root, rel));
      rows.push("file:" + rel + ":" + info.size + ":" + info.mtimeMs);
    }
  }
  return rows.sort();
}

/**
 * 造一个独立夹具包：index.js 副本 + package.json（版本 9.9.9 便于断言）。
 *
 * @param {string} label - 用例名（进临时目录名）。
 * @returns {Promise<string>} 夹具包根。
 */
async function makeFixture(label) {
  const root = await mkdtemp(join(tmpdir(), "vs-diagnose-" + label + "-"));
  await writeFile(join(root, "index.js"), await readFile(join(ROOT, "index.js"), "utf8"), "utf8");
  // "type": "module" 是必须的：夹具 index.js 是 ESM，缺它 Node 会每例打一条 MODULE_TYPELESS_PACKAGE_JSON 告警。
  await writeFile(join(root, "package.json"), JSON.stringify({ name: "fixture", version: "9.9.9", type: "module" }, null, 2), "utf8");
  return root;
}

/**
 * 写一个技能目录（SKILL.md + 可选 references）。
 *
 * @param {string} root - 夹具包根。
 * @param {object} options - { dir, name, description, omitDescription, body, refs }。
 */
async function makeSkill(root, options) {
  const dir = options.dir ?? "video-studio";
  const skillDir = join(root, "skills", dir);
  await mkdir(join(skillDir, "references"), { recursive: true });
  const fm = ["---", "name: " + (options.name ?? dir)];
  if (!options.omitDescription) fm.push("description: " + (options.description ?? "夹具技能：验证 diagnose 的判断，不参与出片。 | Fixture skill."));
  fm.push("---", "");
  const body = options.body ?? "测试正文。";
  await writeFile(join(skillDir, "SKILL.md"), fm.join("\n") + "\n" + body + "\n", "utf8");
  for (const [file, text] of Object.entries(options.refs ?? {})) {
    await writeFile(join(skillDir, "references", file), text, "utf8");
  }
}

/**
 * 动态 import 夹具里的 index.js（每个夹具路径不同，天然不撞缓存）。
 *
 * @param {string} root - 夹具包根。
 * @returns {Promise<object>} 模块命名空间。
 */
async function loadFixture(root) {
  return import(pathToFileURL(join(root, "index.js")).href);
}

/**
 * 用例 1：好包全绿 + 纯 JSON + 不写盘。
 *
 * @returns {Promise<void>}
 */
async function caseHealthy() {
  const root = await makeFixture("healthy");
  try {
    await makeSkill(root, {
      refs: { "a.md": "参考 A。" },
      body: "正文引用 [references/a.md](references/a.md)。",
    });
    const mod = await loadFixture(root);
    const before = await snapshot(root);
    const result = await mod.diagnose();
    const after = await snapshot(root);
    check(result.provider === "video-studio", "provider 必须是 video-studio（实际 " + result.provider + "）");
    check(result.packageVersion === "9.9.9", "packageVersion 必须读 package.json（实际 " + result.packageVersion + "）");
    check(result.skills === 1 && result.visible === 1, "好包必须是 1 个技能 / 1 个可见（实际 " + result.skills + "/" + result.visible + "）");
    check(result.missing.length === 0 && result.degraded.length === 0, "好包不该有 missing / degraded");
    check(result.danglingRefs.length === 0, "好包不该有 danglingRefs（实际 " + JSON.stringify(result.danglingRefs) + "）");
    check(result.hashMismatch.length === 0, "好包不该有 hashMismatch（实际 " + JSON.stringify(result.hashMismatch) + "）");
    check(result.catalogTokens > 0, "catalogTokens 必须 > 0（实际 " + result.catalogTokens + "）");
    check(result.healthy === true, "好包必须 healthy=true");
    check(result.hostVersionHint === null || typeof result.hostVersionHint === "string", "hostVersionHint 只能是 string|null");
    // 纯 JSON 可序列化：不得含 undefined（JSON.stringify 会静默丢键，所以直接查值）。
    let undefinedCount = 0;
    for (const value of Object.values(result)) if (value === undefined) undefinedCount += 1;
    check(undefinedCount === 0, "结果里不得有 undefined 字段");
    check(JSON.parse(JSON.stringify(result)).healthy === true, "结果必须能 JSON 往返且语义不变");
    // 不写盘：前后快照（含 mtime）必须逐行相同。
    check(JSON.stringify(before) === JSON.stringify(after), "diagnose() 不得写盘（目录快照前后不一致）");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

/**
 * 用例 2：正文与 references 里的悬空引用必须点名。
 *
 * @returns {Promise<void>}
 */
async function caseDanglingRefs() {
  const root = await makeFixture("dangling");
  try {
    await makeSkill(root, {
      refs: { "b.md": "B 里引用了一个不存在的文件：references/gone.md。" },
      body: "正文引用 references/missing.md 与 [references/a.md](references/a.md)。",
    });
    const mod = await loadFixture(root);
    const result = await mod.diagnose();
    const fromSkill = result.danglingRefs.filter((item) => item.from === "skills/video-studio/SKILL.md").map((item) => item.ref);
    check(fromSkill.includes("references/missing.md"), "正文里的悬空引用必须报出（实际 " + JSON.stringify(result.danglingRefs) + "）");
    const fromRef = result.danglingRefs.filter((item) => item.from === "skills/video-studio/references/b.md").map((item) => item.ref);
    check(fromRef.includes("references/gone.md"), "references/*.md 里的悬空引用也必须报出（实际 " + JSON.stringify(result.danglingRefs) + "）");
    check(result.healthy === false, "有悬空引用时 healthy 必须是 false");
    // 反向自检：b.md 存在于 references/ 但正文没提它 -> orphanRefs 要点名（正文瘦身时最容易漏）。
    check(result.orphanRefs.includes("references/b.md"), "没被正文引用的 reference 必须进 orphanRefs（实际 " + JSON.stringify(result.orphanRefs) + "）");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

/**
 * 用例 3：frontmatter 非法 -> degraded 带 reason=frontmatter-invalid。
 *
 * @returns {Promise<void>}
 */
async function caseBadFrontmatter() {
  const root = await makeFixture("bad-frontmatter");
  try {
    await makeSkill(root, { omitDescription: true });
    const mod = await loadFixture(root);
    const result = await mod.diagnose();
    check(result.degraded.length === 1, "非法 frontmatter 必须记 1 条 degraded（实际 " + JSON.stringify(result.degraded) + "）");
    check(result.degraded[0].skill === "video-studio" && result.degraded[0].reason === "frontmatter-invalid",
      "degraded 必须是 {skill: video-studio, reason: frontmatter-invalid}（实际 " + JSON.stringify(result.degraded) + "）");
    check(result.visible === 0 && result.healthy === false, "非法 frontmatter 时可见数 0 且 healthy=false");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

/**
 * 用例 4：frontmatter name 与目录名不一致 -> degraded 带 reason=name-mismatch。
 *
 * @returns {Promise<void>}
 */
async function caseNameMismatch() {
  const root = await makeFixture("name-mismatch");
  try {
    await makeSkill(root, { name: "other-skill" });
    const mod = await loadFixture(root);
    const result = await mod.diagnose();
    check(result.degraded.length === 1 && result.degraded[0].reason === "name-mismatch",
      "name 与目录名不一致必须记 name-mismatch（实际 " + JSON.stringify(result.degraded) + "）");
    check(result.visible === 0, "name 不一致的技能不可见");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

/**
 * 用例 5：技能目录缺失 -> missing 点名；用例 6：skills/ 整个不可读 -> 不抛错而是 IO 诊断项。
 *
 * @returns {Promise<void>}
 */
async function caseMissingAndIo() {
  const root = await makeFixture("missing");
  try {
    await mkdir(join(root, "skills"), { recursive: true });
    const mod = await loadFixture(root);
    const result = await mod.diagnose();
    check(result.skills === 0 && result.missing.includes("video-studio"), "空 skills/ 必须报 missing=[video-studio]（实际 " + JSON.stringify(result.missing) + "）");
    check(result.healthy === false, "missing 时 healthy=false");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
  const root2 = await makeFixture("io-error");
  try {
    const mod = await loadFixture(root2);
    const result = await mod.diagnose();
    check(result.hashMismatch.length >= 1, "skills/ 读不到时必须记一条 IO 诊断项（实际 " + JSON.stringify(result.hashMismatch) + "）");
    check(result.healthy === false, "IO 诊断项存在时 healthy=false");
  } finally {
    await rm(root2, { recursive: true, force: true });
  }
}

/**
 * 用例 6：`node tools/diagnose.mjs` 的退出码契约（healthy=false -> 1，healthy=true -> 0），
 * 以及 stdout 必须是可解析 JSON（人读版走 --human，不混进 JSON 输出）。
 *
 * @returns {Promise<void>}
 */
async function caseCliExitCode() {
  const root = await makeFixture("cli");
  try {
    // 坏包：frontmatter 缺 description -> healthy=false -> 退出码 1
    await makeSkill(root, { omitDescription: true });
    await mkdir(join(root, "tools"), { recursive: true });
    await copyFile(join(ROOT, "tools", "diagnose.mjs"), join(root, "tools", "diagnose.mjs"));
    const bad = spawnSync(process.execPath, [join(root, "tools", "diagnose.mjs")], { encoding: "utf8" });
    check(bad.status === 1, "诊断 CLI 在 healthy=false 时必须退出 1（实际 " + bad.status + "）");
    let parsed = null;
    try {
      parsed = JSON.parse(bad.stdout);
    } catch {
      parsed = null;
    }
    check(parsed !== null && parsed.healthy === false, "诊断 CLI 的 stdout 必须是可解析 JSON 且 healthy=false");
    // 换成好包 -> 退出码 0
    await makeSkill(root, { refs: { "a.md": "参考 A。" }, body: "正文引用 references/a.md。" });
    const good = spawnSync(process.execPath, [join(root, "tools", "diagnose.mjs")], { encoding: "utf8" });
    check(good.status === 0, "诊断 CLI 在 healthy=true 时必须退出 0（实际 " + good.status + "）");
    check(JSON.parse(good.stdout).healthy === true, "好包 CLI 的 JSON 必须 healthy=true");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}
/**
 * 入口：跑完全部用例并打印计数。
 *
 * @returns {Promise<number>} 进程退出码。
 */
async function main() {
  await caseHealthy();
  await caseDanglingRefs();
  await caseBadFrontmatter();
  await caseNameMismatch();
  await caseMissingAndIo();
  await caseCliExitCode();
  console.log("PASS 诊断自检：" + passed + " 条断言（好包全绿 / 悬空引用 / frontmatter 非法 / name 不一致 / 缺失 / IO 降级 / CLI 退出码）");
  return 0;
}

main().then((code) => {
  process.exitCode = code;
}).catch((error) => {
  console.error("FAIL 诊断自检：" + (error && error.message ? error.message : String(error)));
  process.exitCode = 1;
});
