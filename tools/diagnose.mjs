/**
 * diagnose.mjs — 一行诊断入口（`npm run diagnose`，契约见 plugin-analysis/改造契约）。
 *
 * 为什么需要它：插件装好之后，用户/Agent 无法回答"我的技能为什么没生效"。
 * 这里把 index.js 的只读 diagnose() 打到 stdout：技能数 / 可见数 / 缺失 / 降级原因 /
 * 悬空 references 引用 / 技能目录 token 估算 / 宿主版本提示 / healthy。
 *
 * 用法：
 *   npm run diagnose            # JSON 到 stdout；healthy=true 退出 0，否则 1
 *   npm run diagnose -- --human # 人读版（同样按 healthy 给退出码）
 *   node -e "const m = await import('./index.js'); console.log(JSON.stringify(await m.diagnose()))"
 *
 * 只读：diagnose() 不写盘、不联网、不抛错（内部异常会变成诊断项）。
 */
import { diagnose } from "../index.js";

/** 是否要人读版（`--human`）。 */
const HUMAN = process.argv.includes("--human");

/**
 * 把诊断结果渲染成人读文本。
 *
 * @param result - diagnose() 的返回值。
 * @returns {string} 多行中文说明。
 */
function renderHuman(result) {
  const lines = [];
  lines.push("插件：" + result.provider + " " + (result.packageVersion ?? "（版本未知）"));
  lines.push("技能：" + result.skills + " 个 / 对模型可见 " + result.visible + " 个"
    + "；目录估算 " + result.catalogTokens + " token");
  lines.push("宿主版本提示：" + (result.hostVersionHint ?? "探不到（null）"));
  lines.push("健康：" + (result.healthy ? "是（missing/degraded/danglingRefs/hashMismatch 全空）" : "否"));
  for (const item of result.missing) lines.push("  缺失技能：" + item);
  for (const item of result.degraded) lines.push("  降级技能：" + item.skill + "（" + item.reason + "）");
  for (const item of result.danglingRefs) lines.push("  悬空引用：" + item.from + " -> " + item.ref);
  for (const item of result.hashMismatch) lines.push("  哈希/IO 异常：" + item);
  return lines.join("\n");
}

/**
 * 入口：打印诊断并按 healthy 设置退出码。
 *
 * @returns {Promise<number>} 进程退出码。
 */
async function main() {
  const result = await diagnose();
  if (HUMAN) {
    console.log(renderHuman(result));
  } else {
    // 契约：JSON 到 stdout（不混人读文本，方便 `npm run diagnose | jq`）。
    console.log(JSON.stringify(result, null, 2));
  }
  return result.healthy ? 0 : 1;
}

main().then((code) => {
  process.exitCode = code;
}).catch((error) => {
  // diagnose() 契约上不抛错；真抛了也要给一行可读错误，而不是堆栈。
  console.error("FAIL diagnose：" + (error && error.message ? error.message : String(error)));
  process.exitCode = 1;
});
