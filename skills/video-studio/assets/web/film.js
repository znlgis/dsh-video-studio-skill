/* film.js — 场景模板（v0.3.0）
 *
 * 3 个场景 / 默认 12 秒（global.DURATION 由场景表推出，见下方 resolveDuration：
 * 加一幕、改一幕时长都不用手工同步常量；要锁死就写 window.WINDOW_DURATION）。
 * 这是**独立的 12 秒演示片**，不是"随便配一份 film.json 就能出全片"的成品页面：
 * 换剧本必须自己重写场景，让画面覆盖到片尾，再按需写 WINDOW_DURATION。
 *
 * 示范 5 件事：
 *   ① 逐字入场（0–4s）  ② 数字滚动（4–8s）  ③ 粒子 / 发光（8–12s）
 *   ④ HUD 标注（贯穿全片）  ⑤ cue / event 登记（由 window.getCues()/getEvents() 导出）
 *
 * 纪律：每帧都是「帧号 → 画面」的纯函数——不读上一帧状态、不用 Math.random / Date.now。
 * 画面里的所有文字都走 FK.g.text()，qaText 才能看到它们的包围盒。
 * 改编自 ai-history-film（MIT）video/web2 的场景组织方式（逐字入场 / 计数器 / HUD 的写法）。
 */
(function (global) {
  'use strict';
  var F = global.FK;
  if (!F) throw new Error('film.js: engine.js 未加载');
  var W = F.W, H = F.H, g = F.g, E = F.E;
  var seg = F.seg, lerp = F.lerp, clamp = F.clamp;

  /* 成片秒数（webengine.py / render.py 会读它做分块与校验）。
   * 默认由场景表推出 = 最后一个场景的 end（加/删/改场景后自动跟着变，不会忘记同步）；
   * 要显式锁死（例如页面里还有场景以外的动画），在 index.html 里先写
   *   <script>window.WINDOW_DURATION = 12.0;</script>
   * 或在引入 film.js 之前赋值即可覆盖。使用说明见 references/web-engine.md §3。 */
  function resolveDuration(F) {
    var explicit = Number(global.WINDOW_DURATION);
    if (isFinite(explicit) && explicit > 0) return explicit;
    // 也认"在 film.js 之前直接写 window.DURATION = 秒数"的写法：render.py 的报错就是
    // 教用户改 window.DURATION（页面里的时长必须等于 timeline.total）。
    // engine.js 自己写的兜底值不算显式，否则模板永远锁死在 12 秒，加场景也不生效。
    var pre = Number(global.DURATION);
    if (isFinite(pre) && pre > 0 && pre !== Number(F.defaultDuration)) return pre;
    return F.timelineEnd();
  }

  var C = {
    ink: '#eaf2ff',
    dim: '#8ea4c0',
    accent: [255, 140, 66],
    cyan: [90, 210, 255],
    warm: [255, 196, 120]
  };

  /* qaText 的「从没露面」检查要先声明 key：必须与 g.text({key}) 对齐 */
  F.watch('open.title', '标题：逐帧确定性');
  F.watch('open.sub', '副标题：Canvas 2D 极简时间轴');
  F.watch('data.num', '数字滚动：0 → 1920');
  F.watch('data.cap', '说明：同一帧号 → 同一张图');
  F.watch('fx.title', '标题：确定性渲染');
  F.watch('fx.hud', 'HUD：粒子数 / 帧率标注');
  F.watch('hud.ch', 'HUD：章节名');
  F.watch('hud.tc', 'HUD：时间码');
  F.watch('hud.brand', 'HUD：品牌名');

  /* ================================================================ 背景层（最先注册 → 最先画） */
  F.always(function (t) {
    g.bgGradient([[0, '#0b0e14'], [0.55, '#0e1624'], [1, '#0a0d13']]);
    g.grid({ t: t, step: 120, alpha: 0.045, color: C.cyan, drift: 6 });
    g.vignette(0.5);
  });

  /* ================================================================ 场景 1：逐字入场（0–4s） */
  F.scene('open', 0, 4.0, function (S) {
    // ⚠ 命名约定（见 references/frame-qa.md §2.5）：
    //   qa_frames 的音画同步**先按 kind == cue 的配方名配对**。
    //   所以每个"该响一下"的画面事件，kind 要写成**同一个配方名**（ui_blip / whoosh / impact …）；
    //   只有纯装饰性的视觉事件（交界黑场）才用 flash 这类通用名。
    //   底下每条 S.cue 都配了一条同名 S.event —— 这样同步检查才真的在查，而不是静默全过。
    S.cue(0.10, 'ui_blip', { pitch: 2200, gain: -4 });
    S.event(0.10, 'ui_blip', { a: 0.5 });
    S.cue(0.34, 'ui_blip', { pitch: 2600, gain: -6 });
    S.event(0.34, 'ui_blip', { a: 0.5 });
    S.cue(1.05, 'whoosh', { dur: 0.55, size: 0.6, gain: -10 });
    S.event(1.05, 'whoosh', { size: 0.6 });            // 强调线"画出来"的同时一声掠过
    S.event(3.70, 'flash', { a: 0.18 });               // 4s 交界黑场起坡（纯视觉，无对应音效）

    var TITLE = '逐帧确定性';
    var SIZE = 150;
    var TRACK = 8;

    S.update(function (t) {
      // ① 逐字入场：每个字从下方 90px 升起 + 淡入，错峰 0.075s
      var lay = g.layoutChars(TITLE, SIZE, '700', null, TRACK);
      var x = (W - lay.width) / 2;
      var y = 470;
      for (var i = 0; i < lay.chars.length; i++) {
        var p = E.out5(seg(t, 0.16 + i * 0.075, 0.72 + i * 0.075));
        var a = seg(t, 0.16 + i * 0.075, 0.44 + i * 0.075);
        g.text(lay.chars[i].ch, x + lay.chars[i].x, y + (1 - p) * 90, {
          size: SIZE, weight: '700', color: C.ink, alpha: a,
          key: 'open.title.' + i, group: 'open.title'
        });
      }
      // 标题下方一条「画出来」的强调线
      var lw = E.out3(seg(t, 1.15, 1.9)) * 360;
      g.rect(W / 2 - lw / 2, y + 118, lw, 4, 'rgba(255,140,66,0.9)');
      // 副标题淡入 + 上浮
      var a2 = E.out3(seg(t, 1.35, 2.05));
      g.text('Canvas 2D · 极简时间轴 · 无 GSAP / three.js', W / 2, 646 + (1 - a2) * 26, {
        size: 40, weight: '400', color: C.dim, align: 'center', alpha: a2,
        key: 'open.sub', group: 'open.sub'
      });
      // 两侧装饰竖线（"长出来"）
      var dw = E.out3(seg(t, 1.6, 2.2));
      if (dw > 0.001) {
        g.rect(196, 430 + (1 - dw) * 60, 2, 120 * dw, 'rgba(90,210,255,0.55)');
        g.rect(W - 198, 430 + (1 - dw) * 60, 2, 120 * dw, 'rgba(90,210,255,0.55)');
      }
    });
  });

  /* ================================================================ 场景 2：数字滚动（4–8s） */
  F.scene('data', 4.0, 4.0, function (S) {
    // 同上：cue 与 event 成对出现、同名，同步检查才有东西可配
    S.cue(0.10, 'data_chirp', { gain: -4 });
    S.event(0.10, 'data_chirp', {});
    S.cue(1.85, 'ui_click', { gain: -5 });
    S.event(1.85, 'ui_click', {});                     // 数字滚完落定的一声"咔"
    S.cue(2.12, 'relay_click', { gain: -12 });
    S.event(2.12, 'relay_click', {});

    S.update(function (t) {
      // ② 数字滚动：0 → 1920，走 out5 缓动（声音 chirp 与它同一条时间线）
      var p = E.out5(seg(t, 0.25, 1.85));
      var num = Math.round(lerp(0, 1920, p));
      var s = String(num);
      while (s.length < 4) s = '0' + s;
      g.text(s, W / 2, 470, {
        size: 236, weight: '700', align: 'center', color: '#f6f9ff',
        key: 'data.num', group: 'data.num'
      });
      g.text('FRAME-STABLE CUTS', W / 2, 606, {
        size: 26, weight: '500', align: 'center', color: 'rgba(90,210,255,0.85)',
        alpha: E.out3(seg(t, 0.9, 1.4)), key: 'data.kicker', group: 'data.kicker'
      });
      // 说明文字
      var a = E.out3(seg(t, 1.05, 1.75));
      g.text('同一帧号 → 同一张图', W / 2, 700 + (1 - a) * 20, {
        size: 44, weight: '400', align: 'center', color: C.dim, alpha: a,
        key: 'data.cap', group: 'data.cap'
      });
      // 进度条（数字滚动的可视化）
      var bw = 720, bx = (W - bw) / 2, by = 792;
      g.rect(bx, by, bw, 3, 'rgba(234,242,255,0.12)');
      g.rect(bx, by, bw * p, 3, 'rgba(255,140,66,0.95)');
      // ④ HUD 标注：右上角小徽标
      var ha = E.out3(seg(t, 0.5, 1.0));
      if (ha > 0.001) {
        g.text('◆ DATA LAYER', W - 64, 206, {
          size: 22, weight: '600', align: 'right', color: 'rgba(255,140,66,0.9)',
          alpha: ha, key: 'data.hud', group: 'data.hud'
        });
        g.text('counter → 1920', W - 64, 238, {
          size: 18, weight: '400', align: 'right', color: 'rgba(234,242,255,0.45)',
          alpha: ha, key: 'data.hud2', group: 'data.hud'
        });
      }
    });
  });

  /* ================================================================ 场景 3：粒子 / 发光（8–12s） */
  F.scene('fx', 8.0, 4.0, function (S) {
    S.event(-0.30, 'flash', { a: 0.18 });   // 8s 交界黑场起坡（本幕负偏移 = 上一幕末帧）
    S.cue(0.02, 'reverse_swell', { gain: -4, dur: 1.2 });
    S.event(0.02, 'reverse_swell', {});                // 吸气 = 画面从黑里亮起来
    S.cue(0.62, 'impact', { size: 1.15, gain: -1 });
    S.event(0.62, 'impact', { a: 0.8 });               // 重击：白闪 + 冲击环同一时刻
    S.event(0.62, 'ring', { r: 620 });                 // 纯装饰（无对应音效）
    S.cue(1.55, 'ui_blip', { pitch: 1800, gain: -8 });
    S.event(1.55, 'ui_blip', { a: 0.5 });

    S.update(function (t) {
      // ③ 粒子 / 发光：位置全部由 hash(seed,i,*) 决定，同一 t 永远同一张图
      g.particles({ n: 150, seed: 20261107, t: t, color: C.accent, alpha: 0.5, speed: 1.0, rmin: 2, rmax: 6, wobble: 30 });
      // 冲击环：impact 时刻从中心扩散
      var rp = E.out5(seg(t, 0.62, 2.3));
      if (rp > 0.001 && rp < 0.999) {
        g.ring(W / 2, 520, lerp(40, 640, rp), 'rgba(255,196,120,' + (1 - rp) * 0.55 + ')', 3);
      }
      // 中心发光：正弦呼吸，相位只由绝对时间 T 决定
      var T = 8.0 + t;
      g.glow(W / 2, 520, 250, C.accent, 0.18 + 0.07 * Math.sin(T * 2.0));
      g.glow(W / 2, 520, 130, C.warm, 0.10 + 0.05 * Math.sin(T * 2.6 + 1.2));
      // 标题逐字入场（复用同一套缓动）
      var lay = g.layoutChars('确定性渲染', 132, '700', null, 10);
      var x = (W - lay.width) / 2;
      var y = 520;
      for (var i = 0; i < lay.chars.length; i++) {
        var p = E.out5(seg(t, 1.05 + i * 0.07, 1.55 + i * 0.07));
        g.text(lay.chars[i].ch, x + lay.chars[i].x, y + (1 - p) * 60, {
          size: 132, weight: '700', color: '#ffffff', alpha: p,
          key: 'fx.title.' + i, group: 'fx.title'
        });
      }
      // HUD 标注
      g.text('PARTICLES 150 · 30 FPS · 同一帧同一图', W / 2, 660, {
        size: 28, weight: '400', align: 'center', color: 'rgba(234,242,255,0.66)',
        alpha: E.out3(seg(t, 1.3, 1.9)), key: 'fx.hud', group: 'fx.hud'
      });
      // 白闪：impact 事件的可视化（event 已登记同一条时间；只从 impact 时刻开始衰减）
      var fp = seg(t, 0.62, 0.92);
      if (t >= 0.62 && fp < 0.999) g.flash(0.55 * (1 - fp), '#ffffff');
      // 片尾淡出到黑
      var fo = E.in2(seg(t, 3.35, 4.0));
      if (fo > 0.001) g.flash(fo, '#000000');
    });
  });

  /* ================================================================ 时间轴总长（DURATION） */
  /* 必须在所有 F.scene(...) 注册完之后求值：resolveDuration 读的就是场景表。
   * 放在 HUD 之前、注册之后，这样 HUD 里读 global.DURATION 时它已经是正确值（boot 在最后）。 */
  global.DURATION = resolveDuration(F);

  /* ================================================================ HUD 层（最后注册 → 最后画，压在所有场景之上） */
  F.always(function (t) {
    var dur = Number(global.DURATION) || 12.0;
    g.text('DSH VIDEO STUDIO', 64, 60, {
      size: 24, weight: '500', color: 'rgba(234,242,255,0.55)', key: 'hud.brand'
    });
    var chap = t < 4 ? '01 · 逐字入场' : (t < 8 ? '02 · 数字滚动' : '03 · 粒子 / 发光');
    g.text(chap, 64, 96, {
      size: 22, weight: '500', color: 'rgba(90,210,255,0.8)', key: 'hud.ch', group: 'hud.ch'
    });
    // 时间码每帧都在变 → 也保证「不同帧渲染结果不同」
    var tc = 'T ' + (t < 10 ? '0' : '') + t.toFixed(2) + ' / ' + dur.toFixed(2);
    g.text(tc, W - 64, 60, {
      size: 22, weight: '500', align: 'right', color: 'rgba(234,242,255,0.45)',
      key: 'hud.tc', group: 'hud.tc'
    });
    // 顶部进度线
    g.rect(64, 122, W - 128, 1, 'rgba(90,210,255,0.16)');
    g.rect(64, 122, (W - 128) * clamp(t / dur, 0, 1), 2, 'rgba(255,140,66,0.85)');
    // 底部品牌行
    g.text('v0.3.0 · Canvas2D 逐帧渲染 · 不使用 GSAP / three.js', 64, H - 52, {
      size: 20, weight: '400', color: 'rgba(234,242,255,0.3)', key: 'hud.foot'
    });
    // 场景交界黑场「呼吸」（4s / 8s），避免硬切
    var d = Math.min(Math.abs(t - 4.0), Math.abs(t - 8.0));
    var dip = clamp(1 - d / 0.3);
    if (dip > 0.001) g.flash(0.5 * E.out2(dip), '#000000');
    // ↑ 这条在 dip 的"起坡帧"会有一个可见的亮度跳变（clamp 从 0 开始斜升）。
    //   对观众来说它是转场的一部分，但对逐帧质检它长得像单帧闪烁：
    //   所以把起坡时刻登记成 flash 事件，qa_frames 会把它从 flicker 里排除（见 FLICKER_EXPLAIN_WIN）。
  });
})(window);
