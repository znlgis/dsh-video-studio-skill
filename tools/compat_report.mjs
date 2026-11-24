/**
 * compat_report.mjs — 生成 docs/compatibility.md（`npm run compat:report`）。
 *
 * 契约（plugin-analysis/改造契约 §3–§4）：
 *   - 内容必须来自**真实执行**：跑 tools/plugin_entry_check.mjs 与 tools/ci_check.py，
 *     记录退出码与耗时，不手写"已验证"；
 *   - 缺 DSH 运行时/缺 node/python 导致跑不了时，必须写 `not-run` 与原因，**不得伪造 pass**；
 *   - package.json 的 dsh.compatibility.verified[].evidence 必须是真实存在的脚本路径。
 *
 * 与 ci_check 的分工：ci_check 是"仓库体检"（静态完整性），本脚本是"把兼容性结论
 * 落成一份可复跑的文档"。两者都真跑，报告里的每个数字都来自这一轮的退出码。
 *
 * 用法：npm run compat:report
 * 退出码：0 = 报告已生成且没有执行失败；1 = 有执行失败（报告仍会写出来，便于看原因）。
 * 注意：`not-run`（缺工具/依赖）默认不算失败（与 ci_check 的 SKIP 口径一致），
 * 但会在报告里单列——"没查"和"查了没事"不许混。
 */
import { spawnSync } from "node:child_process";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { diagnose } from "../index.js";

/** 包根（本文件在 tools/ 下）。 */
const ROOT = fileURLToPath(new URL("../", import.meta.url));
/** 报告输出路径。 */
const OUT = join(ROOT, "docs", "compatibility.md");

/**
 * 跑一条命令并记录退出码 / 耗时 / 输出尾行。
 *
 * @param {string} command - 可执行文件。
 * @param {string[]} args - 参数。
 * @param {number} timeoutMs - 超时（毫秒）。
 * @returns {{ok: boolean, notRun: boolean, reason: string, exitCode: number|null, seconds: number, tail: string}} 执行结果。
 */
function runCheck(command, args, timeoutMs) {
  const started = Date.now();
  let result;
  try {
    result = spawnSync(command, args, { cwd: ROOT, encoding: "utf8", timeout: timeoutMs, shell: process.platform === "win32" });
  } catch (error) {
    return { ok: false, notRun: true, reason: "无法启动 " + command + "：" + (error && error.message ? error.message : String(error)), exitCode: null, seconds: 0, tail: "" };
  }
  const seconds = (Date.now() - started) / 1000;
  const text = ((result.stdout ?? "") + (result.stderr ?? "")).trim();
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== "");
  const tail = lines.length > 0 ? lines[lines.length - 1].trim() : "";
  if (result.error) {
    const notRun = result.error.code === "ENOENT";
    return { ok: false, notRun, reason: notRun ? "找不到 " + command + "（这台机器没装）" : "执行失败：" + result.error.message, exitCode: null, seconds, tail };
  }
  return { ok: result.status === 0, notRun: false, reason: result.status === 0 ? "" : "退出码 " + result.status, exitCode: result.status, seconds, tail };
}
/**
 * 校验 dsh.compatibility 声明：证据脚本必须真实存在，字段不许缺。
 *
 * @param {object} pkg - package.json 解析结果。
 * @returns {Promise<{rows: object[], assumed: object[], problems: string[]}>} 声明行与问题清单。
 */
async function checkDeclarations(pkg) {
  const compat = (pkg.dsh && pkg.dsh.compatibility) || {};
  const verified = Array.isArray(compat.verified) ? compat.verified : [];
  const assumed = Array.isArray(compat.assumed) ? compat.assumed : [];
  const rows = [];
  const problems = [];
  if (verified.length === 0) problems.push("dsh.compatibility.verified 为空（至少要有一条实测证据）");
  for (const item of verified) {
    const evidence = typeof item.evidence === "string" ? item.evidence : "";
    let exists = false;
    if (evidence !== "") {
      try {
        await access(join(ROOT, evidence));
        exists = true;
      } catch {
        exists = false;
      }
    }
    if (evidence === "") problems.push("verified 条目缺 evidence");
    else if (!exists) problems.push("verified 条目的 evidence 不存在：" + evidence);
    rows.push({ version: item.version ?? "?", date: item.date ?? "?", result: item.result ?? "?", evidence, exists });
  }
  for (const item of assumed) {
    if (typeof item.range !== "string" || item.range === "") problems.push("assumed 条目缺 range");
    if (typeof item.reason !== "string" || item.reason === "") problems.push("assumed 条目缺 reason");
  }
  return { rows, assumed: assumed.map((item) => ({ range: item.range ?? "?", reason: item.reason ?? "?" })), problems };
}
/**
 * 渲染 Markdown 报告。
 *
 * @param {object} data - 汇总数据。
 * @returns {string} Markdown 文本。
 */
function render(data) {
  const lines = [];
  lines.push("# 兼容性报告（DSH）");
  lines.push("");
  lines.push("> 本文件由 `npm run compat:report`（`tools/compat_report.mjs`）**生成**，不是手写。");
  lines.push("> 生成时间：" + data.generatedAt + "（" + data.timezone + "）");
  lines.push("> 生成器版本：" + data.pkgName + " " + data.pkgVersion);
  lines.push("> 宿主版本提示：" + String(data.hostVersionHint) + "（diagnose().hostVersionHint；只认 DSH_VERSION 与 DSH_INSTALL_DIR/package.json，探不到就是 null）");
  lines.push("");
  lines.push("判据与证据分离：package.json 的 engines.dsh 是 npm 侧判据，dsh.compatibility 是证据侧；两者不合并。");
  lines.push("");
  lines.push("## 1 声明：dsh.compatibility");
  lines.push("");
  lines.push("### verified（实测通过，evidence 是真实存在的校验脚本）");
  lines.push("");
  lines.push("| DSH 版本 | 日期 | 结果 | evidence | evidence 是否存在 |");
  lines.push("|---|---|---|---|---|");
  if (data.verified.length === 0) {
    lines.push("| — | — | — | — | 无声明 |");
  } else {
    for (const row of data.verified) {
      lines.push("| " + row.version + " | " + row.date + " | " + row.result + " | " + row.evidence + " | " + (row.exists ? "是" : "**否**") + " |");
    }
  }
  lines.push("");
  lines.push("### assumed（engines.dsh 声明范围内、未逐一实测的版本）");
  lines.push("");
  lines.push("| 范围 | 原因 |");
  lines.push("|---|---|");
  if (data.assumed.length === 0) {
    lines.push("| — | 无 |");
  } else {
    for (const row of data.assumed) lines.push("| " + row.range + " | " + row.reason + " |");
  }
  lines.push("");
  lines.push("## 2 本轮真实执行");
  lines.push("");
  lines.push("| 检查 | 命令 | 退出码 | 耗时 | 结果 | 输出尾行 |");
  lines.push("|---|---|---|---|---|---|");
  for (const check of data.checks) {
    const result = check.notRun ? "**not-run**" : (check.ok ? "pass" : "**fail**");
    const tail = check.tail === "" ? "" : "`" + check.tail.replace(/[|]/g, "\\|").slice(0, 160) + "`";
    lines.push("| " + check.name + " | " + check.command + " | " + (check.notRun ? "—" : String(check.exitCode)) + " | "
      + check.seconds.toFixed(2) + " s | " + result + " | " + tail + " |");
  }
  lines.push("");
  lines.push("`not-run` = 这台机器跑不了（缺 node / python / DSH 运行时），**不等于通过**；`fail` 才是结论不成立。");
  if (data.notRunNotes.length > 0) {
    lines.push("");
    lines.push("未执行/声明问题：");
    for (const note of data.notRunNotes) lines.push("- " + note);
  }
  lines.push("");
  lines.push("## 3 怎么复跑");
  lines.push("");
  lines.push("```bash");
  lines.push("npm run compat:report                # 重新生成本文件（覆盖）");
  lines.push("node tools/plugin_entry_check.mjs    # 只跑入口/配置面/诊断冒烟");
  lines.push("python tools/ci_check.py -v          # 仓库体检 18 项");
  lines.push("npm run diagnose                     # 一行诊断（JSON；healthy=false 退 1）");
  lines.push("```");
  lines.push("");
  return lines.join("\n");
}
/**
 * 入口：校验声明 -> 真跑两项检查 -> 写 docs/compatibility.md。
 *
 * @returns {Promise<number>} 进程退出码。
 */
async function main() {
  const pkg = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
  const decl = await checkDeclarations(pkg);
  const checks = [];

  const entry = runCheck(process.execPath, [join(ROOT, "tools", "plugin_entry_check.mjs")], 180000);
  checks.push({ name: "插件入口 / 配置面 / 诊断冒烟", command: "node tools/plugin_entry_check.mjs", ...entry });

  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  const repo = runCheck(python, [join(ROOT, "tools", "ci_check.py")], 600000);
  checks.push({ name: "仓库体检（18 项）", command: python + " tools/ci_check.py", ...repo });

  const diag = await diagnose().catch(() => null);
  const notRunNotes = checks.filter((check) => check.notRun).map((check) => check.name + "：" + check.reason);
  notRunNotes.push(...decl.problems.map((problem) => "声明校验：" + problem));

  const now = new Date();
  const offsetMinutes = -now.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  const pad = (value) => String(value).padStart(2, "0");
  const timezone = "UTC" + sign + pad(Math.floor(abs / 60)) + ":" + pad(abs % 60);
  const markdown = render({
    generatedAt: now.toISOString().replace("T", " ").replace(/[.]\d+Z$/, " UTC"),
    timezone,
    pkgName: pkg.name ?? "?",
    pkgVersion: pkg.version ?? "?",
    hostVersionHint: diag ? diag.hostVersionHint : null,
    verified: decl.rows,
    assumed: decl.assumed,
    checks,
    notRunNotes,
  });
  await mkdir(dirname(OUT), { recursive: true });
  await writeFile(OUT, markdown, "utf8");
  const failed = checks.filter((check) => !check.ok && !check.notRun);
  const summary = checks.map((check) => check.name + "=" + (check.notRun ? "not-run" : (check.ok ? "pass" : "fail"))).join(" / ");
  console.log("生成 " + OUT + "：" + summary);
  if (decl.problems.length > 0) {
    console.error("声明问题：" + decl.problems.join("；"));
    return 1;
  }
  return failed.length > 0 ? 1 : 0;
}

main().then((code) => {
  process.exitCode = code;
}).catch((error) => {
  console.error("FAIL 兼容性报告：" + (error && error.message ? error.message : String(error)));
  process.exitCode = 1;
});




