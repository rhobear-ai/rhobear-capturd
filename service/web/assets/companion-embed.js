/* ==========================================================================
   RHOBEAR COMPANION \u2014 drop-in chatbot embed  ("Rho")  v2
   --------------------------------------------------------------------------
   One self-contained file. Adds the living Rho orb + the FULL chatbot to ANY
   page \u2014 no build, no deps, no framework:

     - Chat panel with the Adobe chat bus: plus menu (attach image / new chat /
       copy), deep-thinking toggle, dictate button (speech-to-text INTO the
       composer \u2014 stays in normal chat), send button.
     - "Talk to Rho" call button → THE BIG ONE: fullscreen voice surface.
       Continuous listening, streamed replies spoken aloud sentence-by-sentence
       (POST /api/tts), tap the orb to interrupt (/api/interrupt).
     - Live crew visibility: tool / agent / task SSE events render as working
       chips in the thread, marked done as results land.
     - Personalize: accent color + voice picker (persisted per browser).
     - Living orb: shimmer, hue drift, breath \u2014 never a stagnant dot.

   MOUNT:
     <script defer src="/companion-embed.js"></script>
     <script>
       window.RHOBEAR_COMPANION = {
         endpoint: 'https://workbench.rhobear.ai/companion',
         ready:    true,
         accent:   '#7c5cff',  // surface accent (user may re-tint in settings)
         title:    'Rho',
         greeting: "Hey \u2014 I'm Rho. Ask me anything about your RHOBEAR."
       };
     </script>

   Transport matches the companion server exactly: POST {endpoint}/api/chat with
   { text, sessionId, chatId, mode, image? } -> SSE stream of
   `event: session|delta|tool|tool_done|agent|task|done|error|close`.
   Sellable-by-default: no keys, no founder identity, no product secrets here.
   ========================================================================== */
(function () {
  'use strict';
  if (window.__rhoEmbedLoaded) return;      // never double-mount
  window.__rhoEmbedLoaded = '2.1';

  // ---- config -------------------------------------------------------------
  var scriptEl = document.currentScript;
  function attr(name, fallback) {
    var v = scriptEl && scriptEl.getAttribute('data-' + name);
    return v == null ? fallback : v;
  }
  var CFG = window.RHOBEAR_COMPANION || {};
  var ENDPOINT = (CFG.endpoint != null ? CFG.endpoint : attr('endpoint', '')).replace(/\/+$/, '');
  var READY    = CFG.ready != null ? !!CFG.ready : (attr('ready', 'false') === 'true');
  var TITLE    = CFG.title   || attr('title', 'Rho');
  // Unicode escapes (\u2014 etc) everywhere below: hosts may serve this file
  // without a UTF-8 charset header and raw em-dashes render as mojibake.
  var GREETING = CFG.greeting || attr('greeting', "Hey \u2014 I'm " + TITLE + ". Ask me anything.");
  var WARMING  = "I'm warming up \u2014 the crew's plugging me in right here. Almost ready. Hang tight and I'll be answering in this very bar.";

  // Personalization survives reloads; the surface accent is only the default.
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  var SURFACE_ACCENT = CFG.accent || attr('accent', '#7c5cff');
  var ACCENT = lsGet('rho.accent') || SURFACE_ACCENT;
  var VOICES = ['Charon', 'Puck', 'Kore', 'Fenrir', 'Aoede', 'Leda', 'Orus', 'Zephyr'];
  var VOICE = lsGet('rho.voice') || 'Charon';
  var SWATCHES = [SURFACE_ACCENT, '#7c5cff', '#3bd6c3', '#f2545b', '#e0a63a', '#4f8ff7', '#a06df7'];

  // Derive the companion hues from the accent so every gradient is harmonious
  // on ANY chosen color (config `accent2` can still override the default).
  function hueShift(hex, deg, satBoost, lightBoost) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return hex;
    var n = parseInt(m[1], 16), r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, h = 0;
    var l = (mx + mn) / 2, s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
    if (d !== 0) {
      if (mx === r) h = 60 * (((g - b) / d) % 6);
      else if (mx === g) h = 60 * ((b - r) / d + 2);
      else h = 60 * ((r - g) / d + 4);
    }
    h = (h + deg + 360) % 360;
    s = Math.min(1, Math.max(0, s + (satBoost || 0)));
    l = Math.min(0.9, Math.max(0.1, l + (lightBoost || 0)));
    var c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), mm = l - c / 2;
    var rr = [c, x, 0, 0, x, c][Math.floor(h / 60) % 6], gg = [x, c, c, x, 0, 0][Math.floor(h / 60) % 6], bb = [0, 0, x, c, c, x][Math.floor(h / 60) % 6];
    function ch(v) { return ('0' + Math.round((v + mm) * 255).toString(16)).slice(-2); }
    return '#' + ch(rr) + ch(gg) + ch(bb);
  }

  // ---- state --------------------------------------------------------------
  var sessionId = null;
  try { sessionId = localStorage.getItem('rho.session') || null; } catch (e) {}
  var activeAbort = null, activeChatId = null, greeted = false;
  var pendingImage = null; // data URL waiting to ride the next send

  // ---- identity: Rho is tied to RHOBEAR credits ----------------------------
  // Same-origin hosts ride the cw_sess cookie automatically; cross-origin
  // embeds (Plans, the desktop hub) hold a companion-minted bearer token
  // handed back by the /api/auth/start popup (postMessage, origin-checked).
  var TOKEN = lsGet('rho.token') || null;
  var auth = { required: false, signedIn: true, checked: false, signin: '' };
  var EP_ORIGIN = (function () {
    try { return new URL(ENDPOINT || '/', location.href).origin; } catch (e) { return location.origin; }
  })();
  function authHeaders() {
    var h = { 'Content-Type': 'application/json' };
    if (TOKEN) h['Authorization'] = 'Bearer ' + TOKEN;
    return h;
  }
  var GOOGLE_G = '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>';

  // ---- styles -------------------------------------------------------------
  var css = `
  #rho-embed, #rho-embed * { box-sizing: border-box; }
  #rho-embed {
    --rho-a: ${ACCENT};
    --rho-a2: ${CFG.accent2 || hueShift(ACCENT, 42, 0.08, 0.04)};
    --rho-a3: ${hueShift(ACCENT, -28, 0.05, 0.10)};
    position: fixed; z-index: 2147483000;
    font-family: 'Assistant', system-ui, -apple-system, 'Segoe UI', sans-serif;
  }

  /* ---- launcher: the living orb (no glyph \u2014 the orb IS the brand) ---- */
  #rho-launch {
    position: fixed; right: 20px; bottom: 20px; z-index: 2147483000;
    width: 60px; height: 60px; border-radius: 50%; border: none; cursor: pointer;
    padding: 0; background: transparent;
    transition: transform .18s cubic-bezier(.34,1.56,.64,1);
    animation: rho-float 4.6s ease-in-out infinite;
  }
  #rho-launch:hover { transform: scale(1.08); }
  #rho-launch:active { transform: scale(.93); }
  #rho-launch.rho-hidden { display: none; }
  .rho-orbcore, .rho-orbrim, .rho-orbspec, .rho-orbsheen { position: absolute; inset: 0; border-radius: 50%; pointer-events: none; }
  .rho-orbcore {
    background:
      radial-gradient(circle at 33% 30%, rgba(255,255,255,.9), rgba(255,255,255,.18) 18%, transparent 42%),
      radial-gradient(circle at 68% 74%, var(--rho-a2), transparent 62%),
      radial-gradient(circle at 30% 70%, var(--rho-a3), transparent 58%),
      radial-gradient(circle at 60% 40%, var(--rho-a), #0c0a1f 88%);
    box-shadow:
      0 12px 34px rgba(0,0,0,.45),
      0 0 30px color-mix(in srgb, var(--rho-a) 50%, transparent),
      inset 0 1px 3px rgba(255,255,255,.5);
    animation: rho-breath 3.4s ease-in-out infinite, rho-hue 14s linear infinite;
  }
  .rho-orbrim {
    inset: -1px;
    background: conic-gradient(from 0deg, var(--rho-a), var(--rho-a2), #fff, var(--rho-a3), var(--rho-a));
    -webkit-mask: radial-gradient(circle, transparent 62%, #000 67%);
    mask: radial-gradient(circle, transparent 62%, #000 67%);
    opacity: .85; filter: blur(.4px);
    animation: rho-spin 7s linear infinite;
  }
  .rho-orbspec {
    background: radial-gradient(circle at 30% 26%, rgba(255,255,255,.92), transparent 16%),
                radial-gradient(circle at 74% 66%, rgba(255,255,255,.35), transparent 10%);
    animation: rho-drift 5.2s ease-in-out infinite alternate;
  }
  .rho-orbsheen {
    background: linear-gradient(115deg, transparent 30%, rgba(255,255,255,.35) 48%, transparent 62%);
    background-size: 260% 260%;
    animation: rho-sheen 4.8s ease-in-out infinite;
    mix-blend-mode: screen;
  }
  @keyframes rho-spin { to { transform: rotate(360deg); } }
  @keyframes rho-hue { to { filter: hue-rotate(360deg); } }
  @keyframes rho-sheen { 0%, 55% { background-position: 120% 120%; } 85%, 100% { background-position: -60% -60%; } }
  @keyframes rho-float { 0%,100% { translate: 0 0; } 50% { translate: 0 -5px; } }
  @keyframes rho-drift { from { transform: translate(0,0); } to { transform: translate(2px,2px); } }
  @keyframes rho-breath {
    0%,100% { box-shadow: 0 12px 34px rgba(0,0,0,.45), 0 0 24px color-mix(in srgb, var(--rho-a) 38%, transparent), inset 0 1px 3px rgba(255,255,255,.5); }
    50%     { box-shadow: 0 12px 34px rgba(0,0,0,.45), 0 0 42px color-mix(in srgb, var(--rho-a) 62%, transparent), inset 0 1px 3px rgba(255,255,255,.5); }
  }
  @media (prefers-reduced-motion: reduce) {
    #rho-launch, .rho-orbcore, .rho-orbrim, .rho-orbspec, .rho-orbsheen { animation: none !important; }
  }

  /* ---- panel: glass with a living accent aura ---- */
  #rho-panel {
    position: fixed; right: 20px; bottom: 20px; z-index: 2147483001;
    width: min(400px, calc(100vw - 32px));
    height: min(640px, calc(100vh - 40px));
    display: none; flex-direction: column; overflow: hidden;
    border-radius: 24px;
    background: rgba(10,9,20,.9);
    backdrop-filter: blur(24px) saturate(1.25); -webkit-backdrop-filter: blur(24px) saturate(1.25);
    border: 1px solid rgba(255,255,255,.11);
    box-shadow: 0 26px 80px rgba(0,0,0,.62), 0 0 44px color-mix(in srgb, var(--rho-a) 20%, transparent);
    color: #fff; isolation: isolate;
  }
  #rho-panel::before {
    content: ""; position: absolute; inset: -30% -20% auto -20%; height: 70%; z-index: -1;
    background:
      radial-gradient(ellipse at 30% 20%, color-mix(in srgb, var(--rho-a) 34%, transparent), transparent 62%),
      radial-gradient(ellipse at 75% 35%, color-mix(in srgb, var(--rho-a2) 26%, transparent), transparent 58%);
    filter: blur(30px); pointer-events: none;
    animation: rho-aura 11s ease-in-out infinite alternate;
  }
  @keyframes rho-aura { from { transform: translate(-3%,0) scale(1); opacity:.8; } to { transform: translate(3%,4%) scale(1.08); opacity:1; } }
  #rho-embed.rho-open #rho-panel { display: flex; animation: rho-rise .26s cubic-bezier(.21,1.02,.55,1); }
  @keyframes rho-rise { from { opacity: 0; transform: translateY(14px) scale(.97); } to { opacity: 1; transform: none; } }

  #rho-head { display: flex; align-items: center; gap: 10px; padding: 14px 14px 12px; flex-shrink: 0; border-bottom: 1px solid rgba(255,255,255,.07); position: relative; }
  .rho-head-orb { position: relative; width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0; }
  .rho-head-orb .rho-orbrim { animation-duration: 9s; }
  #rho-embed.rho-busy .rho-head-orb { animation: rho-pulse 1.1s ease-in-out infinite; }
  @keyframes rho-pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.12); } }
  #rho-head .rho-name { font-weight: 700; font-size: 15px; letter-spacing: .2px; }
  #rho-head .rho-chip {
    font-size: 9.5px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase;
    padding: 3px 8px; border-radius: 9px;
    background: linear-gradient(120deg, rgba(251,146,60,.2), rgba(251,146,60,.1));
    border: 1px solid rgba(251,146,60,.28); color: rgba(251,191,120,.95);
    animation: rho-chipglow 2.6s ease-in-out infinite;
  }
  @keyframes rho-chipglow { 0%,100% { box-shadow: 0 0 0 transparent; } 50% { box-shadow: 0 0 12px rgba(251,146,60,.28); } }
  #rho-head .rho-spacer { flex: 1; }
  .rho-hbtn {
    width: 30px; height: 30px; border: 1px solid rgba(255,255,255,.1); border-radius: 10px; cursor: pointer;
    background: linear-gradient(160deg, rgba(255,255,255,.09), rgba(255,255,255,.03));
    color: rgba(255,255,255,.75); display: flex; align-items: center; justify-content: center;
    transition: transform .15s, box-shadow .15s, color .15s;
  }
  .rho-hbtn:hover { color: #fff; transform: translateY(-1px); box-shadow: 0 4px 14px rgba(0,0,0,.35); }
  .rho-hbtn svg { width: 14px; height: 14px; }

  /* ---- settings popover: colors + voices ---- */
  #rho-settings {
    display: none; position: absolute; top: 52px; right: 12px; z-index: 5;
    width: 240px; padding: 14px; border-radius: 16px;
    background: rgba(16,14,30,.97); border: 1px solid rgba(255,255,255,.12);
    box-shadow: 0 18px 50px rgba(0,0,0,.55);
    animation: rho-rise .2s cubic-bezier(.21,1.02,.55,1);
  }
  #rho-settings.rho-on { display: block; }
  .rho-set-label { font-size: 10px; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase; color: rgba(255,255,255,.45); margin: 0 0 8px; }
  .rho-set-label + .rho-set-label { margin-top: 14px; }
  .rho-swatches { display: flex; gap: 8px; flex-wrap: wrap; }
  .rho-swatch {
    width: 26px; height: 26px; border-radius: 50%; cursor: pointer; border: 2px solid transparent;
    transition: transform .12s, border-color .12s;
  }
  .rho-swatch:hover { transform: scale(1.12); }
  .rho-swatch.rho-on { border-color: #fff; box-shadow: 0 0 10px color-mix(in srgb, var(--rho-a) 60%, transparent); }
  .rho-voices { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 2px; }
  .rho-voice {
    padding: 5px 10px; border-radius: 10px; cursor: pointer; font-size: 12px; font-weight: 600;
    background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.1); color: rgba(255,255,255,.8);
    transition: background .12s, border-color .12s, color .12s;
  }
  .rho-voice:hover { color: #fff; background: rgba(255,255,255,.1); }
  .rho-voice.rho-on { background: linear-gradient(135deg, var(--rho-a), var(--rho-a2)); border-color: transparent; color: #fff; }

  /* ---- thread + bubbles + crew chips ---- */
  #rho-thread { flex: 1; overflow-y: auto; padding: 18px 14px; display: flex; flex-direction: column; gap: 11px; }
  #rho-thread::-webkit-scrollbar { width: 7px; }
  #rho-thread::-webkit-scrollbar-thumb { background: rgba(255,255,255,.14); border-radius: 4px; }
  .rho-msg {
    position: relative; max-width: 86%; padding: 10px 14px; border-radius: 17px;
    font-size: 14.5px; line-height: 1.48; white-space: pre-wrap; word-wrap: break-word;
    animation: rho-msgin .3s cubic-bezier(.21,1.02,.55,1);
  }
  @keyframes rho-msgin { from { opacity: 0; transform: translateY(8px) scale(.97); } to { opacity: 1; transform: none; } }
  .rho-msg.user {
    align-self: flex-end; color: #fff; border-bottom-right-radius: 6px;
    background: linear-gradient(120deg, var(--rho-a), var(--rho-a2) 60%, color-mix(in srgb, var(--rho-a2) 60%, #fff));
    box-shadow: 0 6px 20px color-mix(in srgb, var(--rho-a) 32%, transparent), inset 0 1px 0 rgba(255,255,255,.32);
  }
  .rho-msg.user.rho-sendoff { animation: rho-msgin .3s cubic-bezier(.21,1.02,.55,1), rho-glowoff 1.1s ease-out; }
  @keyframes rho-glowoff {
    0% { box-shadow: 0 0 0 3px color-mix(in srgb, var(--rho-a) 55%, transparent), 0 6px 20px color-mix(in srgb, var(--rho-a) 32%, transparent); }
    100% { box-shadow: 0 6px 20px color-mix(in srgb, var(--rho-a) 32%, transparent), inset 0 1px 0 rgba(255,255,255,.32); }
  }
  .rho-msg.assistant {
    align-self: flex-start; color: rgba(255,255,255,.95); border-bottom-left-radius: 6px;
    background: rgba(255,255,255,.065);
    backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(255,255,255,.09);
    box-shadow: 0 4px 16px rgba(0,0,0,.25);
    overflow: hidden;
  }
  .rho-msg.assistant::before {
    content: ""; position: absolute; top: 0; left: 8%; right: 8%; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,.5), transparent);
  }
  .rho-msg.assistant.is-streaming:empty { min-width: 54px; min-height: 20px; }
  .rho-msg.assistant.is-streaming:empty::after {
    content: "\\00B7 \\00B7 \\00B7"; font-weight: 800; letter-spacing: 2px;
    background: linear-gradient(90deg, var(--rho-a), var(--rho-a2));
    -webkit-background-clip: text; background-clip: text; color: transparent;
    animation: rho-dots 1.1s ease-in-out infinite;
  }
  .rho-msg.assistant.is-streaming:not(:empty)::after { content: '\\258B'; opacity: .5; animation: rho-blink 1s steps(2) infinite; }
  @keyframes rho-dots { 0%,100% { opacity: .35; } 50% { opacity: 1; } }
  @keyframes rho-blink { 50% { opacity: 0; } }

  /* the send comet: a spark that flies from the bar up to the head orb */
  .rho-comet {
    position: absolute; width: 10px; height: 10px; border-radius: 50%; z-index: 6; pointer-events: none;
    background: radial-gradient(circle, #fff, var(--rho-a) 55%, transparent 75%);
    box-shadow: 0 0 14px color-mix(in srgb, var(--rho-a) 80%, transparent);
  }

  /* crew chips \u2014 live tool/agent activity, Claude-Code style */
  .rho-crew { align-self: flex-start; display: flex; flex-direction: column; gap: 6px; max-width: 92%; }
  .rho-chip-tool {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 12px; border-radius: 12px;
    font-size: 12px; font-weight: 600; letter-spacing: .2px;
    font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
    background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.1);
    color: rgba(255,255,255,.78);
    animation: rho-msgin .25s cubic-bezier(.21,1.02,.55,1);
  }
  .rho-chip-tool .rho-dot {
    width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
    background: linear-gradient(135deg, var(--rho-a), var(--rho-a2));
    box-shadow: 0 0 8px color-mix(in srgb, var(--rho-a) 70%, transparent);
    animation: rho-dotpulse 1s ease-in-out infinite;
  }
  @keyframes rho-dotpulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(.6); opacity: .5; } }
  .rho-chip-tool.rho-done { color: rgba(255,255,255,.45); }
  .rho-chip-tool.rho-done .rho-dot { animation: none; background: #43c98a; box-shadow: none; }
  .rho-chip-tool .rho-chip-task { color: rgba(255,255,255,.5); font-weight: 400; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }

  .rho-attach-pill {
    align-self: flex-end; display: inline-flex; align-items: center; gap: 8px;
    padding: 5px 10px; border-radius: 10px; font-size: 11.5px; font-weight: 600;
    background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.12); color: rgba(255,255,255,.75);
  }
  .rho-attach-pill img { width: 26px; height: 26px; border-radius: 6px; object-fit: cover; }
  .rho-attach-pill button { border: none; background: transparent; color: rgba(255,255,255,.6); cursor: pointer; font-size: 13px; padding: 0 2px; }

  /* ---- the Adobe chat bus ---- */
  #rho-barwrap { padding: 10px 14px calc(14px + env(safe-area-inset-bottom, 0px)); flex-shrink: 0; position: relative; }
  #rho-bar {
    position: relative; border-radius: 22px; padding: 13px 14px 9px;
    background: linear-gradient(170deg, rgba(255,255,255,.075), rgba(255,255,255,.035));
    border: 1px solid rgba(255,255,255,.14);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.12), 0 8px 26px rgba(0,0,0,.35);
    transition: border-color .2s, box-shadow .2s;
  }
  #rho-bar::before { content: ""; position: absolute; top: 0; left: 10%; right: 10%; height: 1px; background: linear-gradient(90deg, transparent, rgba(255,255,255,.72), transparent); }
  #rho-bar:focus-within { border-color: color-mix(in srgb, var(--rho-a) 55%, transparent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--rho-a) 13%, transparent), inset 0 1px 0 rgba(255,255,255,.12); }
  #rho-input { width: 100%; border: none; background: transparent; color: #fff; font-size: 15px; line-height: 1.45; resize: none; outline: none; min-height: 22px; max-height: 120px; padding: 0 2px; font-family: inherit; }
  #rho-input::placeholder { color: rgba(255,255,255,.46); font-weight: 500; }
  .rho-barrow { display: flex; align-items: center; gap: 4px; margin-top: 9px; }
  .rho-barrow .grow { flex: 1; }
  .rho-bbtn {
    width: 34px; height: 34px; border-radius: 50%; cursor: pointer; flex-shrink: 0;
    border: 1px solid rgba(255,255,255,.1);
    background: linear-gradient(160deg, rgba(255,255,255,.09), rgba(255,255,255,.03));
    color: rgba(255,255,255,.75); display: flex; align-items: center; justify-content: center;
    transition: transform .15s, box-shadow .15s, color .15s;
  }
  .rho-bbtn:hover { color: #fff; transform: translateY(-1px); box-shadow: 0 4px 14px color-mix(in srgb, var(--rho-a) 24%, transparent); }
  .rho-bbtn svg { width: 18px; height: 18px; }
  .rho-bbtn.rho-on { background: linear-gradient(135deg, var(--rho-a3), var(--rho-a)); color: #fff; border-color: transparent; box-shadow: 0 0 16px color-mix(in srgb, var(--rho-a) 45%, transparent); }
  #rho-think.rho-on { background: linear-gradient(135deg, #2b2350, var(--rho-a)); }
  #rho-call { color: rgba(255,255,255,.85); }
  #rho-send {
    width: 38px; height: 38px; border-radius: 50%; border: none; margin-left: 4px; color: #fff; cursor: pointer; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, var(--rho-a), var(--rho-a2));
    box-shadow: 0 4px 16px color-mix(in srgb, var(--rho-a) 38%, transparent), inset 0 1px 0 rgba(255,255,255,.34);
    transition: transform .12s, box-shadow .2s, opacity .2s;
  }
  #rho-send:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 6px 22px color-mix(in srgb, var(--rho-a) 52%, transparent), inset 0 1px 0 rgba(255,255,255,.34); }
  #rho-send:active { transform: scale(.92); }
  #rho-send:disabled { opacity: .35; cursor: not-allowed; }
  #rho-send svg { width: 16px; height: 16px; }
  .rho-hint { font-size: 10px; color: rgba(255,255,255,.30); text-align: center; margin: 6px 0 0; }

  /* ---- plus menu ---- */
  #rho-plusmenu {
    display: none; position: absolute; bottom: 66px; left: 18px; z-index: 6;
    min-width: 190px; padding: 6px; border-radius: 14px;
    background: rgba(16,14,30,.97); border: 1px solid rgba(255,255,255,.12);
    box-shadow: 0 18px 50px rgba(0,0,0,.55);
    animation: rho-rise .18s cubic-bezier(.21,1.02,.55,1);
  }
  #rho-plusmenu.rho-on { display: block; }
  .rho-pmitem {
    display: flex; align-items: center; gap: 10px; width: 100%;
    padding: 9px 10px; border: none; border-radius: 9px; cursor: pointer;
    background: transparent; color: rgba(255,255,255,.85); font-size: 13.5px; font-weight: 600; text-align: left;
    font-family: inherit;
  }
  .rho-pmitem:hover { background: rgba(255,255,255,.08); color: #fff; }
  .rho-pmitem svg { width: 15px; height: 15px; opacity: .75; }

  /* ---- THE BIG ONE: fullscreen voice call ---- */
  #rho-call-surface {
    display: none; position: fixed; inset: 0; z-index: 2147483002;
    flex-direction: column; align-items: center; justify-content: center; gap: 26px;
    background:
      radial-gradient(ellipse at 50% 34%, color-mix(in srgb, var(--rho-a) 16%, transparent), transparent 60%),
      radial-gradient(ellipse at 20% 80%, color-mix(in srgb, var(--rho-a3) 10%, transparent), transparent 55%),
      rgba(6,5,14,.97);
    backdrop-filter: blur(30px); -webkit-backdrop-filter: blur(30px);
    color: #fff;
  }
  #rho-embed.rho-call-open #rho-call-surface { display: flex; animation: rho-callin .34s cubic-bezier(.21,1.02,.55,1); }
  @keyframes rho-callin { from { opacity: 0; } to { opacity: 1; } }
  #rho-call-orb {
    position: relative; width: min(46vmin, 240px); height: min(46vmin, 240px);
    border-radius: 50%; border: none; cursor: pointer; background: transparent; padding: 0;
    transition: transform .3s cubic-bezier(.34,1.56,.64,1);
  }
  #rho-call-orb .rho-orbrim { animation-duration: 8s; }
  #rho-embed.rho-v-listening #rho-call-orb { transform: scale(1.03); }
  #rho-embed.rho-v-listening #rho-call-orb .rho-orbcore { animation-duration: 2.2s, 14s; }
  #rho-embed.rho-v-thinking #rho-call-orb .rho-orbrim { animation-duration: 1.6s; }
  #rho-embed.rho-v-speaking #rho-call-orb { animation: rho-speakpulse .62s ease-in-out infinite; }
  @keyframes rho-speakpulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.055); } }
  #rho-call-state {
    font-size: 12px; font-weight: 700; letter-spacing: 2.4px; text-transform: uppercase;
    color: rgba(255,255,255,.55); min-height: 15px;
  }
  #rho-call-line {
    max-width: min(640px, 86vw); min-height: 52px; text-align: center;
    font-size: clamp(16px, 2.6vmin, 21px); line-height: 1.5; color: rgba(255,255,255,.92);
  }
  #rho-call-line .rho-heard { color: rgba(255,255,255,.5); font-style: italic; }
  #rho-call-crew { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; max-width: 86vw; min-height: 30px; }
  #rho-call-exit {
    position: absolute; top: max(18px, env(safe-area-inset-top)); right: 22px;
    width: 42px; height: 42px; border-radius: 50%;
  }
  #rho-call-hint { position: absolute; bottom: max(20px, env(safe-area-inset-bottom)); font-size: 11.5px; color: rgba(255,255,255,.35); letter-spacing: .4px; }

  /* Phones: the panel is a full-screen surface, not a floating window */
  @media (max-width: 520px) {
    #rho-panel { right: 0; bottom: 0; width: 100vw; height: 100vh; height: 100dvh; border-radius: 0; }
    #rho-launch { width: 52px; height: 52px; }
  }

  html[data-theme="light"] #rho-panel, :root[data-color-scheme="light"] #rho-panel { color: #fff; }

  /* sign-in card: Rho is tied to credits, so Rho knows who you are */
  #rho-embed .rho-signin {
    margin: 10px 4px 4px; padding: 16px 16px 14px; border-radius: 16px;
    background: linear-gradient(160deg, rgba(255,255,255,.05), rgba(255,255,255,.015));
    border: 1px solid rgba(255,255,255,.09); box-shadow: 0 12px 32px rgba(0,0,0,.35);
  }
  #rho-embed .rho-signin-title { font-weight: 700; font-size: 15px; margin-bottom: 5px; color: #eef4fb; }
  #rho-embed .rho-signin-sub { font-size: 12.5px; line-height: 1.5; color: rgba(230,238,247,.62); margin-bottom: 12px; }
  #rho-embed .rho-gbtn {
    display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%;
    padding: 10px 14px; border-radius: 12px; border: 1px solid rgba(255,255,255,.14);
    background: #fff; color: #1f1f1f; font-weight: 600; font-size: 13.5px; cursor: pointer;
    transition: transform .15s ease, box-shadow .15s ease;
  }
  #rho-embed .rho-gbtn:hover {
    transform: translateY(-1px);
    box-shadow: 0 8px 22px rgba(0,0,0,.35), 0 0 0 3px color-mix(in srgb, var(--rho-a) 25%, transparent);
  }
  `;
  var styleEl = document.createElement('style');
  styleEl.id = 'rho-embed-style';
  styleEl.textContent = css;

  // ---- markup -------------------------------------------------------------
  var ICON = {
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    think: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6.5 6.5 0 0 1 6.5 6.5c0 1.9-.86 3.4-2 4.5-.83.8-1.5 1.6-1.5 2.5V18h-6v-1.5c0-.9-.67-1.7-1.5-2.5-1.14-1.1-2-2.6-2-4.5A6.5 6.5 0 0 1 12 3z"/><path d="M9.5 21h5"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
    call: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12h2M7 8v8M11 5v14M15 8v8M19 10v4"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l16-8-6 16-3-6-7-2z"/></svg>',
    image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5-9 9"/></svg>',
    fresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>'
  };
  var ORB = '<span class="rho-orbcore"></span><span class="rho-orbrim"></span><span class="rho-orbspec"></span><span class="rho-orbsheen"></span>';

  var root = document.createElement('div');
  root.id = 'rho-embed';
  root.innerHTML =
    '<button id="rho-launch" aria-label="Open ' + TITLE + '">' + ORB + '</button>' +
    '<section id="rho-panel" role="dialog" aria-label="' + TITLE + ' chat">' +
      '<header id="rho-head">' +
        '<span class="rho-head-orb">' + ORB + '</span>' +
        '<span class="rho-name">' + TITLE + '</span>' +
        (READY ? '' : '<span class="rho-chip" id="rho-chip">warming up</span>') +
        '<span class="rho-spacer"></span>' +
        '<button class="rho-hbtn" id="rho-gear" aria-label="Personalize" title="Colors & voice">' + ICON.gear + '</button>' +
        '<button class="rho-hbtn" id="rho-close" aria-label="Close">' + ICON.close + '</button>' +
        '<div id="rho-settings" role="menu" aria-label="Personalize ' + TITLE + '">' +
          '<p class="rho-set-label">Accent</p>' +
          '<div class="rho-swatches" id="rho-swatches"></div>' +
          '<p class="rho-set-label">Voice</p>' +
          '<div class="rho-voices" id="rho-voices"></div>' +
        '</div>' +
      '</header>' +
      '<div id="rho-thread" aria-live="polite"></div>' +
      '<div id="rho-barwrap">' +
        '<div id="rho-plusmenu" role="menu">' +
          '<button class="rho-pmitem" id="rho-pm-image">' + ICON.image + 'Attach an image</button>' +
          '<button class="rho-pmitem" id="rho-pm-new">' + ICON.fresh + 'Start fresh</button>' +
          '<button class="rho-pmitem" id="rho-pm-copy">' + ICON.copy + 'Copy conversation</button>' +
        '</div>' +
        '<div id="rho-bar">' +
          '<textarea id="rho-input" rows="1" placeholder="Ask anything…" aria-label="Message ' + TITLE + '"></textarea>' +
          '<div class="rho-barrow">' +
            '<button class="rho-bbtn" id="rho-plus" title="More" aria-label="More options">' + ICON.plus + '</button>' +
            '<button class="rho-bbtn" id="rho-think" title="Deep thinking" aria-label="Toggle deep thinking" aria-pressed="false">' + ICON.think + '</button>' +
            '<button class="rho-bbtn" id="rho-dictate" title="Dictate into the message" aria-label="Dictate">' + ICON.mic + '</button>' +
            '<span class="grow"></span>' +
            '<button class="rho-bbtn" id="rho-call" title="Talk to ' + TITLE + '" aria-label="Talk to ' + TITLE + '">' + ICON.call + '</button>' +
            '<button id="rho-send" aria-label="Send" disabled>' + ICON.send + '</button>' +
          '</div>' +
        '</div>' +
        '<p class="rho-hint">' + (READY ? 'Enter to send · Shift+Enter for a new line · the waveform opens a live voice call' : TITLE + ' is being wired in \u2014 answers land here soon') + '</p>' +
      '</div>' +
    '</section>' +
    '<div id="rho-call-surface" role="dialog" aria-label="Voice call with ' + TITLE + '">' +
      '<button class="rho-hbtn" id="rho-call-exit" aria-label="End the call">' + ICON.close + '</button>' +
      '<button id="rho-call-orb" aria-label="Tap to interrupt">' + ORB + '</button>' +
      '<div id="rho-call-state">connecting</div>' +
      '<div id="rho-call-line"></div>' +
      '<div id="rho-call-crew"></div>' +
      '<div id="rho-call-hint">Just talk \u2014 ' + TITLE + ' is listening. Tap the orb to cut in.</div>' +
    '</div>';

  function mount() {
    document.head.appendChild(styleEl);
    document.body.appendChild(root);
    wire();
  }

  // ---- behavior -----------------------------------------------------------
  function wire() {
    var launch  = root.querySelector('#rho-launch');
    var closeBtn= root.querySelector('#rho-close');
    var thread  = root.querySelector('#rho-thread');
    var input   = root.querySelector('#rho-input');
    var sendBtn = root.querySelector('#rho-send');
    var dictate = root.querySelector('#rho-dictate');
    var plusBtn = root.querySelector('#rho-plus');
    var plusMenu= root.querySelector('#rho-plusmenu');
    var thinkBtn= root.querySelector('#rho-think');
    var gearBtn = root.querySelector('#rho-gear');
    var settings= root.querySelector('#rho-settings');
    var callBtn = root.querySelector('#rho-call');
    var callExit= root.querySelector('#rho-call-exit');
    var callOrb = root.querySelector('#rho-call-orb');
    var callState = root.querySelector('#rho-call-state');
    var callLine  = root.querySelector('#rho-call-line');
    var callCrew  = root.querySelector('#rho-call-crew');
    var thinking = false;

    function open() {
      root.classList.add('rho-open');
      launch.classList.add('rho-hidden');
      if (!greeted) { greeted = true; append('assistant', GREETING); }
      checkAuth();
      setTimeout(function () { input.focus(); }, 260);
    }
    function close() {
      root.classList.remove('rho-open');
      launch.classList.remove('rho-hidden');
      settings.classList.remove('rho-on');
      plusMenu.classList.remove('rho-on');
    }

    launch.addEventListener('click', open);
    closeBtn.addEventListener('click', close);

    // ---- sign-in: Rho spends credits, so Rho knows who you are --------------
    var signinCard = null;
    function checkAuth() {
      if (!READY || !ENDPOINT) return;
      fetch(ENDPOINT + '/api/me', { credentials: 'include', headers: TOKEN ? { 'Authorization': 'Bearer ' + TOKEN } : {} })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          auth.checked = true;
          auth.required = !!(j && j.authRequired);
          auth.signedIn = !!(j && j.signedIn);
          auth.signin = (j && j.signin) || 'https://workbench.rhobear.ai/signin';
          if (auth.required && !auth.signedIn) showSigninCard();
          else hideSigninCard();
        }).catch(function () {});
    }
    function showSigninCard() {
      if (signinCard && signinCard.isConnected) { thread.scrollTop = thread.scrollHeight; return; }
      signinCard = document.createElement('div');
      signinCard.className = 'rho-signin';
      signinCard.innerHTML =
        '<div class="rho-signin-title">Let\u2019s make it yours</div>' +
        '<div class="rho-signin-sub">Rho runs on your RHOBEAR credits \u2014 sign in and every chat, voice call, and scout is yours.</div>' +
        '<button class="rho-gbtn" type="button">' + GOOGLE_G + '<span>Continue with Google</span></button>';
      signinCard.querySelector('.rho-gbtn').addEventListener('click', openSignin);
      thread.appendChild(signinCard);
      thread.scrollTop = thread.scrollHeight;
    }
    function hideSigninCard() {
      if (signinCard) { signinCard.remove(); signinCard = null; }
    }
    function openSignin() {
      var url = ENDPOINT + '/api/auth/start?o=' + encodeURIComponent(location.origin);
      var w = 520, hh = 680;
      var popup = window.open(url, 'rho-signin', 'popup,width=' + w + ',height=' + hh +
        ',left=' + Math.max(0, ((screen.width || w) - w) / 2) + ',top=' + Math.max(0, ((screen.height || hh) - hh) / 2));
      if (!popup) { try { location.href = auth.signin || url; } catch (e) {} }
    }
    window.addEventListener('message', function (e) {
      if (e.origin !== EP_ORIGIN) return;
      var d = e.data;
      if (d && d.type === 'rho:token' && typeof d.token === 'string') {
        TOKEN = d.token; lsSet('rho.token', TOKEN);
        auth.signedIn = true;
        hideSigninCard();
        append('assistant', 'You\u2019re in. What are we doing first?');
      }
    });

    // ---- personalize: accent + voice --------------------------------------
    function applyAccent(hex) {
      ACCENT = hex;
      root.style.setProperty('--rho-a', hex);
      root.style.setProperty('--rho-a2', hueShift(hex, 42, 0.08, 0.04));
      root.style.setProperty('--rho-a3', hueShift(hex, -28, 0.05, 0.10));
      lsSet('rho.accent', hex);
      renderSwatches();
    }
    function renderSwatches() {
      var box = root.querySelector('#rho-swatches');
      box.innerHTML = '';
      var seen = {};
      SWATCHES.forEach(function (hex) {
        if (seen[hex.toLowerCase()]) return; seen[hex.toLowerCase()] = 1;
        var b = document.createElement('button');
        b.className = 'rho-swatch' + (hex.toLowerCase() === ACCENT.toLowerCase() ? ' rho-on' : '');
        b.style.background = 'linear-gradient(135deg, ' + hex + ', ' + hueShift(hex, 42, 0.08, 0.04) + ')';
        b.setAttribute('aria-label', 'Accent ' + hex);
        b.addEventListener('click', function () { applyAccent(hex); });
        box.appendChild(b);
      });
    }
    function renderVoices() {
      var box = root.querySelector('#rho-voices');
      box.innerHTML = '';
      VOICES.forEach(function (v) {
        var b = document.createElement('button');
        b.className = 'rho-voice' + (v === VOICE ? ' rho-on' : '');
        b.textContent = v;
        b.addEventListener('click', function () {
          VOICE = v; lsSet('rho.voice', v); renderVoices();
          speakSample(v);
        });
        box.appendChild(b);
      });
    }
    function speakSample(v) {
      if (!READY || !ENDPOINT) return;
      fetch(ENDPOINT + '/api/tts', {
        method: 'POST', headers: authHeaders(), credentials: 'include',
        body: JSON.stringify({ text: 'Hey, this is ' + TITLE + ' \u2014 sounding like ' + v + '.', voice: v })
      }).then(function (r) { return r.ok ? r.blob() : null; }).then(function (b) {
        if (!b) return;
        var a = new Audio(URL.createObjectURL(b));
        a.play().catch(function () {});
      }).catch(function () {});
    }
    renderSwatches();
    renderVoices();
    gearBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      plusMenu.classList.remove('rho-on');
      settings.classList.toggle('rho-on');
    });

    // ---- plus menu ---------------------------------------------------------
    plusBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      settings.classList.remove('rho-on');
      plusMenu.classList.toggle('rho-on');
    });
    document.addEventListener('click', function (e) {
      if (!settings.contains(e.target) && e.target !== gearBtn) settings.classList.remove('rho-on');
      if (!plusMenu.contains(e.target) && e.target !== plusBtn) plusMenu.classList.remove('rho-on');
    });
    root.querySelector('#rho-pm-new').addEventListener('click', function () {
      plusMenu.classList.remove('rho-on');
      sessionId = null;
      try { localStorage.removeItem('rho.session'); } catch (e) {}
      thread.innerHTML = '';
      pendingImage = null;
      append('assistant', 'Fresh start \u2014 what are we doing?');
    });
    root.querySelector('#rho-pm-copy').addEventListener('click', function () {
      plusMenu.classList.remove('rho-on');
      var lines = [];
      thread.querySelectorAll('.rho-msg').forEach(function (m) {
        lines.push((m.classList.contains('user') ? 'You: ' : TITLE + ': ') + m.textContent);
      });
      try { navigator.clipboard.writeText(lines.join('\n')); } catch (e) {}
    });
    root.querySelector('#rho-pm-image').addEventListener('click', function () {
      plusMenu.classList.remove('rho-on');
      var fi = document.createElement('input');
      fi.type = 'file'; fi.accept = 'image/png,image/jpeg,image/webp';
      fi.addEventListener('change', function () {
        var f = fi.files && fi.files[0];
        if (!f) return;
        var rd = new FileReader();
        rd.onload = function () {
          if (typeof rd.result === 'string' && rd.result.length < 3200000) {
            pendingImage = rd.result;
            showAttachPill(rd.result, f.name);
          }
        };
        rd.readAsDataURL(f);
      });
      fi.click();
    });
    function showAttachPill(dataUrl, name) {
      var old = thread.querySelector('.rho-attach-pill');
      if (old) old.remove();
      var pill = document.createElement('div');
      pill.className = 'rho-attach-pill';
      pill.innerHTML = '<img alt="attachment" src="' + dataUrl + '"><span>' + (name || 'image') + ' rides the next message</span>';
      var x = document.createElement('button');
      x.textContent = '×'; x.setAttribute('aria-label', 'Remove attachment');
      x.addEventListener('click', function () { pendingImage = null; pill.remove(); });
      pill.appendChild(x);
      thread.appendChild(pill);
      thread.scrollTop = thread.scrollHeight;
    }

    // ---- deep thinking ------------------------------------------------------
    thinkBtn.addEventListener('click', function () {
      thinking = !thinking;
      thinkBtn.classList.toggle('rho-on', thinking);
      thinkBtn.setAttribute('aria-pressed', String(thinking));
    });

    // ---- composer -----------------------------------------------------------
    function autogrow() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 120) + 'px'; }
    function refreshSend() { sendBtn.disabled = input.value.trim().length === 0; }
    input.addEventListener('input', function () { autogrow(); refreshSend(); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!sendBtn.disabled) send(); }
    });
    sendBtn.addEventListener('click', function () { if (!sendBtn.disabled) send(); });

    function append(role, text, opts) {
      opts = opts || {};
      var d = document.createElement('div');
      d.className = 'rho-msg ' + role + (opts.streaming ? ' is-streaming' : '');
      d.textContent = text;
      thread.appendChild(d);
      thread.scrollTop = thread.scrollHeight;
      return d;
    }
    function setText(node, text) { node.textContent = text; thread.scrollTop = thread.scrollHeight; }
    function setBusy(on) { root.classList.toggle('rho-busy', !!on); }

    // the send comet: a spark that arcs from the bar to the head orb
    function comet() {
      try {
        var panel = root.querySelector('#rho-panel');
        var from = root.querySelector('#rho-send').getBoundingClientRect();
        var to = root.querySelector('.rho-head-orb').getBoundingClientRect();
        var pr = panel.getBoundingClientRect();
        var c = document.createElement('span');
        c.className = 'rho-comet';
        c.style.left = (from.left - pr.left + from.width / 2 - 5) + 'px';
        c.style.top = (from.top - pr.top + from.height / 2 - 5) + 'px';
        panel.appendChild(c);
        var dx = (to.left - from.left), dy = (to.top - from.top);
        c.animate([
          { transform: 'translate(0,0) scale(1)', opacity: 1 },
          { transform: 'translate(' + dx * 0.5 + 'px,' + (dy * 0.55 - 40) + 'px) scale(.85)', opacity: 1, offset: 0.55 },
          { transform: 'translate(' + dx + 'px,' + dy + 'px) scale(.3)', opacity: 0 }
        ], { duration: 620, easing: 'cubic-bezier(.3,.6,.3,1)' }).onfinish = function () { c.remove(); };
      } catch (e) {}
    }

    // crew chips (chat thread)
    var crewBox = null, crewChips = {};
    function crewChip(key, label, task) {
      if (!crewBox || !crewBox.isConnected) {
        crewBox = document.createElement('div');
        crewBox.className = 'rho-crew';
        thread.appendChild(crewBox);
      }
      var chip = crewChips[key];
      if (!chip) {
        chip = document.createElement('span');
        chip.className = 'rho-chip-tool';
        chip.innerHTML = '<span class="rho-dot"></span><span class="rho-chip-name"></span><span class="rho-chip-task"></span>';
        crewBox.appendChild(chip);
        crewChips[key] = chip;
      }
      chip.querySelector('.rho-chip-name').textContent = label;
      if (task) chip.querySelector('.rho-chip-task').textContent = '\u2014 ' + task;
      thread.scrollTop = thread.scrollHeight;
      return chip;
    }
    function crewDone(key) {
      var chip = key == null ? null : crewChips[key];
      if (chip) chip.classList.add('rho-done');
    }
    function crewAllDone() {
      Object.keys(crewChips).forEach(function (k) { crewChips[k].classList.add('rho-done'); });
      crewChips = {};
      crewBox = null;
    }

    async function send() {
      var text = input.value.trim();
      if (!text) return;
      if (READY && ENDPOINT && auth.checked && auth.required && !auth.signedIn) {
        // keep their words in the box \u2014 sign in, then hit send again
        showSigninCard();
        return;
      }
      input.value = ''; autogrow(); refreshSend();
      var bubble = append('user', text);
      bubble.classList.add('rho-sendoff');
      comet();
      var attach = thread.querySelector('.rho-attach-pill');
      if (attach) attach.remove();

      // FROZEN-READY: bot backend not live yet -> graceful local reply, no dead network call.
      if (!READY || !ENDPOINT) {
        var warm = append('assistant', '', { streaming: true });
        typeOut(warm, WARMING);
        return;
      }

      var node = append('assistant', '', { streaming: true });
      sendBtn.disabled = true;
      setBusy(true);
      try {
        await askBrain(text, {
          image: pendingImage || undefined,
          thinking: thinking,
          mode: 'text',
          onDelta: function (_d, full) { setText(node, full); },
          onTool: function (name) { crewChip('t:' + name, name); },
          onToolDone: function () {},
          onAgent: function (id, kind, task) { crewChip('a:' + id, kind, task); },
          onTask: function (id, status, summary) {
            var chip = crewChip('a:' + id, 'task', summary || status);
            if (status === 'completed' || status === 'done') chip.classList.add('rho-done');
          }
        });
        node.classList.remove('is-streaming');
        crewAllDone();
        if (!node.textContent) setText(node, "…I didn't catch a reply that time. Try me again?");
      } catch (err) {
        node.classList.remove('is-streaming');
        crewAllDone();
        if (err && err.rho === 'signin') {
          node.remove();
          showSigninCard();
        } else if (err && err.rho === 'credits') {
          setText(node, err.message + ' Top up at workbench.rhobear.ai.');
        } else {
          setText(node, "I hit a snag reaching the crew. Give it another go in a sec.");
        }
      } finally {
        pendingImage = null;
        setBusy(false);
        refreshSend();
      }
    }

    // typewriter for the warming message (no network)
    function typeOut(node, text) {
      var i = 0;
      setBusy(true);
      (function step() {
        node.textContent = text.slice(0, i);
        thread.scrollTop = thread.scrollHeight;
        if (i++ < text.length) setTimeout(step, 14);
        else { node.classList.remove('is-streaming'); setBusy(false); }
      })();
    }

    // ---- the brain call: full SSE surface ----------------------------------
    async function askBrain(text, h) {
      var chatId = 'c' + Math.random().toString(36).slice(2);
      activeChatId = chatId;
      var controller = new AbortController();
      activeAbort = controller;
      var payload = {
        text: (h.thinking ? '(Take your time and think this through carefully before answering.) ' : '') + text,
        sessionId: sessionId, chatId: chatId, mode: h.mode || 'text'
      };
      if (h.image) payload.image = h.image;
      var resp = await fetch(ENDPOINT + '/api/chat', {
        method: 'POST',
        headers: authHeaders(),
        credentials: 'include',
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      if (resp.status === 401) {
        auth.signedIn = false;
        var e401 = new Error('signin required'); e401.rho = 'signin'; throw e401;
      }
      if (resp.status === 402) {
        var j402 = null; try { j402 = await resp.json(); } catch (e) {}
        var e402 = new Error((j402 && j402.message) || 'Out of credits.'); e402.rho = 'credits'; throw e402;
      }
      if (!resp.ok || !resp.body) throw new Error('brain ' + resp.status);
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buf = '', full = '', brainError = null;
      for (;;) {
        var r = await reader.read();
        if (r.done) break;
        buf += decoder.decode(r.value, { stream: true });
        var idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          var raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
          var ev = 'message', data = null;
          raw.split('\n').forEach(function (line) {
            if (line.indexOf('event:') === 0) ev = line.slice(6).trim();
            else if (line.indexOf('data:') === 0) { try { data = JSON.parse(line.slice(5)); } catch (e) {} }
          });
          if (ev === 'session' && data && data.sessionId) {
            sessionId = data.sessionId;
            try { localStorage.setItem('rho.session', sessionId); } catch (e) {}
          } else if (ev === 'delta' && data) {
            full += (data.text || '');
            if (h.onDelta) h.onDelta(data.text || '', full);
          } else if (ev === 'tool' && data) {
            if (h.onTool) h.onTool(data.name || 'tool', data.agent);
          } else if (ev === 'tool_done' && data) {
            if (h.onToolDone) h.onToolDone(data.id);
          } else if (ev === 'agent' && data) {
            if (h.onAgent) h.onAgent(data.id, data.kind || 'scout', data.task || '');
          } else if (ev === 'task' && data) {
            if (h.onTask) h.onTask(data.id, data.status, data.summary);
          } else if (ev === 'error' && data) {
            brainError = new Error(data.message || 'brain error');
          }
        }
      }
      activeAbort = null; activeChatId = null;
      if (brainError && !full) throw brainError;
      return full;
    }

    function interrupt() {
      if (activeAbort) { try { activeAbort.abort(); } catch (e) {} }
      if (activeChatId && ENDPOINT) {
        fetch(ENDPOINT + '/api/interrupt', {
          method: 'POST', headers: authHeaders(), credentials: 'include',
          body: JSON.stringify({ chatId: activeChatId })
        }).catch(function () {});
      }
      activeAbort = null; activeChatId = null;
    }

    // ---- dictation: speech INTO the composer \u2014 stays in normal chat ---------
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { dictate.style.display = 'none'; }
    else {
      var rec = null, dictating = false;
      dictate.addEventListener('click', function () {
        if (dictating) { try { rec.stop(); } catch (e) {} return; }
        rec = new SR(); rec.continuous = true; rec.interimResults = true; rec.lang = 'en-US';
        var base = input.value;
        rec.onresult = function (e) {
          var t = '';
          for (var i = e.resultIndex; i < e.results.length; i++) t += e.results[i][0].transcript;
          input.value = (base ? base + ' ' : '') + t; autogrow(); refreshSend();
        };
        rec.onend = function () { dictating = false; dictate.classList.remove('rho-on'); };
        rec.onerror = function () { dictating = false; dictate.classList.remove('rho-on'); };
        try { rec.start(); dictating = true; dictate.classList.add('rho-on'); } catch (e) {}
      });
    }

    /* ---- THE BIG ONE: live voice call --------------------------------------
       Continuous listening -> brain -> spoken reply, sentence by sentence.
       States: listening / thinking / speaking. Tap the orb to interrupt. */
    var call = {
      on: false, state: 'idle', rec: null,
      queue: [], playing: false, audio: null,
      pendingSentence: '', spokenFull: ''
    };

    function callSetState(s) {
      call.state = s;
      root.classList.remove('rho-v-listening', 'rho-v-thinking', 'rho-v-speaking');
      if (s !== 'idle') root.classList.add('rho-v-' + s);
      callState.textContent = s === 'listening' ? 'listening' : s === 'thinking' ? 'thinking' : s === 'speaking' ? 'speaking' : '';
    }

    function callCrewChip(label, task) {
      var chip = document.createElement('span');
      chip.className = 'rho-chip-tool';
      chip.innerHTML = '<span class="rho-dot"></span><span></span>';
      chip.children[1].textContent = label + (task ? ' \u2014 ' + task : '');
      callCrew.appendChild(chip);
      while (callCrew.children.length > 4) callCrew.removeChild(callCrew.firstChild);
      return chip;
    }

    function ttsEnqueue(sentence) {
      var s = sentence.trim();
      if (!s) return;
      call.queue.push(s);
      ttsPump();
    }
    function ttsPump() {
      if (call.playing || !call.queue.length || !call.on) return;
      call.playing = true;
      var s = call.queue.shift();
      fetch(ENDPOINT + '/api/tts', {
        method: 'POST', headers: authHeaders(), credentials: 'include',
        body: JSON.stringify({ text: s, voice: VOICE })
      }).then(function (r) { return r.ok ? r.blob() : null; }).then(function (b) {
        if (!call.on) { call.playing = false; return; }
        if (!b) { call.playing = false; ttsPump(); return; }
        callSetState('speaking');
        var a = new Audio(URL.createObjectURL(b));
        call.audio = a;
        a.onended = a.onerror = function () {
          call.playing = false; call.audio = null;
          if (call.queue.length) ttsPump();
          else if (call.on && !activeAbort) { callSetState('listening'); callListen(); }
        };
        a.play().catch(function () { a.onended(); });
      }).catch(function () { call.playing = false; ttsPump(); });
    }
    function ttsStop() {
      call.queue = [];
      if (call.audio) { try { call.audio.pause(); } catch (e) {} call.audio = null; }
      call.playing = false;
    }

    function callListen() {
      if (!call.on || !SR) return;
      try { if (call.rec) call.rec.abort(); } catch (e) {}
      var rec = new SR();
      call.rec = rec;
      rec.continuous = false; rec.interimResults = true; rec.lang = 'en-US';
      rec.onresult = function (e) {
        var t = '', isFinal = false;
        for (var i = e.resultIndex; i < e.results.length; i++) {
          t += e.results[i][0].transcript;
          if (e.results[i].isFinal) isFinal = true;
        }
        callLine.innerHTML = '<span class="rho-heard"></span>';
        callLine.firstChild.textContent = t;
        if (isFinal && t.trim()) callTurn(t.trim());
      };
      rec.onend = function () {
        // keep the ear open while idle-listening (Chrome stops after silence)
        if (call.on && call.state === 'listening') setTimeout(function () { if (call.on && call.state === 'listening') callListen(); }, 250);
      };
      rec.onerror = function () {};
      try { rec.start(); } catch (e) {}
    }

    async function callTurn(text) {
      try { if (call.rec) call.rec.abort(); } catch (e) {}
      callSetState('thinking');
      callCrew.innerHTML = '';
      call.spokenFull = ''; call.pendingSentence = '';
      var replyShown = '';
      try {
        await askBrain(text, {
          mode: 'voice',
          onDelta: function (d, full) {
            replyShown = full;
            callLine.textContent = full;
            // sentence pipeline: speak as soon as a sentence completes
            call.pendingSentence += d;
            var m;
            while ((m = call.pendingSentence.match(/^([\s\S]*?[.!?])(\s|$)/))) {
              ttsEnqueue(m[1]);
              call.pendingSentence = call.pendingSentence.slice(m[0].length);
            }
          },
          onTool: function (name) { callCrewChip(name); },
          onAgent: function (_id, kind, task) { callCrewChip(kind, task); },
          onTask: function () {}
        });
        if (call.pendingSentence.trim()) { ttsEnqueue(call.pendingSentence); call.pendingSentence = ''; }
        if (!replyShown) callLine.textContent = '…';
        // if nothing is queued/speaking (empty reply), go straight back to listening
        if (!call.queue.length && !call.playing && call.on) { callSetState('listening'); callListen(); }
      } catch (err) {
        if (err && err.rho === 'signin') {
          // voice can't sign you in \u2014 drop to the chat panel, card's waiting
          callClose(); open();
          return;
        }
        if (err && err.rho === 'credits') {
          if (call.on) { callLine.textContent = err.message + ' Top up at workbench.rhobear.ai.'; callSetState('listening'); callListen(); }
          return;
        }
        if (call.on) {
          callLine.textContent = 'I hit a snag \u2014 say that again?';
          callSetState('listening'); callListen();
        }
      }
    }

    function callOpen() {
      if (!READY || !ENDPOINT) { open(); return; }
      if (auth.checked && auth.required && !auth.signedIn) { open(); return; }
      call.on = true;
      root.classList.add('rho-call-open');
      callCrew.innerHTML = '';
      callLine.textContent = '';
      // audio unlock on the user gesture so replies are allowed to play
      try {
        var unlock = new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=');
        unlock.play().catch(function () {});
      } catch (e) {}
      if (SR) { callSetState('listening'); callListen(); }
      else { callSetState('idle'); callLine.textContent = 'This browser has no speech input \u2014 type to me in the chat instead.'; }
    }
    function callClose() {
      call.on = false;
      ttsStop();
      interrupt();
      try { if (call.rec) call.rec.abort(); } catch (e) {}
      call.rec = null;
      callSetState('idle');
      root.classList.remove('rho-call-open');
    }
    callBtn.addEventListener('click', callOpen);
    callExit.addEventListener('click', callClose);
    callOrb.addEventListener('click', function () {
      // tap = cut in: stop the voice, stop the brain, hand the floor back
      ttsStop();
      interrupt();
      if (call.on) { callSetState('listening'); callListen(); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && root.classList.contains('rho-call-open')) callClose();
    });

    // Host-page bridge: surfaces (Workbench composer, Plans chrome, the Hub)
    // can open the chat or jump straight into the live call.
    window.RhoEmbed = {
      version: '2.1',
      open: open,
      close: close,
      call: callOpen,
      endCall: callClose,
      setAccent: applyAccent
    };
  }

  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);
})();
