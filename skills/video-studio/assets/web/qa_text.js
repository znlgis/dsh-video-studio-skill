/* qa_text.js — 浏览器端文字质检（v0.3.0）
 *
 * 逐帧重渲染画布，读回 engine.js 登记的文字包围盒，找出四类问题：
 *   ① overlaps   文字互相压住（同一 group 内的逐字排版不算）
 *   ② offscreen  出画（包围盒越过 1920x1080 边界 6px）
 *   ③ brief      一闪而过（可见时长 < 0.6s）
 *   ④ never      从没露面（需页面用 FK.watch(key, 标签) 声明）
 *
 * 判据改编自 ai-history-film（MIT）video/qa_text.js：
 * 重叠面积比 0.08、出画容差 6px、短命阈值 0.6s。
 */
(function (global) {
  'use strict';

  function r2(v) { return Math.round(v * 100) / 100; }
  function short(s) {
    s = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
    return s.length > 18 ? s.slice(0, 18) + '…' : s;
  }
  function nameOf(q) { return (q.group || q.key) + '「' + short(q.text) + '」'; }

  async function qaText(t0, t1, step) {
    var F = global.FK;
    if (!F) throw new Error('qa_text.js: 找不到 window.FK（engine.js 未加载？）');
    var start = Number(t0) || 0;
    var dur = Number(global.DURATION) || 0;
    var end = (t1 == null || !isFinite(Number(t1))) ? dur : Number(t1);
    var fps = Number(global.FPS) || 30;
    var dt = Math.max(Number(step) || 0.2, 1 / fps);
    var empty = { overlaps: [], offscreen: [], brief: [], never: [], frames: 0 };
    if (end < start) return empty;
    F.qaReset();

    var life = new Map();    // key -> {text, group, first, last, n}
    var overl = new Map();   // 名字对 -> {at, to, ratio}
    var off = new Map();     // 名字   -> {at, to, box}
    var visTotal = 0;

    for (var T = start; T <= end + 1e-6; T += dt) {
      await global.renderFrame(Math.round(T * fps));
      var all = F.texts().filter(function (q) {
        return q.alpha >= 0.3 && (q.x1 - q.x0) >= 2 && (q.y1 - q.y0) >= 2;
      });
      visTotal += all.length;

      for (var i = 0; i < all.length; i++) {
        var q = all[i];
        var L = life.get(q.key);
        if (!L) { L = { text: q.text, group: q.group, first: T, last: T, n: 0 }; life.set(q.key, L); }
        L.last = T;
        L.n += 1;
        if (q.x0 < -6 || q.x1 > F.W + 6 || q.y0 < -6 || q.y1 > F.H + 6) {
          var k = nameOf(q);
          var o = off.get(k);
          if (!o) {
            o = { at: T, to: T, box: [Math.round(q.x0), Math.round(q.y0), Math.round(q.x1), Math.round(q.y1)], el: k };
            off.set(k, o);
          }
          o.to = T;
        }
      }

      for (var a = 0; a < all.length; a++) {
        for (var b = a + 1; b < all.length; b++) {
          var A = all[a];
          var B = all[b];
          if (A.key === B.key) continue;
          if (A.group && A.group === B.group) continue;
          var w = Math.min(A.x1, B.x1) - Math.max(A.x0, B.x0);
          var h = Math.min(A.y1, B.y1) - Math.max(A.y0, B.y0);
          if (w <= 2 || h <= 2) continue;
          var ar = (w * h) / Math.min((A.x1 - A.x0) * (A.y1 - A.y0), (B.x1 - B.x0) * (B.y1 - B.y0));
          if (ar < 0.08) continue;
          var pk = nameOf(A) + ' × ' + nameOf(B);
          var po = overl.get(pk);
          if (!po) { po = { at: T, to: T, ratio: 0 }; overl.set(pk, po); }
          po.to = T;
          if (ar > po.ratio) po.ratio = ar;
        }
      }
    }

    var brief = [];
    life.forEach(function (L, key) {
      var d = L.last - L.first + dt;
      if (d < 0.6) brief.push({ at: r2(L.first), dur: r2(d), el: (L.group || key) + '「' + short(L.text) + '」' });
    });

    var drawn = new Set(F.drawnKeys());
    var never = F.watched()
      .filter(function (w) { return !drawn.has(w.key); })
      .map(function (w) { return w.label; });

    var overlaps = [];
    overl.forEach(function (o, k) { overlaps.push({ at: r2(o.at), to: r2(o.to), ratio: r2(o.ratio), pair: k }); });
    var offscreen = [];
    off.forEach(function (o, k) { offscreen.push({ at: r2(o.at), to: r2(o.to), box: o.box, el: k }); });

    overlaps.sort(function (x, y) { return x.at - y.at; });
    offscreen.sort(function (x, y) { return x.at - y.at; });
    brief.sort(function (x, y) { return x.at - y.at; });
    return { overlaps: overlaps, offscreen: offscreen, brief: brief, never: never, frames: visTotal };
  }

  global.qaText = qaText;
})(window);
