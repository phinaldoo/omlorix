/* ==========================================================================
   Native Slide Presentation Editor
   ========================================================================== */

(function () {
'use strict';

const host = document.getElementById('slide-presentation-EditorHost');
if (!host) return;

const root = host.shadowRoot || host.attachShadow({ mode: 'open' });
root.innerHTML = `
<style>
  :host, :host([data-theme="dark"]) {
    --bg: var(--background, #0c0d10);
    --panel: var(--surface-elevated, #131519);
    --panel-2: var(--surface-muted, #17191f);
    --line: var(--border-color, #23262d);
    --line-soft: var(--surface-control-border, #1c1f25);
    --line-strong: var(--surface-elevated-border, #3a3f4b);
    --text: var(--text-color, #e8eaf0);
    --text-dim: var(--text-color-secondary, #9aa1ad);
    --text-faint: var(--text-color-tertiary, #5f6672);
    --accent: var(--primary-color, #7c9aff);
    --accent-soft: color-mix(in srgb, var(--accent) 14%, transparent);
    --accent-ink: var(--primary-contrast-text, #0b0d14);
    --danger: var(--error-color, #ff6b64);
    --bar-bg: var(--surface-elevated, #1a1d24);
    --bar-hover: var(--hover-bg, #242833);
    --thumb-bg: var(--background, #0a0b0d);
    --chip-bg: rgba(10, 11, 14, .72);
    --chip-text: #fff;
    --scrollbar: var(--scrollbar-thumb, #262932);
    --backdrop: rgba(5, 6, 8, .6);
    --canvas-glow: rgba(124, 154, 255, .05);
    --stage-shadow: 0 24px 80px rgba(0, 0, 0, .55), 0 2px 8px rgba(0, 0, 0, .4);
    --menu-shadow: 0 10px 34px rgba(0, 0, 0, .5);
    --modal-shadow: 0 30px 90px rgba(0, 0, 0, .6);
    --radius: 10px;
    --font: var(--app-font-family, Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif);
    --mono: "SF Mono", SFMono-Regular, ui-monospace, Menlo, Consolas, monospace;
  }
  :host([data-theme="light"]) {
    --bg: var(--background, #eef0f4);
    --panel: var(--surface-elevated, #ffffff);
    --panel-2: var(--surface-muted, #f2f4f7);
    --line: var(--border-color, #dde1e8);
    --line-soft: var(--surface-control-border, #e8ebf1);
    --line-strong: var(--surface-elevated-border, #c3c9d4);
    --text: var(--text-color, #161b24);
    --text-dim: var(--text-color-secondary, #525c6b);
    --text-faint: var(--text-color-tertiary, #8b95a4);
    --accent: var(--primary-color, #4a6dff);
    --accent-soft: color-mix(in srgb, var(--accent) 12%, transparent);
    --accent-ink: var(--primary-contrast-text, #ffffff);
    --danger: var(--error-color, #e5484d);
    --bar-bg: var(--surface-elevated, rgba(255, 255, 255, .96));
    --bar-hover: var(--hover-bg, #eef0f5);
    --thumb-bg: var(--background, #e6e9ee);
    --chip-bg: rgba(255, 255, 255, .85);
    --chip-text: #39414e;
    --scrollbar: var(--scrollbar-thumb, #c6ccd6);
    --backdrop: rgba(30, 41, 59, .32);
    --canvas-glow: rgba(74, 109, 255, .06);
    --stage-shadow: 0 24px 70px rgba(15, 23, 42, .16), 0 2px 8px rgba(15, 23, 42, .08);
    --menu-shadow: 0 10px 34px rgba(15, 23, 42, .18);
    --modal-shadow: 0 30px 80px rgba(15, 23, 42, .22);
  }
  * { box-sizing: border-box; }
  :host { display: block; width: 100%; height: 100%; contain: strict; }
  :host {
    margin: 0; background: var(--bg); color: var(--text);
    font: 13px/1.45 var(--font);
    -webkit-font-smoothing: antialiased;
    overflow: hidden; user-select: none;
  }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; }
  input, select, textarea { font: inherit; color: var(--text); background: var(--panel-2); border: 1px solid var(--line); border-radius: 7px; outline: none; }
  input:focus, select:focus, textarea:focus { border-color: var(--accent); }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 6px; border: 2px solid var(--bg); }
  ::-webkit-scrollbar-track { background: transparent; }

  /* ---------- App shell ---------- */
  #app { display: grid; grid-template-rows: 52px 1fr; height: 100vh; }
  #main { display: grid; grid-template-columns: 224px 1fr 288px; min-height: 0; }

  /* ---------- Top bar ---------- */
  #topbar {
    display: flex; align-items: center; gap: 6px;
    padding: 0 14px; background: var(--panel); border-bottom: 1px solid var(--line);
  }
  .logo-mark { width: 22px; height: 22px; border-radius: 6px; background: linear-gradient(135deg, var(--accent), #b78cff); position: relative; }
  .logo-mark::after { content: ""; position: absolute; inset: 6px; border-radius: 3px; background: var(--panel); }
  #deckTitle {
    background: transparent; border: 1px solid transparent; padding: 5px 8px; border-radius: 7px;
    width: 220px; color: var(--text); font-weight: 500;
  }
  @media (hover: hover) and (pointer: fine) { #deckTitle:hover { border-color: var(--line); } }
  #deckTitle:focus { border-color: var(--accent); background: var(--panel-2); }
  .tb-group { display: flex; align-items: center; gap: 2px; padding: 0 8px; border-left: 1px solid var(--line-soft); }
  .tb-btn {
    display: inline-flex; align-items: center; gap: 7px;
    height: 32px; padding: 0 10px; border-radius: 8px; color: var(--text-dim);
    transition: background .12s, color .12s; white-space: nowrap;
  }
  @media (hover: hover) and (pointer: fine) { .tb-btn:hover { background: var(--panel-2); color: var(--text); } }
  .tb-btn:disabled { opacity: .35; pointer-events: none; }
  .tb-btn svg { width: 16px; height: 16px; flex: none; }
  .tb-btn.primary { background: var(--accent); color: var(--accent-ink); font-weight: 600; }
  @media (hover: hover) and (pointer: fine) {
    :host([data-theme="dark"]) .tb-btn.primary:hover { background: #93acff; }
    :host([data-theme="light"]) .tb-btn.primary:hover { background: #6480ff; }
  }
  .spacer { flex: 1; }
  #btnCloseEditor { display: none; width: 32px; padding: 0; justify-content: center; }
  :host([data-embedded="true"]) #btnCloseEditor { display: inline-flex; }
  :host([data-embedded="true"]) #btnOpen { display: none; }
  #saveState { min-width: 82px; text-align: right; color: var(--text-dim); }
  #saveState.error { color: var(--danger); }
  #saveState.saved { color: #4fba77; }
  .zoom-label { min-width: 46px; text-align: center; color: var(--text-dim); font-variant-numeric: tabular-nums; cursor: pointer; }
  kbd { font: 10px/1 var(--mono); color: var(--text-faint); background: var(--panel-2); border: 1px solid var(--line); border-radius: 4px; padding: 2px 4px; }

  /* ---------- Left: slides ---------- */
  #slidesPanel { background: var(--panel); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
  .panel-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 12px 8px; font-size: 11px; font-weight: 600; letter-spacing: .08em;
    text-transform: uppercase; color: var(--text-faint);
  }
  #thumbList { flex: 1; overflow-y: auto; padding: 2px 12px 12px; display: flex; flex-direction: column; gap: 10px; }
  .thumb {
    position: relative; border-radius: 8px; cursor: pointer; flex: none;
    border: 1.5px solid var(--line); background: var(--thumb-bg); overflow: hidden;
    transition: border-color .12s;
  }
  @media (hover: hover) and (pointer: fine) { .thumb:hover { border-color: var(--line-strong); } }
  .thumb:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .thumb.active { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .thumb-frame { width: 100%; aspect-ratio: 16 / 9; pointer-events: none; display: block; border: 0; background: #fff; }
  .thumb-num {
    position: absolute; left: 6px; top: 6px; z-index: 2;
    font: 600 10px/1 var(--mono); color: var(--chip-text); background: var(--chip-bg);
    padding: 4px 6px; border-radius: 5px; backdrop-filter: blur(4px);
  }
  .thumb-actions {
    position: absolute; right: 6px; top: 6px; z-index: 2; display: none; gap: 4px;
  }
  .thumb:focus-within .thumb-actions, .thumb.active .thumb-actions { display: flex; }
  @media (hover: hover) and (pointer: fine) { .thumb:hover .thumb-actions { display: flex; } }
  .thumb-actions button {
    width: 22px; height: 22px; border-radius: 5px; background: var(--chip-bg);
    color: var(--text-dim); display: grid; place-items: center; backdrop-filter: blur(4px);
  }
  @media (hover: hover) and (pointer: fine) {
    .thumb-actions button:hover { color: var(--text); }
    :host([data-theme="dark"]) .thumb-actions button:hover { background: rgba(30,33,40,.9); }
    :host([data-theme="light"]) .thumb-actions button:hover { background: #fff; }
  }
  .thumb-actions svg { width: 12px; height: 12px; }
  .thumb.drag-over-before::before, .thumb.drag-over-after::after {
    content: ""; position: absolute; left: 4px; right: 4px; height: 3px; border-radius: 2px;
    background: var(--accent); z-index: 3;
  }
  .thumb.drag-over-before::before { top: -7px; }
  .thumb.drag-over-after::after { bottom: -7px; }
  #addSlideBtn {
    margin: 0 12px 12px; height: 34px; border-radius: 8px; border: 1px dashed var(--line);
    color: var(--text-dim); display: flex; align-items: center; justify-content: center; gap: 7px;
  }
  @media (hover: hover) and (pointer: fine) { #addSlideBtn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); } }

  /* ---------- Center: canvas ---------- */
  #canvasArea {
    position: relative; overflow: hidden; min-width: 0; min-height: 0;
    background:
      radial-gradient(circle at 50% 0%, var(--canvas-glow), transparent 60%),
      var(--bg);
  }
  #canvasScroller { position: absolute; inset: 0; overflow: auto; display: grid; place-items: center; padding: 48px; }
  #stage { position: relative; flex: none; box-shadow: var(--stage-shadow); border-radius: 2px; }
  #deckFrame { display: block; border: 0; width: 1920px; height: 1080px; transform-origin: 0 0; background: #fff; }

  /* selection overlay (parent-side, crisp at any zoom) */
  #overlay { position: absolute; inset: 0; pointer-events: none; z-index: 5; }
  #selBox { position: absolute; border: 1.5px solid var(--accent); display: none; }
  #selBox.editing { border-style: dashed; }
  #hoverBox { position: absolute; border: 1px solid rgba(124,154,255,.55); display: none; }
  .handle {
    position: absolute; width: 9px; height: 9px; background: #fff;
    border: 1.5px solid var(--accent); border-radius: 2.5px; pointer-events: auto;
  }
  .handle[data-h="nw"] { left: -5px; top: -5px; cursor: nwse-resize; }
  .handle[data-h="n"]  { left: calc(50% - 5px); top: -5px; cursor: ns-resize; }
  .handle[data-h="ne"] { right: -5px; top: -5px; cursor: nesw-resize; }
  .handle[data-h="e"]  { right: -5px; top: calc(50% - 5px); cursor: ew-resize; }
  .handle[data-h="se"] { right: -5px; bottom: -5px; cursor: nwse-resize; }
  .handle[data-h="s"]  { left: calc(50% - 5px); bottom: -5px; cursor: ns-resize; }
  .handle[data-h="sw"] { left: -5px; bottom: -5px; cursor: nesw-resize; }
  .handle[data-h="w"]  { left: -5px; top: calc(50% - 5px); cursor: ew-resize; }
  #selTag {
    position: absolute; top: -24px; left: -1.5px; font: 600 10px/1 var(--mono);
    background: var(--accent); color: var(--accent-ink); padding: 4px 7px; border-radius: 5px 5px 5px 0;
    white-space: nowrap;
  }
  .guide { position: absolute; background: #ff4fa3; display: none; }
  #guideV { width: 1px; top: 0; bottom: 0; }
  #guideH { height: 1px; left: 0; right: 0; }

  /* floating text toolbar */
  #textBar {
    position: absolute; z-index: 8; display: none; align-items: center; gap: 2px;
    background: var(--bar-bg); border: 1px solid var(--line); border-radius: 10px; padding: 4px;
    box-shadow: var(--menu-shadow); max-width: calc(100% - 16px); flex-wrap: nowrap;
    backdrop-filter: blur(10px); user-select: none;
  }
  #textBar svg { width: 14px; height: 14px; flex: none; }
  #textBar .sep { width: 1px; height: 16px; background: var(--line); margin: 0 3px; flex: none; }
  .tbtn {
    min-width: 28px; height: 28px; padding: 0 5px; border-radius: 6px; flex: none;
    display: inline-flex; align-items: center; justify-content: center; gap: 4px;
    color: var(--text-dim);
  }
  @media (hover: hover) and (pointer: fine) { .tbtn:hover { background: var(--bar-hover); color: var(--text); } }
  .tbtn.on { background: var(--accent-soft); color: var(--accent); }
  .tbtn .glyph { font-size: 13.5px; font-weight: 650; }
  .tbtn.glyph-btn { font-size: 12px; font-weight: 650; letter-spacing: -.01em; }
  .tbtn.swatch { position: relative; }
  .swatch .chip {
    position: absolute; left: 5px; right: 5px; bottom: 4px; height: 3px; border-radius: 2px;
    background: #e5484d; box-shadow: 0 0 0 .5px rgba(0,0,0,.18);
  }
  .hidden-color { position: absolute; width: 26px; height: 26px; opacity: 0; pointer-events: none; border: none; padding: 0; }
  .tbf { position: relative; display: flex; align-items: center; flex: none; }
  #tbSize {
    width: 34px; height: 28px; border: none; background: transparent; padding: 0;
    text-align: right; color: var(--text); font-size: 12.5px; font-variant-numeric: tabular-nums;
    border-radius: 6px;
  }
  @media (hover: hover) and (pointer: fine) { #tbSize:hover { background: var(--bar-hover); } }
  #tbSize:focus { border: none; background: var(--bar-hover); }
  .chev {
    width: 16px; height: 28px; border-radius: 5px; display: grid; place-items: center;
    color: var(--text-faint);
  }
  .chev.open { background: var(--bar-hover); color: var(--text); }
  @media (hover: hover) and (pointer: fine) { .chev:hover { background: var(--bar-hover); color: var(--text); } }
  .chev svg { width: 11px; height: 11px; }
  #tbFamilyLbl {
    max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px;
  }
  .tmenu {
    position: absolute; top: calc(100% + 7px); left: 0; z-index: 9;
    min-width: 160px; max-height: 300px; overflow-y: auto; display: none; flex-direction: column;
    background: var(--bar-bg); border: 1px solid var(--line); border-radius: 10px;
    box-shadow: var(--menu-shadow); padding: 5px;
  }
  .tmenu.open { display: flex; }
  .tmi {
    display: flex; align-items: center; gap: 9px; padding: 6px 10px; border-radius: 7px;
    color: var(--text-dim); white-space: nowrap; font-size: 12.5px; text-align: left; flex: none;
  }
  @media (hover: hover) and (pointer: fine) { .tmi:hover { background: var(--bar-hover); color: var(--text); } }
  .tmi.on { background: var(--accent-soft); color: var(--accent); }
  .tmi svg { width: 14px; height: 14px; flex: none; }
  .tmi .mi-label { flex: 1; }
  .tmh {
    font-size: 10px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase;
    color: var(--text-faint); padding: 8px 10px 3px; flex: none;
  }

  /* ---------- Right: inspector ---------- */
  #inspector { background: var(--panel); border-left: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
  .tabs { display: flex; padding: 8px 10px 0; gap: 2px; border-bottom: 1px solid var(--line-soft); }
  .tab {
    flex: 1; padding: 8px 4px 10px; text-align: center; color: var(--text-faint);
    font-weight: 550; border-bottom: 2px solid transparent; margin-bottom: -1px; border-radius: 6px 6px 0 0;
  }
  @media (hover: hover) and (pointer: fine) { .tab:hover { color: var(--text-dim); } }
  .tab.active { color: var(--text); border-bottom-color: var(--accent); }
  #inspBody { flex: 1; overflow-y: auto; padding: 14px; }
  .insp-section { margin-bottom: 18px; }
  .insp-title { font-size: 10.5px; font-weight: 650; letter-spacing: .09em; text-transform: uppercase; color: var(--text-faint); margin-bottom: 9px; }
  .row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
  .row > * { min-width: 0; }
  .field { flex: 1; display: flex; flex-direction: column; gap: 4px; }
  .field label { font-size: 10.5px; color: var(--text-faint); }
  .field input, .field select { height: 28px; padding: 0 8px; width: 100%; font-size: 12px; }
  .color-field { display: flex; gap: 6px; align-items: center; }
  .color-field input[type=color] {
    width: 28px; height: 28px; padding: 2px; flex: none; cursor: pointer; border-radius: 7px;
  }
  .color-field input[type=text] { flex: 1; height: 28px; padding: 0 8px; font: 11px var(--mono); }
  .seg { display: flex; background: var(--panel-2); border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
  .seg button { flex: 1; height: 27px; display: grid; place-items: center; color: var(--text-faint); }
  @media (hover: hover) and (pointer: fine) { .seg button:hover { color: var(--text); } }
  .seg button.on { background: var(--accent-soft); color: var(--accent); }
  .seg svg { width: 14px; height: 14px; }
  .empty-hint { color: var(--text-faint); text-align: center; padding: 36px 18px; line-height: 1.7; }
  .crumbs { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 14px; }
  .crumb {
    font: 10.5px var(--mono); color: var(--text-dim); background: var(--panel-2);
    border: 1px solid var(--line); border-radius: 5px; padding: 3px 7px;
  }
  @media (hover: hover) and (pointer: fine) { .crumb:hover { color: var(--accent); border-color: var(--accent); } }
  .crumb.cur { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
  .btn-row { display: flex; gap: 8px; }
  .mini-btn {
    flex: 1; height: 29px; border: 1px solid var(--line); border-radius: 7px; color: var(--text-dim);
    display: flex; align-items: center; justify-content: center; gap: 6px; font-size: 12px;
  }
  @media (hover: hover) and (pointer: fine) {
    .mini-btn:hover { color: var(--text); border-color: var(--line-strong); background: var(--panel-2); }
    .mini-btn.danger:hover { color: var(--danger); border-color: var(--danger); }
  }
  .mini-btn svg { width: 13px; height: 13px; }
  .var-row { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
  .var-row .name { flex: 1; font: 11px var(--mono); color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .var-row input[type=color] { width: 26px; height: 26px; padding: 2px; border-radius: 6px; flex: none; cursor: pointer; }
  .var-row input[type=text] { width: 110px; height: 26px; padding: 0 7px; font: 10.5px var(--mono); flex: none; }

  /* Match the presentation preview sidebar while delegating both actions to
     that sidebar's existing behavior. */
  .shared-export-controls { display: flex; align-items: center; gap: 6px; height: 36px; padding-left: 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--panel); }
  .shared-export-controls select { border: 0; background: transparent; color: var(--text); font-size: 12px; font-weight: 500; padding: 0 4px 0 0; appearance: none; cursor: pointer; }
  .shared-export-controls .shared-export-btn { height: 34px; padding: 0 12px; border-radius: 999px; font-size: 12px; font-weight: 600; color: var(--text); background: var(--bg); }
  .shared-export-btn svg { width: 14px; height: 14px; margin-right: 4px; vertical-align: -2px; }
  .shared-present-btn { display: inline-flex; align-items: center; gap: 6px; height: 36px; padding: 0 16px; border-radius: 20px; background: var(--accent); color: var(--accent-ink); font-size: 13px; font-weight: 600; }
  .shared-present-btn svg { width: 14px; height: 14px; }

  /* ---------- Landing ---------- */
  #landing {
    position: fixed; inset: 0; z-index: 40; display: grid; place-items: center;
    background: radial-gradient(ellipse 70% 55% at 50% 38%, rgba(124,154,255,.08), transparent), var(--bg);
  }
  .landing-card { text-align: center; max-width: 520px; padding: 24px; }
  .landing-card .logo-mark { width: 52px; height: 52px; border-radius: 14px; margin: 0 auto 22px; }
  .landing-card .logo-mark::after { inset: 14px; border-radius: 7px; background: var(--bg); }
  .landing-card h1 { font-size: 30px; font-weight: 650; letter-spacing: -.02em; margin: 0 0 10px; }
  .landing-card p { color: var(--text-dim); margin: 0 0 30px; font-size: 14px; line-height: 1.6; }
  #dropZone {
    border: 1.5px dashed var(--line); border-radius: 16px; padding: 42px 30px; cursor: pointer;
    transition: border-color .15s, background .15s;
  }
  #dropZone.over { border-color: var(--accent); background: var(--accent-soft); }
  @media (hover: hover) and (pointer: fine) { #dropZone:hover { border-color: var(--accent); background: var(--accent-soft); } }
  #dropZone strong { display: block; font-size: 15px; margin-bottom: 6px; }
  #dropZone span { color: var(--text-faint); font-size: 12.5px; }
  .landing-actions { margin-top: 22px; display: flex; justify-content: center; gap: 10px; }
  .ghost-btn {
    height: 36px; padding: 0 16px; border: 1px solid var(--line); border-radius: 9px; color: var(--text-dim);
    display: inline-flex; align-items: center; gap: 8px;
  }
  @media (hover: hover) and (pointer: fine) { .ghost-btn:hover { color: var(--text); border-color: var(--line-strong); } }

  /* ---------- Modals ---------- */
  .modal-back {
    --shared-modal-z-index: 60;
  }
  .modal-head h3 { flex: 1; }
  #codeArea {
    width: 100%; height: 52vh; resize: vertical; font: 12px/1.55 var(--mono);
    padding: 12px; white-space: pre; tab-size: 2; user-select: text;
  }
  .code-scope { display: flex; gap: 2px; background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 2px; }
  .code-scope button { padding: 5px 12px; border-radius: 6px; color: var(--text-faint); font-size: 12px; }
  .code-scope button.on { background: var(--accent-soft); color: var(--accent); }

  /* template picker */
  .tpl-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  .tpl {
    border: 1.5px solid var(--line); border-radius: 10px; overflow: hidden; cursor: pointer; text-align: left;
    background: var(--panel-2); padding: 0;
  }
  @media (hover: hover) and (pointer: fine) { .tpl:hover { border-color: var(--accent); } }
  .tpl .prev { aspect-ratio: 16/9; display: grid; place-items: center; font-size: 11px; color: var(--text-faint); }
  .tpl .lbl { padding: 9px 11px; font-size: 12px; font-weight: 550; border-top: 1px solid var(--line-soft); }

  /* toast */
  #toast {
    position: fixed; bottom: 42px; left: 50%; transform: translateX(-50%) translateY(12px);
    background: var(--panel); border: 1px solid var(--line); color: var(--text);
    padding: 9px 16px; border-radius: 10px; font-size: 12.5px; z-index: 200;
    opacity: 0; pointer-events: none; transition: opacity .18s, transform .18s;
    box-shadow: var(--menu-shadow);
  }
  #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

  /* Shadow DOM does not inherit Omlorix's global animation overrides, so the
     editor mirrors the operating-system preference explicitly. Functionality
     never depends on these transitions. */
  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: .01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: .01ms !important;
      scroll-behavior: auto !important;
    }
  }

  .hidden { display: none !important; }
</style>
<div id="app" class="hidden">
  <!-- ============ TOP BAR ============ -->
  <header id="topbar">
    <button class="tb-btn" id="btnCloseEditor" title="Close editor" aria-label="Close editor">
      ${Icons.resolveIcon("close")}
    </button>
    <input id="deckTitle" value="Untitled deck" spellcheck="false" title="Deck title" aria-label="Presentation title">
    <div class="tb-group">
      <button class="tb-btn" id="btnOpen" title="Open HTML file">
        ${Icons.resolveIcon("upload")}
        Open
      </button>
      <button class="tb-btn" id="btnUndo" title="Undo (⌘Z)" aria-label="Undo">
        ${Icons.resolveIcon("undo")}
      </button>
      <button class="tb-btn" id="btnRedo" title="Redo (⇧⌘Z)" aria-label="Redo">
        ${Icons.resolveIcon("redo")}
      </button>
    </div>
    <div class="tb-group">
      <button class="tb-btn" id="zoomOut" title="Zoom out" aria-label="Zoom out">−</button>
      <button class="zoom-label" id="zoomLabel" title="Fit to screen" aria-label="Fit to screen">100%</button>
      <button class="tb-btn" id="zoomIn" title="Zoom in" aria-label="Zoom in">+</button>
    </div>
    <span id="saveState" role="status" aria-live="polite"></span>
    <div class="spacer"></div>
    <div class="tb-group">
      <button class="tb-btn" id="btnCode" title="Edit HTML source">
        ${Icons.resolveIcon("code")}
        Code
      </button>
      <button class="shared-present-btn" id="btnPresent" title="Present (⌘⏎)">
        ${Icons.resolveIcon("play")}
        Present
      </button>
    </div>
    <div class="shared-export-controls">
      <select id="editorExportFormat" aria-label="Download format">
        <option value="pptx">PPTX</option>
        <option value="pdf">PDF</option>
        <option value="slides_zip">Images</option>
      </select>
      <button class="shared-export-btn" id="btnExport" title="Download">
        ${Icons.resolveIcon("download")}
        Download
      </button>
    </div>
  </header>

  <!-- ============ MAIN ============ -->
  <div id="main">
    <!-- Left: slides -->
    <aside id="slidesPanel">
      <div class="panel-head"><span>Slides</span><span id="slideCount"></span></div>
      <div id="thumbList"></div>
      <button id="addSlideBtn">
        ${Icons.withSvgAttributes("plus", { "width": "14", "height": "14" })}
        New slide
      </button>
    </aside>

    <!-- Center: canvas -->
    <div id="canvasArea">
      <div id="canvasScroller">
        <div id="stage">
          <!-- Active content is removed before loading and the frame CSP blocks
               scripts. Leaving this same-origin srcdoc unsandboxed keeps direct
               DOM editing and pointer events reliable in WebKit/Safari. -->
          <iframe id="deckFrame" title="Slide canvas"></iframe>
          <div id="overlay">
            <div id="hoverBox"></div>
            <div id="guideV" class="guide"></div>
            <div id="guideH" class="guide"></div>
            <div id="selBox">
              <div id="selTag"></div>
              <div class="handle" data-h="nw"></div><div class="handle" data-h="n"></div>
              <div class="handle" data-h="ne"></div><div class="handle" data-h="e"></div>
              <div class="handle" data-h="se"></div><div class="handle" data-h="s"></div>
              <div class="handle" data-h="sw"></div><div class="handle" data-h="w"></div>
            </div>
          </div>
        </div>
      </div>
      <!-- floating text toolbar -->
      <div id="textBar">
        <!-- font size -->
        <div class="tbf">
          <input id="tbSize" value="40" spellcheck="false" autocomplete="off" title="Font size (px)">
          <button class="chev" data-menu="sizeMenu" title="Font size presets">${Icons.resolveIcon("chevron")}</button>
          <div class="tmenu" id="sizeMenu"></div>
        </div>
        <div class="sep"></div>
        <!-- font family -->
        <div class="tbf">
          <button class="tbtn" id="tbFamilyBtn" data-menu="familyMenu" title="Font family"><span id="tbFamilyLbl">Font</span>${Icons.withSvgAttributes("chevron", { "style": "width:11px;height:11px" })}</button>
          <div class="tmenu" id="familyMenu"></div>
        </div>
        <div class="sep"></div>
        <!-- colors -->
        <button class="tbtn swatch" id="tbForeBtn" title="Text color"><span class="glyph">A</span><i class="chip" id="tbForeChip"></i></button>
        <input type="color" id="tbFore" class="hidden-color" tabindex="-1">
        <button class="tbtn swatch" id="tbHiliteBtn" title="Highlight color">${Icons.resolveIcon("markdownEditorIcons.paint")}<i class="chip" id="tbHiliteChip"></i></button>
        <input type="color" id="tbHilite" class="hidden-color" tabindex="-1">
        <div class="sep"></div>
        <!-- inline formatting -->
        <button class="tbtn fmt" data-cmd="bold" title="Bold (⌘B)">${Icons.resolveIcon("markdownEditorIcons.bold")}</button>
        <button class="tbtn fmt" data-cmd="italic" title="Italic (⌘I)">${Icons.resolveIcon("markdownEditorIcons.italic")}</button>
        <button class="tbtn fmt" data-cmd="underline" title="Underline (⌘U)">${Icons.resolveIcon("markdownEditorIcons.underline")}</button>
        <button class="tbtn fmt" data-cmd="strikeThrough" title="Strikethrough">${Icons.resolveIcon("markdownEditorIcons.strike")}</button>
        <button class="tbtn glyph-btn fmt" data-cmd="superscript" title="Superscript">x²</button>
        <button class="tbtn glyph-btn fmt" data-cmd="subscript" title="Subscript">x₂</button>
        <div class="sep"></div>
        <!-- paragraph align -->
        <div class="tbf">
          <button class="tbtn" data-menu="alignMenu" title="Text align">${Icons.resolveIcon("markdownEditorIcons.alignLeft")}</button>
          <div class="tmenu" id="alignMenu">
            <button class="tmi" data-align="left">${Icons.resolveIcon("markdownEditorIcons.alignLeft")}<span class="mi-label">Left</span></button>
            <button class="tmi" data-align="center">${Icons.resolveIcon("markdownEditorIcons.alignCenter")}<span class="mi-label">Center</span></button>
            <button class="tmi" data-align="right">${Icons.resolveIcon("markdownEditorIcons.alignRight")}<span class="mi-label">Right</span></button>
            <button class="tmi" data-align="justify">${Icons.resolveIcon("markdownEditorIcons.alignJustify")}<span class="mi-label">Justify</span></button>
          </div>
        </div>
        <!-- vertical align -->
        <div class="tbf">
          <button class="tbtn" data-menu="valignMenu" title="Vertical align (box)">${Icons.resolveIcon("verticalAlign")}</button>
          <div class="tmenu" id="valignMenu">
            <button class="tmi" data-valign="top">${Icons.resolveIcon("verticalAlignTop")}<span class="mi-label">Top</span></button>
            <button class="tmi" data-valign="middle">${Icons.resolveIcon("verticalAlignMiddle")}<span class="mi-label">Middle</span></button>
            <button class="tmi" data-valign="bottom">${Icons.resolveIcon("verticalAlignBottom")}<span class="mi-label">Bottom</span></button>
          </div>
        </div>
        <!-- spacing -->
        <div class="tbf">
          <button class="tbtn" data-menu="spacingMenu" title="Line & letter spacing">${Icons.resolveIcon("textSpacing")}</button>
          <div class="tmenu" id="spacingMenu"></div>
        </div>
        <!-- lists -->
        <div class="tbf">
          <button class="tbtn" data-menu="listMenu" title="Lists">${Icons.resolveIcon("markdownEditorIcons.list")}</button>
          <div class="tmenu" id="listMenu">
            <button class="tmi" data-list="ul">${Icons.resolveIcon("markdownEditorIcons.list")}<span class="mi-label">Bulleted list</span></button>
            <button class="tmi" data-list="ol">${Icons.resolveIcon("markdownEditorIcons.ordered")}<span class="mi-label">Numbered list</span></button>
          </div>
        </div>
        <!-- layers -->
        <div class="tbf">
          <button class="tbtn" data-menu="layerMenu" title="Arrange / layers">${Icons.resolveIcon("layers")}</button>
          <div class="tmenu" id="layerMenu">
            <button class="tmi" data-layer="front">Bring to front</button>
            <button class="tmi" data-layer="fwd">Bring forward</button>
            <button class="tmi" data-layer="bwd">Send backward</button>
            <button class="tmi" data-layer="back">Send to back</button>
          </div>
        </div>
        <div class="sep"></div>
        <button class="tbtn fmt" data-cmd="removeFormat" title="Clear formatting">${Icons.resolveIcon("markdownEditorIcons.clear")}</button>
      </div>
    </div>

    <!-- Right: inspector -->
    <aside id="inspector">
      <div class="tabs">
        <button class="tab active" data-tab="element">Element</button>
        <button class="tab" data-tab="slide">Slide</button>
        <button class="tab" data-tab="theme">Theme</button>
      </div>
      <div id="inspBody"></div>
    </aside>
  </div>

</div>

<!-- ============ LANDING ============ -->
<div id="landing">
  <div class="landing-card">
    <div class="logo-mark"></div>
    <h1>Deck Studio</h1>
    <p>A precision editor for HTML slide decks.<br>Open a deck, edit anything, download the result.</p>
    <div id="dropZone">
      <strong>Drop your HTML deck here</strong>
      <span>or click to browse — expects 1920×1080 <code>.slide</code> sections</span>
    </div>
    <div class="landing-actions">
      <button class="ghost-btn" id="btnNewDeck">
        ${Icons.withSvgAttributes("plus", { "width": "14", "height": "14" })}
        Start a blank deck
      </button>
    </div>
  </div>
</div>

<!-- ============ CODE MODAL ============ -->
<div class="modal-back shared-modal-overlay" id="codeModal" aria-hidden="true" hidden>
  <div class="modal shared-modal shared-modal--large shared-modal--fixed" role="dialog" aria-modal="true" aria-labelledby="codeModalTitle" tabindex="-1">
    <header class="modal-head shared-modal-header shared-modal-header--main">
      <h3 class="shared-modal-title" id="codeModalTitle">HTML source</h3>
      <div class="code-scope">
        <button id="scopeSlide" class="on">Current slide</button>
        <button id="scopeDeck">Whole deck</button>
      </div>
      <button class="shared-modal-close" id="codeClose" type="button" title="Close" aria-label="Close">${Icons.close}</button>
    </header>
    <div class="modal-body shared-modal-body">
      <textarea id="codeArea" spellcheck="false"></textarea>
    </div>
    <footer class="modal-foot shared-modal-footer">
      <button class="om-button border cancel" id="codeCancel" type="button">Cancel</button>
      <button class="om-button border submit" id="codeApply" type="button">Apply changes</button>
    </footer>
  </div>
</div>

<!-- ============ TEMPLATE PICKER ============ -->
<div class="modal-back shared-modal-overlay" id="tplModal" aria-hidden="true" hidden>
  <div class="modal shared-modal shared-modal--wide shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="tplModalTitle" tabindex="-1">
    <header class="modal-head shared-modal-header shared-modal-header--main"><h3 class="shared-modal-title" id="tplModalTitle">New slide</h3><button class="shared-modal-close" id="tplClose" type="button" title="Close" aria-label="Close">${Icons.close}</button></header>
    <div class="modal-body shared-modal-body">
      <div class="tpl-grid">
        <button class="tpl" data-tpl="blank-light"><div class="prev" style="background:#fbfaf7;color:#999">Blank · Light</div><div class="lbl">Blank light</div></button>
        <button class="tpl" data-tpl="blank-dark"><div class="prev" style="background:#111827;color:#667">Blank · Dark</div><div class="lbl">Blank dark</div></button>
        <button class="tpl" data-tpl="title"><div class="prev" style="background:#111827;color:#dde;font-size:14px;font-weight:600">Big Title</div><div class="lbl">Title slide</div></button>
        <button class="tpl" data-tpl="two-col"><div class="prev" style="background:#fbfaf7;color:#888">▌ ▐&nbsp; Two columns</div><div class="lbl">Two columns</div></button>
        <button class="tpl" data-tpl="cards"><div class="prev" style="background:#fbfaf7;color:#888">▢ ▢ ▢&nbsp; Cards</div><div class="lbl">Three cards</div></button>
        <button class="tpl" data-tpl="duplicate"><div class="prev" style="background:var(--accent-soft);color:var(--accent)">⧉ Duplicate current</div><div class="lbl">Duplicate current slide</div></button>
      </div>
    </div>
  </div>
</div>

<div id="toast"></div>
<input type="file" id="fileInput" accept=".html,.htm,text/html" class="hidden">
`;
// The production build hashes these URLs in index.html. Clone the rewritten
// links so the Shadow DOM never falls back to unhashed asset names.
const sharedStylesheets = Array.from(
  document.querySelectorAll('link[data-slide-presentation-editor-stylesheet]'),
  sourceStylesheet => {
    const stylesheet = sourceStylesheet.cloneNode(false);
    stylesheet.removeAttribute('data-slide-presentation-editor-stylesheet');
    return stylesheet;
  }
);
root.prepend(...sharedStylesheets);
host.dataset.embedded = 'true';
host.dataset.theme = 'dark';

"use strict";
/* =====================================================================
   Deck Studio — single-file HTML slide deck editor
   ===================================================================== */

const SLIDE_W = 1920, SLIDE_H = 1080, MAX_SLIDES = 50;
const SLIDE_CONTRACT_CSS = `
/* omlorix-slide-contract */
.slide { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }
`;
function withSlideContractCss(css) {
  const withoutPriorContract = String(css || '').replace(
    /\/\* omlorix-slide-contract \*\/[\s\S]*?\.slide\s*\{[^}]*\}/g,
    ''
  ).trim();
  return `${withoutPriorContract}\n${SLIDE_CONTRACT_CSS}`.trim();
}
const ownerDocument = host.ownerDocument;
const $ = (selector, scope = root) => scope.querySelector(selector);
const $$ = (selector, scope = root) => [...scope.querySelectorAll(selector)];
const EMBEDDED = true;
let editorController = null;

function tr(key, fallback) {
  if (typeof window.getTranslation === 'function') {
    return window.getTranslation(key, fallback);
  }
  return fallback;
}

function formatShortcutTranslation(key, fallback, shortcutModifier) {
  return tr(key, fallback).replaceAll('{keyboard_shortcut_ctrl}', shortcutModifier);
}

function primaryShortcutModifier() {
  const platform = String(navigator.userAgentData?.platform || navigator.platform || '');
  return /mac|iphone|ipad|ipod/i.test(platform)
    ? 'Cmd'
    : tr('keyboard_shortcut_ctrl', 'Ctrl');
}

const state = {
  loaded: false,
  active: 0,          // active slide index
  scale: 0.5,
  fitMode: true,
  selected: null,     // selected element inside iframe
  hovered: null,
  editing: false,     // contentEditable text editing active
  tab: 'element',
  undo: [], redo: [],
};

const server = {
  // Every mount gets a new session. Async work captures this value and must
  // not update shared editor state after another presentation is mounted.
  sessionId: 0,
  revision: 0,
  openedRevision: 0,
  renderRevision: 0,
  dirty: false,
  editVersion: 0,
  loaded: false,
  saveInFlight: null,
  renderInFlight: null,
  renderRequestedRevision: 0,
  saveTimer: null,
  renderTimer: null,
  conflict: false,
};
const MAX_SERVER_RENDER_DRAIN_ITERATIONS = 8;

const frame = $('#deckFrame');
const stage = $('#stage');
const selBox = $('#selBox');
const hoverBox = $('#hoverBox');
const selTag = $('#selTag');

/* Follow Omlorix's appearance instead of maintaining an editor-only theme. */
function syncThemeFromSite() {
  host.dataset.theme = ownerDocument.documentElement.dataset.mode === 'light' ? 'light' : 'dark';
}
syncThemeFromSite();
new MutationObserver(syncThemeFromSite).observe(ownerDocument.documentElement, {
  attributes: true,
  attributeFilter: ['data-mode'],
});

const idoc = () => frame.contentDocument;
const ibody = () => idoc().body;
const slides = () => $$('.slide', ibody());
const activeSlide = () => slides()[state.active] || null;
const deckStyleEl = () => idoc().getElementById('__deckstyle');

/* ---------------------------------------------------------------------
   Toast
--------------------------------------------------------------------- */
let toastT;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove('show'), 2200);
}

function setSaveState(message, kind = '') {
  const target = $('#saveState');
  if (!target) return;
  target.textContent = message;
  target.className = kind;
}

function showEmbeddedLoadFailure(message) {
  // Keep the editor header available so a failed network or validation load
  // never traps the user inside the fullscreen overlay.
  $('#landing').classList.add('hidden');
  $('#app').classList.remove('hidden');
  setSaveState(message, 'error');
  toast(message);
}

function bridgeRequest(type, payload) {
  const handler = type === 'omlorix-presentation-editor-save'
    ? editorController?.save
    : editorController?.render;
  if (typeof handler !== 'function') {
    return Promise.reject(new Error(
      tr('slide_presentation_editor_unavailable', 'The presentation editor is not available.')
    ));
  }
  return Promise.resolve().then(() => handler(payload));
}

/* ---------------------------------------------------------------------
   Loading & parsing
--------------------------------------------------------------------- */
const EDITOR_CSS = `
  html, body { margin:0 !important; padding:0 !important; overflow:hidden !important; background:transparent !important; }
  body > .slide, body .slide { margin:0 !important; }
  .slide { display:none !important; }
  .slide.__amp-active { display:block !important; }
  .slide.__amp-active * { cursor: default; }
  [contenteditable="true"] { outline: none !important; cursor: text !important; }
  ::selection { background: rgba(124,154,255,.35); }
`;

// Apply the same restrictive resource policy used by the server-side deck
// sanitizer. This also protects unsaved source while the user is editing it.
const EDITOR_FRAME_CSP = "default-src 'none'; img-src data: blob:; media-src data: blob:; font-src data:; style-src 'unsafe-inline';";
const editorFrameCspMeta = () =>
  `<meta http-equiv="Content-Security-Policy" content="${EDITOR_FRAME_CSP}">`;

function parseDeckHTML(text) {
  const doc = new DOMParser().parseFromString(text, 'text/html');
  const css = $$('style', doc).map(s => s.textContent).join('\n\n');
  let slideEls = $$('section.slide', doc);
  if (!slideEls.length) slideEls = $$('.slide', doc);
  if (!slideEls.length) return null;

  // Presentation decks are static documents. Strip active content before it
  // is copied into the sandboxed editing frame. The backend applies the same
  // policy to saved decks, but this client-side boundary also covers imported
  // files and older stored presentations. Besides keeping the editor safe, it
  // prevents browsers from repeatedly reporting blocked script execution for
  // every script nested inside a slide.
  const sanitizedSlidesHTML = slideEls.map((slide, index) => {
    const clone = doc.createElement('section');
    [...slide.attributes].forEach(attribute => clone.setAttribute(attribute.name, attribute.value));
    clone.innerHTML = slide.innerHTML;
    clone.classList.add('slide');
    clone.setAttribute('data-slide-index', String(index + 1));
    if (!clone.getAttribute('data-slide-title')) {
      const heading = clone.querySelector('h1, h2, h3');
      clone.setAttribute(
        'data-slide-title',
        heading?.textContent?.trim() || `${tr('slide_presentation_editor_slide', 'Slide')} ${index + 1}`
      );
    }
    $$('script, noscript, iframe, frame, object, embed', clone).forEach(element => element.remove());
    $$('*', clone).forEach(element => {
      [...element.attributes].forEach(attribute => {
        if (attribute.name.toLowerCase().startsWith('on')) {
          element.removeAttribute(attribute.name);
        }
      });
    });
    return clone.outerHTML;
  });

  return { css: withSlideContractCss(css), slidesHTML: sanitizedSlidesHTML, title: doc.title || 'Untitled deck' };
}

function buildSrcdoc(css, slidesHTML, loadId) {
  return `<!DOCTYPE html><html data-editor-load-id="${loadId}"><head><meta charset="utf-8">` +
    editorFrameCspMeta() +
    `<style id="__deckstyle">${css}</style>` +
    `<style id="__ampcss">${EDITOR_CSS}</style>` +
    `</head><body>${slidesHTML.join('\n')}</body></html>`;
}

let frameLoadId = 0;

function loadDeck(text, name) {
  const parsed = parseDeckHTML(text);
  if (!parsed) {
    toast(tr('slide_presentation_editor_no_slide_sections_file', 'No .slide sections found in that file.'));
    return;
  }
  if (parsed.slidesHTML.length > MAX_SLIDES) {
    toast(tr('slide_presentation_editor_slide_limit', 'A presentation can contain at most 50 slides.'));
    return;
  }
  $('#deckTitle').value = parsed.title;
  state.active = 0; state.selected = null; state.undo = []; state.redo = [];
  const loadId = ++frameLoadId;
  frame.onload = () => {
    // A queued load event from a prior deck or the blank reset must not bind
    // handlers or mark the replacement deck ready.
    const loadedId = frame.contentDocument?.documentElement?.dataset.editorLoadId;
    if (loadId !== frameLoadId || loadedId !== String(loadId)) return;
    frame.onload = null;
    bindFrameEvents();
    afterDeckLoaded();
  };
  frame.srcdoc = buildSrcdoc(parsed.css, parsed.slidesHTML, loadId);
  $('#landing').classList.add('hidden');
  $('#app').classList.remove('hidden');
  state.loaded = true;
  if (name && !EMBEDDED) toast(`Opened ${name}`);
}

function afterDeckLoaded() {
  setActive(0);
  fitZoom();
  renderThumbs();
  pushUndo();
  renderInspector();
  if (pendingServerPayload) {
    server.revision = Number(pendingServerPayload.canvas_revision) || 0;
    server.openedRevision = server.revision;
    server.renderRevision = Number(pendingServerPayload.render_revision) || 0;
    server.dirty = false;
    server.conflict = false;
    server.loaded = true;
    setSaveState(tr('slide_presentation_editor_saved', 'Saved'), 'saved');
    pendingServerPayload = null;
  }
  editorController?.onReady?.();
}

let pendingServerPayload = null;

function loadEmbeddedDeck(payload) {
  const html = String(payload?.html || '');
  if (!html) {
    throw new Error(tr('slide_presentation_editor_load_failed', 'Failed to open the presentation editor.'));
  }
  pendingServerPayload = payload;
  loadDeck(html, null);
  $('#deckTitle').value = String(payload.title || tr('slide_presentation_default_title', 'Presentation'));
}

const BLANK_DECK = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>New deck</title>
<style>
  :root {
    --ink: #131722; --paper: #fbfaf7; --accent: #4a6dff; --muted: #7a828f;
    --display: Georgia, 'Times New Roman', serif;
    --sans: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #d9d9d4; font-family: var(--sans); color: var(--ink); }
  body { padding: 40px 0; }
  .slide { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; margin: 0 auto 32px; background: var(--paper); }
</style>
</head>
<body>
<section class="slide" data-slide-index="1" data-slide-title="Title">
  <div style="position:absolute;left:120px;top:340px;width:1400px;">
    <div style="font:700 20px/1 var(--sans);letter-spacing:.18em;text-transform:uppercase;color:var(--accent);">New presentation</div>
    <h1 style="margin:28px 0 0;font:400 150px/.95 var(--display);letter-spacing:-.04em;">Your title here</h1>
    <p style="margin:36px 0 0;max-width:760px;font-size:28px;line-height:1.4;color:var(--muted);">A short subtitle that frames the story.</p>
  </div>
  <div style="position:absolute;left:120px;bottom:100px;right:120px;height:1px;background:rgba(19,23,34,.15);"></div>
</section>
</body>
</html>`;

/* ---------------------------------------------------------------------
   Slides: activation, thumbnails, ops
--------------------------------------------------------------------- */
function setActive(i) {
  const all = slides();
  if (!all.length) return;
  state.active = Math.max(0, Math.min(i, all.length - 1));
  all.forEach((s, k) => s.classList.toggle('__amp-active', k === state.active));
  select(null);
  updateThumbActive();
  if (state.tab === 'slide') renderInspector();
}

function updateThumbActive() {
  $$('#thumbList .thumb').forEach((t, k) => t.classList.toggle('active', k === state.active));
  $('#slideCount').textContent = `${state.active + 1} / ${slides().length}`;
}

let thumbTimer;
let thumbCssKey = null;             // thumbs embed the deck CSS — full rebuild when it changes
const dirtyThumbSlides = new WeakSet(); // slides whose content changed since their thumb was rendered
// while typing, previews stay stale — re-render when the edit session ends (commit)
function scheduleThumbs() {
  if (state.editing) return;
  clearTimeout(thumbTimer);
  thumbTimer = setTimeout(renderThumbs, 450);
}

// climb from a mutated node in the frame document to its owning .slide
function slideOf(node) {
  let n = node.nodeType === 1 ? node : node.parentElement;
  while (n && n !== idoc().body) {
    if (n.classList && n.classList.contains('slide')) return n;
    n = n.parentElement;
  }
  return null;
}

/* Build one thumbnail element for slide `s` at index `i`.
   Indexes are resolved dynamically at event time, so reused thumbs never go stale. */
function buildThumb(s, i, css) {
  const clone = s.cloneNode(true);
  clone.classList.remove('__amp-active');
  clone.removeAttribute('contenteditable');
  const item = ownerDocument.createElement('div');
  item.className = 'thumb' + (i === state.active ? ' active' : '');
  item.draggable = true;
  item.dataset.idx = i;
  item.tabIndex = 0;
  item.setAttribute('aria-label', `${tr('slide_presentation_editor_slide', 'Slide')} ${i + 1}`);
  const num = ownerDocument.createElement('div');
  num.className = 'thumb-num'; num.textContent = i + 1;
  const acts = ownerDocument.createElement('div');
  acts.className = 'thumb-actions';
  acts.innerHTML = `
    <button data-act="dup" title="${tr('slide_presentation_editor_duplicate', 'Duplicate')}" aria-label="${tr('slide_presentation_editor_duplicate', 'Duplicate')}">${Icons.resolveIcon("copy")}</button>
    <button data-act="del" title="${tr('common_delete', 'Delete')}" aria-label="${tr('common_delete', 'Delete')}">${Icons.resolveIcon("trash")}</button>`;
  const fr = ownerDocument.createElement('iframe');
  fr.className = 'thumb-frame';
  fr.setAttribute('tabindex', '-1');
  // The thumbnail receives already-sanitized slide markup and the restrictive
  // frame CSP. An iframe sandbox is unnecessary and produces repeated WebKit
  // console errors for browser-injected frame helpers.
  fr.removeAttribute('sandbox');
  // CSS-driven scaling via 100vw of the thumb viewport — no parse-timing pitfalls
  fr.srcdoc = `<!DOCTYPE html><html><head><meta charset="utf-8">${editorFrameCspMeta()}<style>${css}</style>` +
    `<style>html,body{margin:0!important;padding:0!important;overflow:hidden!important;background:transparent!important}` +
    `.slide{margin:0!important;display:block!important}` +
    `#w{width:${SLIDE_W}px;height:${SLIDE_H}px;transform-origin:0 0;` +
    `transform:scale(calc(100vw / ${SLIDE_W}px));}</style></head>` +
    `<body><div id="w">${clone.outerHTML}</div></body></html>`;
  item.append(num, acts, fr);
  const idxOf = () => slides().indexOf(item._slide);
  item.addEventListener('click', e => {
    const act = e.target.closest('[data-act]');
    if (act) {
      e.stopPropagation();
      if (act.dataset.act === 'dup') duplicateSlide(idxOf());
      else deleteSlide(idxOf());
      return;
    }
    setActive(idxOf());
  });
  item.addEventListener('keydown', e => {
    if (e.target.closest('button')) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setActive(idxOf());
    } else if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault();
      const from = idxOf();
      const movedSlide = item._slide;
      moveSlide(from, e.key === 'ArrowUp' ? Math.max(0, from - 1) : Math.min(slides().length, from + 2));
      // Reordering renders thumbnails synchronously and may replace this node.
      // Resolve by slide identity so repeated Alt+Arrow presses keep working.
      $$('#thumbList .thumb').find(thumb => thumb._slide === movedSlide)?.focus();
    }
  });
  // drag reorder
  item.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', String(idxOf())); e.dataTransfer.effectAllowed = 'move'; });
  item.addEventListener('dragover', e => {
    e.preventDefault();
    const r = item.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    item.classList.toggle('drag-over-before', before);
    item.classList.toggle('drag-over-after', !before);
  });
  item.addEventListener('dragleave', () => item.classList.remove('drag-over-before', 'drag-over-after'));
  item.addEventListener('drop', e => {
    e.preventDefault();
    const from = +e.dataTransfer.getData('text/plain');
    const r = item.getBoundingClientRect();
    const to = idxOf() + (e.clientY < r.top + r.height / 2 ? 0 : 1);
    item.classList.remove('drag-over-before', 'drag-over-after');
    moveSlide(from, to);
  });
  return item;
}

/* Incremental thumbnail rendering: reuse thumbs by slide-element identity so
   duplicate/add/delete/reorder never reload untouched iframes (no flicker).
   Only new or content-dirty slides get a fresh iframe. */
function renderThumbs() {
  if (!state.loaded) return;
  const list = $('#thumbList');
  const css = withSlideContractCss(deckStyleEl().textContent);
  const slideEls = slides();
  const full = css !== thumbCssKey;   // deck CSS changed → every thumb must reload anyway
  thumbCssKey = css;

  const bySlide = new Map();
  $$('.thumb', list).forEach(t => { if (t._slide) bySlide.set(t._slide, t); });

  const wanted = slideEls.map((s, i) => {
    const item = bySlide.get(s);
    const dirty = dirtyThumbSlides.has(s);
    if (!full && item && !dirty) {
      item.querySelector('.thumb-num').textContent = i + 1; // cheap renumber, no reload
      item.setAttribute('aria-label', `${tr('slide_presentation_editor_slide', 'Slide')} ${i + 1}`);
      return item;
    }
    const fresh = buildThumb(s, i, css);
    fresh._slide = s;
    if (item) item.replaceWith(fresh); // swap the single stale thumb only
    dirtyThumbSlides.delete(s);
    return fresh;
  });

  // drop thumbs whose slide no longer exists
  $$('.thumb', list).forEach(t => { if (!slideEls.includes(t._slide)) t.remove(); });

  // reconcile order front-to-back; nodes already in position are untouched
  // (re-parenting an iframe reloads it, so we only move when the order changed)
  for (let i = 0; i < wanted.length; i++) {
    if (list.children[i] !== wanted[i]) list.insertBefore(wanted[i], list.children[i] || null);
  }
  updateThumbActive();
}

function renumberSlides() {
  slides().forEach((s, i) => s.setAttribute('data-slide-index', i + 1));
}

function moveSlide(from, to) {
  const all = slides();
  if (from === to || from === to - 1 && to > from) { /* no-op cases handled below */ }
  const el = all[from];
  if (!el) return;
  const ref = all[to] || null;
  if (ref === el) return;
  ibody().insertBefore(el, ref);
  renumberSlides();
  const newIdx = slides().indexOf(el);
  state.active = newIdx;
  setActive(newIdx);
  renderThumbs();
  commit('Reordered slides');
}

function duplicateSlide(i) {
  if (slides().length >= MAX_SLIDES) {
    toast(tr('slide_presentation_editor_slide_limit', 'A presentation can contain at most 50 slides.'));
    return;
  }
  const s = slides()[i];
  if (!s) return;
  const copy = s.cloneNode(true);
  copy.classList.remove('__amp-active');
  s.after(copy);
  renumberSlides();
  setActive(i + 1);
  renderThumbs();
  commit('Slide duplicated');
}

function deleteSlide(i) {
  const all = slides();
  if (all.length <= 1) {
    toast(tr('slide_presentation_editor_requires_slide', 'A deck needs at least one slide.'));
    return;
  }
  all[i].remove();
  renumberSlides();
  setActive(Math.min(i, slides().length - 1));
  renderThumbs();
  commit('Slide deleted');
}

/* -------- slide templates -------- */
function templateHtml() {
  return {
  'blank-light': `<section class="slide" style="background:#fbfaf7;"></section>`,
  'blank-dark': `<section class="slide" style="background:#111827;color:#f4f5f7;"></section>`,
  'title': `<section class="slide" style="background:#111827;color:#f4f5f7;">
    <div style="position:absolute;left:120px;top:360px;width:1500px;">
      <div style="font:700 20px/1 ui-monospace,monospace;letter-spacing:.16em;text-transform:uppercase;opacity:.6;">${tr('slide_presentation_editor_template_section', 'Section')}</div>
      <h1 style="margin:26px 0 0;font-size:140px;line-height:.95;letter-spacing:-.04em;font-weight:600;">${tr('slide_presentation_editor_template_section_title', 'Section title')}</h1>
    </div>
    <div style="position:absolute;left:120px;bottom:110px;width:220px;height:6px;background:#4a6dff;"></div>
  </section>`,
  'two-col': `<section class="slide" style="background:#fbfaf7;color:#131722;padding:100px 120px;">
    <h2 style="margin:0;font-size:72px;letter-spacing:-.03em;line-height:1;">${tr('slide_presentation_editor_template_two_columns', 'Two columns')}</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:60px;margin-top:80px;">
      <div><h3 style="margin:0 0 18px;font-size:34px;">${tr('slide_presentation_editor_left', 'Left')}</h3><p style="margin:0;font-size:26px;line-height:1.5;color:#5a6270;">${tr('slide_presentation_editor_template_replace_content', 'Replace this with your content.')}</p></div>
      <div><h3 style="margin:0 0 18px;font-size:34px;">${tr('slide_presentation_editor_right', 'Right')}</h3><p style="margin:0;font-size:26px;line-height:1.5;color:#5a6270;">${tr('slide_presentation_editor_template_replace_content', 'Replace this with your content.')}</p></div>
    </div>
  </section>`,
  'cards': `<section class="slide" style="background:#fbfaf7;color:#131722;padding:100px 120px;">
    <h2 style="margin:0;font-size:72px;letter-spacing:-.03em;line-height:1;">${tr('slide_presentation_editor_template_three_points', 'Three points')}</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:28px;margin-top:90px;">
      <div style="border:1px solid rgba(19,23,34,.15);padding:44px;min-height:420px;"><div style="font:700 17px/1 ui-monospace,monospace;letter-spacing:.12em;">01</div><h3 style="margin:150px 0 14px;font-size:38px;">${tr('slide_presentation_editor_template_first', 'First')}</h3><p style="margin:0;font-size:22px;line-height:1.45;color:#5a6270;">${tr('slide_presentation_editor_template_describe_point', 'Describe this point.')}</p></div>
      <div style="border:1px solid rgba(19,23,34,.15);padding:44px;min-height:420px;background:#111827;color:#f4f5f7;"><div style="font:700 17px/1 ui-monospace,monospace;letter-spacing:.12em;">02</div><h3 style="margin:150px 0 14px;font-size:38px;">${tr('slide_presentation_editor_template_second', 'Second')}</h3><p style="margin:0;font-size:22px;line-height:1.45;color:#aab2bf;">${tr('slide_presentation_editor_template_describe_point', 'Describe this point.')}</p></div>
      <div style="border:1px solid rgba(19,23,34,.15);padding:44px;min-height:420px;"><div style="font:700 17px/1 ui-monospace,monospace;letter-spacing:.12em;">03</div><h3 style="margin:150px 0 14px;font-size:38px;">${tr('slide_presentation_editor_template_third', 'Third')}</h3><p style="margin:0;font-size:22px;line-height:1.45;color:#5a6270;">${tr('slide_presentation_editor_template_describe_point', 'Describe this point.')}</p></div>
    </div>
  </section>`,
  };
}

function insertTemplate(key) {
  closeTemplateModal();
  if (key === 'duplicate') { duplicateSlide(state.active); return; }
  if (slides().length >= MAX_SLIDES) {
    toast(tr('slide_presentation_editor_slide_limit', 'A presentation can contain at most 50 slides.'));
    return;
  }
  const html = templateHtml()[key];
  if (!html) return;
  const tmp = idoc().createElement('div');
  tmp.innerHTML = html;
  const el = tmp.firstElementChild;
  const cur = activeSlide();
  cur ? cur.after(el) : ibody().appendChild(el);
  renumberSlides();
  const insertedIndex = slides().indexOf(el);
  if (!el.getAttribute('data-slide-title')) {
    // The backend presentation contract requires a stable title for every
    // slide. Derive a useful title from the template heading when possible,
    // and give blank templates a localized numbered fallback.
    const heading = el.querySelector('h1, h2, h3');
    el.setAttribute(
      'data-slide-title',
      heading?.textContent?.trim()
        || `${tr('slide_presentation_editor_slide', 'Slide')} ${insertedIndex + 1}`
    );
  }
  setActive(insertedIndex);
  renderThumbs();
  commit('Slide added');
}

/* ---------------------------------------------------------------------
   Zoom / stage geometry
--------------------------------------------------------------------- */
function applyZoom() {
  frame.style.transform = `scale(${state.scale})`;
  stage.style.width = SLIDE_W * state.scale + 'px';
  stage.style.height = SLIDE_H * state.scale + 'px';
  $('#zoomLabel').textContent = Math.round(state.scale * 100) + '%';
  updateOverlay();
}

function fitZoom() {
  const area = $('#canvasScroller');
  const pad = 96;
  const s = Math.min((area.clientWidth - pad) / SLIDE_W, (area.clientHeight - pad) / SLIDE_H);
  state.scale = Math.max(0.05, Math.min(s, 2));
  state.fitMode = true;
  applyZoom();
}

function setZoom(z) {
  state.scale = Math.max(0.08, Math.min(z, 4));
  state.fitMode = false;
  applyZoom();
}

$('#zoomIn').addEventListener('click', () => setZoom(state.scale * 1.2));
$('#zoomOut').addEventListener('click', () => setZoom(state.scale / 1.2));
$('#zoomLabel').addEventListener('click', fitZoom);
window.addEventListener('resize', () => { if (state.fitMode) fitZoom(); });
$('#canvasArea').addEventListener('wheel', e => {
  if (!(e.ctrlKey || e.metaKey)) return;
  e.preventDefault();
  setZoom(state.scale * (e.deltaY < 0 ? 1.08 : 1 / 1.08));
}, { passive: false });

/* ---------------------------------------------------------------------
   Selection & overlay
--------------------------------------------------------------------- */
function pickTarget(el) {
  // walk up from raw target: don't select the slide itself via click-through,
  // select direct meaningful elements
  if (!el || el === ibody() || el === idoc().documentElement) return null;
  let n = el;
  while (n && n !== ibody() && !(n instanceof idoc().defaultView.HTMLElement)) n = n.parentElement;
  if (!n || n === ibody()) return null;
  if (n.classList.contains('slide')) return null;
  return n;
}

function select(el) {
  if (state.editing && el !== state.selected) stopTextEdit();
  state.selected = el;
  updateOverlay();
  if (state.tab === 'element') renderInspector();
}

function describe(el) {
  if (!el) return '';
  let d = el.tagName.toLowerCase();
  if (el.id) d += '#' + el.id;
  else if (el.classList.length) d += '.' + [...el.classList].filter(c => !c.startsWith('__amp')).slice(0, 2).join('.');
  return d;
}

function rectOf(el) {
  const r = el.getBoundingClientRect(); // iframe coords == slide coords (unscaled)
  return { x: r.left * state.scale, y: r.top * state.scale, w: r.width * state.scale, h: r.height * state.scale };
}

function updateOverlay() {
  const el = state.selected;
  if (!el || !el.isConnected) {
    selBox.style.display = 'none';
    if (el && !el.isConnected) state.selected = null;
  } else {
    const r = rectOf(el);
    selBox.style.display = 'block';
    selBox.style.left = r.x + 'px'; selBox.style.top = r.y + 'px';
    selBox.style.width = r.w + 'px'; selBox.style.height = r.h + 'px';
    selBox.classList.toggle('editing', state.editing);
    selTag.textContent = describe(el) + `  ${Math.round(el.getBoundingClientRect().width)}×${Math.round(el.getBoundingClientRect().height)}`;
    $$('.handle', selBox).forEach(h => h.style.display = state.editing ? 'none' : 'block');
  }
  const hv = state.hovered;
  if (hv && hv.isConnected && hv !== state.selected) {
    const r = rectOf(hv);
    hoverBox.style.display = 'block';
    hoverBox.style.left = r.x + 'px'; hoverBox.style.top = r.y + 'px';
    hoverBox.style.width = r.w + 'px'; hoverBox.style.height = r.h + 'px';
  } else hoverBox.style.display = 'none';
}

/* ---------------------------------------------------------------------
   Frame events: click/hover/drag/dblclick
--------------------------------------------------------------------- */
let drag = null; // {mode:'move'|'resize', ...}

function bindFrameEvents() {
  const doc = idoc();

  doc.addEventListener('mousedown', e => {
    if (state.editing) {
      const t = pickTarget(e.target);
      if (t && state.selected && (t === state.selected || state.selected.contains(t))) return; // continue editing
      stopTextEdit();
    }
    const t = pickTarget(e.target);
    select(t);
    if (t && e.button === 0) startMove(e, t);
  });

  doc.addEventListener('dblclick', e => {
    const t = pickTarget(e.target);
    if (!t) return;
    // find the closest text-bearing element
    let target = t;
    select(target);
    startTextEdit(target);
    e.preventDefault();
  });

  doc.addEventListener('mousemove', e => {
    if (drag) { onDragMove(e); return; }
    if (state.editing) return;
    state.hovered = pickTarget(e.target);
    updateOverlay();
  });
  doc.addEventListener('mouseup', endDrag);
  doc.addEventListener('mouseleave', () => { state.hovered = null; updateOverlay(); });

  doc.addEventListener('input', () => { scheduleThumbs(); debouncedCommit(); updateOverlay(); });
  doc.addEventListener('keydown', e => {
    if (state.editing) {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === 's') {
        e.preventDefault();
        flushServerSave({ renderAfter: true });
        return;
      }
      if (e.key === 'Escape') { e.preventDefault(); stopTextEdit(); }
      return;
    }
    handleEditorKeys(e);
  });

  // keep floating toolbar state in sync with the caret/selection
  doc.addEventListener('selectionchange', () => {
    if (!state.editing) return;
    saveRange();
    syncBar();
  });

  // observe mutations for thumbs — but ignore editor-internal DOM noise
  // (slide activation, contenteditable) so switching slides never re-renders.
  // Real content changes mark their slide's thumb dirty for the next render.
  const normClass = s => (s || '').split(/\s+/).filter(c => c && !c.startsWith('__amp')).sort().join(' ');
  new MutationObserver(muts => {
    let real = false;
    for (const m of muts) {
      if (m.type === 'attributes') {
        if (m.attributeName === 'class') {
          if (normClass(m.oldValue) === normClass(m.target.getAttribute('class'))) continue;
        } else if (m.attributeName === 'contenteditable' || m.attributeName === 'spellcheck' ||
                   m.attributeName === 'data-slide-index' || m.attributeName === 'data-slide-title') continue;
      }
      real = true;
      const s = slideOf(m.target);
      if (s) dirtyThumbSlides.add(s);
    }
    if (real) scheduleThumbs();
  }).observe(doc.body, { subtree: true, childList: true, attributes: true, characterData: true, attributeOldValue: true });
}

/* -------- move -------- */
function parseTranslate(el) {
  const t = el.style.transform || '';
  const m = t.match(/translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/);
  return m ? { x: +m[1], y: +m[2], rest: t.replace(m[0], '').trim() } : { x: 0, y: 0, rest: t };
}
function setTranslate(el, x, y, rest) {
  const tr = `translate(${Math.round(x)}px, ${Math.round(y)}px)` + (rest ? ' ' + rest : '');
  el.style.transform = (x === 0 && y === 0 && !rest) ? '' : tr;
}

function startMove(e, el) {
  const base = parseTranslate(el);
  drag = {
    mode: 'move', el, sx: e.clientX, sy: e.clientY,
    baseX: base.x, baseY: base.y, rest: base.rest, moved: false,
    startRect: el.getBoundingClientRect(),
  };
}

function onDragMove(e) {
  const d = drag;
  let dx = e.clientX - d.sx, dy = e.clientY - d.sy; // iframe coords, already unscaled
  if (!d.moved && Math.hypot(dx, dy) < 3) return;
  d.moved = true;

  if (d.mode === 'move') {
    // snapping to slide center
    const gv = $('#guideV'), gh = $('#guideH');
    const cx = d.startRect.left + dx + d.startRect.width / 2;
    const cy = d.startRect.top + dy + d.startRect.height / 2;
    const SNAP = 8;
    gv.style.display = gh.style.display = 'none';
    if (Math.abs(cx - SLIDE_W / 2) < SNAP) {
      dx += SLIDE_W / 2 - cx;
      gv.style.left = (SLIDE_W / 2) * state.scale + 'px'; gv.style.display = 'block';
    }
    if (Math.abs(cy - SLIDE_H / 2) < SNAP) {
      dy += SLIDE_H / 2 - cy;
      gh.style.top = (SLIDE_H / 2) * state.scale + 'px'; gh.style.display = 'block';
    }
    setTranslate(d.el, d.baseX + dx, d.baseY + dy, d.rest);
  } else {
    resizeApply(d, dx, dy);
  }
  updateOverlay();
}

function endDrag() {
  if (!drag) return;
  const moved = drag.moved;
  drag = null;
  $('#guideV').style.display = $('#guideH').style.display = 'none';
  if (moved) { commit('Transformed element'); renderInspector(); }
}

/* -------- resize (handles live in parent overlay) -------- */
$$('.handle', selBox).forEach(h => {
  h.addEventListener('mousedown', e => {
    e.preventDefault(); e.stopPropagation();
    const el = state.selected;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const cs = idoc().defaultView.getComputedStyle(el);
    const base = parseTranslate(el);
    drag = {
      mode: 'resize', el, h: h.dataset.h,
      sx: e.clientX, sy: e.clientY, parent: true,
      w0: r.width, h0: r.height,
      baseX: base.x, baseY: base.y, rest: base.rest,
      moved: false,
      abs: cs.position === 'absolute' || cs.position === 'fixed',
      left0: parseFloat(cs.left) || 0, top0: parseFloat(cs.top) || 0,
    };
    const onMove = ev => {
      const dx = (ev.clientX - drag.sx) / state.scale;
      const dy = (ev.clientY - drag.sy) / state.scale;
      if (!drag.moved && Math.hypot(dx, dy) < 2) return;
      drag.moved = true;
      resizeApply(drag, dx, dy);
      updateOverlay();
    };
    const onUp = () => {
      ownerDocument.removeEventListener('mousemove', onMove);
      ownerDocument.removeEventListener('mouseup', onUp);
      endDrag();
    };
    ownerDocument.addEventListener('mousemove', onMove);
    ownerDocument.addEventListener('mouseup', onUp);
  });
});

function resizeApply(d, dx, dy) {
  const el = d.el, h = d.h;
  let w = d.w0, ht = d.h0, shiftX = 0, shiftY = 0;
  if (h.includes('e')) w = d.w0 + dx;
  if (h.includes('s')) ht = d.h0 + dy;
  if (h.includes('w')) { w = d.w0 - dx; shiftX = dx; }
  if (h.includes('n')) { ht = d.h0 - dy; shiftY = dy; }
  w = Math.max(12, w); ht = Math.max(12, ht);
  if (h === 'n' || h === 's') { el.style.height = Math.round(ht) + 'px'; }
  else if (h === 'e' || h === 'w') { el.style.width = Math.round(w) + 'px'; }
  else { el.style.width = Math.round(w) + 'px'; el.style.height = Math.round(ht) + 'px'; }
  if (shiftX || shiftY) {
    if (d.abs) {
      if (shiftX) el.style.left = Math.round(d.left0 + shiftX) + 'px';
      if (shiftY) el.style.top = Math.round(d.top0 + shiftY) + 'px';
    } else {
      setTranslate(el, d.baseX + shiftX, d.baseY + shiftY, d.rest);
    }
  }
}

/* ---------------------------------------------------------------------
   Text editing
--------------------------------------------------------------------- */
function startTextEdit(el) {
  if (!el) return;
  state.editing = true;
  el.setAttribute('contenteditable', 'true');
  el.setAttribute('spellcheck', 'false');
  try { idoc().execCommand('styleWithCSS', false, true); } catch (_) {}
  el.focus();
  // place caret at click point handled by browser
  showTextBar();
  updateOverlay();
}

function stopTextEdit() {
  if (!state.editing) return;
  state.editing = false;
  $$('[contenteditable]', ibody()).forEach(el => {
    el.removeAttribute('contenteditable');
    el.removeAttribute('spellcheck');
  });
  closeMenus();
  $('#textBar').style.display = 'none';
  savedRange = null;
  commit('Text edited');
  updateOverlay();
}

function showTextBar() {
  const el = state.selected;
  if (!el) return;
  const bar = $('#textBar');
  bar.style.display = 'flex';
  const r = rectOf(el);
  const stageR = stage.getBoundingClientRect();
  const areaR = $('#canvasArea').getBoundingClientRect();
  let x = stageR.left - areaR.left + r.x;
  let y = stageR.top - areaR.top + r.y - bar.offsetHeight - 12;
  if (y < 8) y = stageR.top - areaR.top + r.y + r.h + 12; // flip below when no room above
  x = Math.max(8, Math.min(x, areaR.width - bar.offsetWidth - 8));
  y = Math.max(8, Math.min(y, areaR.height - bar.offsetHeight - 8));
  bar.style.left = x + 'px';
  bar.style.top = y + 'px';
  syncBar();
}

/* =====================================================================
   Floating text toolbar: options, menus, state sync
   ===================================================================== */
const tbar = $('#textBar');
const sizeInput = $('#tbSize');
let savedRange = null;

const FONT_SIZES = [12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 88, 96, 120, 144, 180];
const FONT_FAMILIES = [
  ['Default', ''],
  ['Inter', 'Inter, ui-sans-serif, system-ui, sans-serif'],
  ['Arial', 'Arial, Helvetica, sans-serif'],
  ['Helvetica', 'Helvetica, Arial, sans-serif'],
  ['Georgia', 'Georgia, "Times New Roman", serif'],
  ['Times', '"Times New Roman", Times, serif'],
  ['Verdana', 'Verdana, Geneva, sans-serif'],
  ['Trebuchet', '"Trebuchet MS", sans-serif'],
  ['Courier', '"Courier New", Courier, monospace'],
  ['Menlo', 'Menlo, Consolas, monospace'],
];
const LINE_HEIGHTS = ['0.9', '1', '1.15', '1.3', '1.5', '1.75', '2'];
const LETTER_SPACINGS = ['-0.05em', '-0.02em', '0', '0.02em', '0.05em', '0.1em'];

function iSel() { return idoc().getSelection(); }
function editRoot() { return state.editing && state.selected && state.selected.isConnected ? state.selected : null; }
function anchorEl() {
  const s = iSel();
  const n = s.rangeCount ? s.anchorNode : null;
  let el = n ? (n.nodeType === 3 ? n.parentElement : n) : null;
  const root = editRoot();
  if (!el || (root && !root.contains(el))) el = root;
  return el;
}

function buildMenus() {
  $('#sizeMenu').innerHTML = FONT_SIZES.map(s => `<button class="tmi" data-size="${s}">${s}px</button>`).join('');
  $('#familyMenu').innerHTML = FONT_FAMILIES.map(([n, v]) =>
    `<button class="tmi" data-family="${v.replace(/"/g, '&quot;')}"${v ? ` style="font-family:${v.replace(/"/g, '&quot;')}"` : ''}><span class="mi-label">${n === 'Default' ? tr('slide_presentation_editor_default', 'Default') : n}</span></button>`).join('');
  $('#spacingMenu').innerHTML =
    `<div class="tmh">${tr('slide_presentation_editor_line_height', 'Line height')}</div>` + LINE_HEIGHTS.map(v => `<button class="tmi" data-lh="${v}">${v}</button>`).join('') +
    `<div class="tmh">${tr('slide_presentation_editor_letter_spacing', 'Letter spacing')}</div>` + LETTER_SPACINGS.map(v => `<button class="tmi" data-ls="${v}">${v === '0' ? tr('slide_presentation_editor_normal_zero', 'normal (0)') : v}</button>`).join('');
}
buildMenus();

/* -------- dropdown menus -------- */
function closeMenus() { $$('#textBar .tmenu.open').forEach(m => m.classList.remove('open')); }
$$('#textBar [data-menu]').forEach(btn => {
  btn.addEventListener('mousedown', e => e.preventDefault()); // keep iframe selection
  btn.addEventListener('click', () => {
    const m = $('#' + btn.dataset.menu);
    const was = m.classList.contains('open');
    closeMenus();
    if (!was) m.classList.add('open');
  });
});
$$('#textBar .tmenu').forEach(m => m.addEventListener('mousedown', e => e.preventDefault()));
root.addEventListener('mousedown', e => { if (!tbar.contains(e.target)) closeMenus(); });

/* -------- selection save / restore (for focus-stealing pickers) -------- */
function saveRange() {
  const s = iSel();
  if (s.rangeCount) { try { savedRange = s.getRangeAt(0).cloneRange(); } catch (_) {} }
}
function restoreRange() {
  if (!savedRange) return;
  try {
    frame.contentWindow.focus();
    const s = iSel();
    s.removeAllRanges();
    s.addRange(savedRange);
  } catch (_) {}
}

/* -------- inline format commands (B/I/U/S/sup/sub/clear) -------- */
function execFmt(cmd, val = null) {
  idoc().execCommand(cmd, false, val);
  commit('Format');
  syncBar();
}
$$('#textBar [data-cmd]').forEach(b => {
  b.addEventListener('mousedown', e => e.preventDefault());
  b.addEventListener('click', () => execFmt(b.dataset.cmd));
});

/* -------- font size -------- */
function applyFontSize(px) {
  px = parseInt(px, 10);
  if (!px || px < 1 || px > 900) return;
  const doc = idoc(), root = editRoot();
  if (!root) return;
  let sel = iSel();
  // focus may be in the size input — fall back to the last iframe selection
  if ((!sel.rangeCount || sel.isCollapsed) && savedRange && !savedRange.collapsed) { restoreRange(); sel = iSel(); }
  if (sel.rangeCount && !sel.isCollapsed && root.contains(sel.anchorNode)) {
    // execCommand marker trick, then normalize the produced spans to exact px
    doc.execCommand('styleWithCSS', false, true);
    doc.execCommand('fontSize', false, '7');
    root.querySelectorAll('span').forEach(s => {
      if (/xxx-large/i.test(s.style.fontSize || '')) s.style.fontSize = px + 'px';
    });
    root.querySelectorAll('font[size="7"]').forEach(f => {
      const sp = doc.createElement('span');
      sp.style.fontSize = px + 'px';
      sp.innerHTML = f.innerHTML;
      f.replaceWith(sp);
    });
  } else {
    root.style.fontSize = px + 'px';
  }
  commit('Font size');
  syncBar();
  updateOverlay();
}
sizeInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); applyFontSize(sizeInput.value); sizeInput.blur(); }
  if (e.key === 'Escape') { sizeInput.blur(); }
});
sizeInput.addEventListener('change', () => applyFontSize(sizeInput.value));
$('#sizeMenu').addEventListener('click', e => {
  const b = e.target.closest('[data-size]');
  if (b) { applyFontSize(+b.dataset.size); closeMenus(); }
});

/* -------- font family -------- */
function applyFontFamily(fam) {
  const doc = idoc(), root = editRoot();
  if (!root) return;
  let sel = iSel();
  if ((!sel.rangeCount || sel.isCollapsed) && savedRange && !savedRange.collapsed) { restoreRange(); sel = iSel(); }
  if (fam && sel.rangeCount && !sel.isCollapsed && root.contains(sel.anchorNode)) {
    doc.execCommand('styleWithCSS', false, true);
    doc.execCommand('fontName', false, fam);
  } else {
    root.style.fontFamily = fam; // '' clears back to inherited
  }
  commit('Font family');
  syncBar();
}
$('#familyMenu').addEventListener('click', e => {
  const b = e.target.closest('[data-family]');
  if (b) { applyFontFamily(b.dataset.family); closeMenus(); }
});

/* -------- paragraph alignment -------- */
$('#alignMenu').addEventListener('click', e => {
  const b = e.target.closest('[data-align]');
  if (!b) return;
  const cmd = { left: 'justifyLeft', center: 'justifyCenter', right: 'justifyRight', justify: 'justifyFull' }[b.dataset.align];
  if (cmd) execFmt(cmd);
  closeMenus();
});

/* -------- vertical alignment of the text box -------- */
$('#valignMenu').addEventListener('click', e => {
  const b = e.target.closest('[data-valign]');
  const root = editRoot();
  if (!b || !root) return;
  root.style.display = 'flex';
  root.style.flexDirection = 'column';
  root.style.justifyContent = { top: 'flex-start', middle: 'center', bottom: 'flex-end' }[b.dataset.valign];
  commit('Vertical align');
  closeMenus(); syncBar(); updateOverlay();
});

/* -------- line & letter spacing -------- */
$('#spacingMenu').addEventListener('click', e => {
  const lh = e.target.closest('[data-lh]');
  const ls = e.target.closest('[data-ls]');
  const root = editRoot();
  if (!root) return;
  if (lh) root.style.lineHeight = lh.dataset.lh;
  if (ls) root.style.letterSpacing = ls.dataset.ls === '0' ? '' : ls.dataset.ls;
  if (lh || ls) { commit('Spacing'); updateOverlay(); }
  closeMenus();
});

/* -------- lists -------- */
$('#listMenu').addEventListener('click', e => {
  const b = e.target.closest('[data-list]');
  if (!b) return;
  execFmt(b.dataset.list === 'ul' ? 'insertUnorderedList' : 'insertOrderedList');
  closeMenus();
});

/* -------- layers (DOM order) -------- */
$('#layerMenu').addEventListener('click', e => {
  const b = e.target.closest('[data-layer]');
  const el = state.selected;
  if (!b || !el || !el.parentElement) return;
  const p = el.parentElement;
  if (b.dataset.layer === 'front') p.appendChild(el);
  else if (b.dataset.layer === 'back') p.insertBefore(el, p.firstElementChild);
  else if (b.dataset.layer === 'fwd' && el.nextElementSibling) el.nextElementSibling.after(el);
  else if (b.dataset.layer === 'bwd' && el.previousElementSibling) p.insertBefore(el, el.previousElementSibling);
  commit('Layer order');
  closeMenus(); updateOverlay();
});

/* -------- text & highlight colors -------- */
function bindColorPicker(btnId, inputId, cmd) {
  const btn = $('#' + btnId), inp = $('#' + inputId);
  btn.addEventListener('mousedown', e => { e.preventDefault(); saveRange(); });
  btn.addEventListener('click', () => {
    const el = anchorEl();
    if (el) {
      const cs = idoc().defaultView.getComputedStyle(el);
      inp.value = cmd === 'foreColor' ? rgbToHex(cs.color) : '#ffff00';
    }
    inp.click();
  });
  inp.addEventListener('input', () => {
    restoreRange();
    idoc().execCommand(cmd, false, inp.value);
    syncBar();
  });
  inp.addEventListener('change', () => commit('Color'));
}
bindColorPicker('tbForeBtn', 'tbFore', 'foreColor');
bindColorPicker('tbHiliteBtn', 'tbHilite', 'hiliteColor');

/* -------- toolbar state sync -------- */
function syncBar() {
  if (!state.editing || !state.loaded) return;
  const doc = idoc();
  const qs = c => { try { return !!doc.queryCommandState(c); } catch (_) { return false; } };
  $$('#textBar [data-cmd]').forEach(b => {
    if (b.dataset.cmd === 'removeFormat') return;
    b.classList.toggle('on', qs(b.dataset.cmd));
  });
  const el = anchorEl();
  if (!el) return;
  const cs = doc.defaultView.getComputedStyle(el);
  const cur = Math.round(parseFloat(cs.fontSize)) || '';
  if (root.activeElement !== sizeInput) sizeInput.value = cur;
  $$('#sizeMenu .tmi').forEach(b => b.classList.toggle('on', +b.dataset.size === cur));
  const fam = (cs.fontFamily || '').split(',')[0].trim().replace(/^["']|["']$/g, '');
  $('#tbFamilyLbl').textContent = fam || 'Font';
  $('#tbForeChip').style.background = rgbToHex(cs.color);
  const bg = cs.backgroundColor;
  $('#tbHiliteChip').style.background = (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') ? bg : 'transparent';
  const textAlignment = resolvedTextAlignment(cs);
  $$('#alignMenu .tmi').forEach(b => {
    b.classList.toggle('on', b.dataset.align === textAlignment);
  });
  updateAlignmentButtonStates($$('#iAlign button'), textAlignment);
}

/* ---------------------------------------------------------------------
   Keyboard
--------------------------------------------------------------------- */
function handleEditorKeys(e) {
  const meta = e.metaKey || e.ctrlKey;
  if (meta && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (meta && e.key.toLowerCase() === 's') { e.preventDefault(); flushServerSave({ renderAfter: true }); return; }
  if (meta && e.key.toLowerCase() === 'd' && state.selected) { e.preventDefault(); duplicateElement(); return; }
  if (meta && e.key === 'Enter') { e.preventDefault(); requestSharedPresent(); return; }
  if (e.key === 'Escape') {
    if (state.selected) select(null);
    else closeEmbeddedEditor();
    return;
  }
  if ((e.key === 'Delete' || e.key === 'Backspace') && state.selected && !state.editing) {
    e.preventDefault(); deleteElement(); return;
  }
  if (state.selected && !state.editing && ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
    e.preventDefault();
    const step = e.shiftKey ? 10 : 1;
    const t = parseTranslate(state.selected);
    if (e.key === 'ArrowUp') t.y -= step;
    if (e.key === 'ArrowDown') t.y += step;
    if (e.key === 'ArrowLeft') t.x -= step;
    if (e.key === 'ArrowRight') t.x += step;
    setTranslate(state.selected, t.x, t.y, t.rest);
    updateOverlay(); debouncedCommit();
    return;
  }
  if (!state.selected && !state.editing) {
    if (e.key === 'ArrowDown' || e.key === 'PageDown') { e.preventDefault(); setActive(state.active + 1); }
    if (e.key === 'ArrowUp' || e.key === 'PageUp') { e.preventDefault(); setActive(state.active - 1); }
  }
}
function trapModalFocus(e, modal) {
  if (e.key !== 'Tab') return false;
  const controls = $$('button:not(:disabled), textarea, input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])', modal);
  if (!controls.length) return false;
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (e.shiftKey && root.activeElement === first) {
    e.preventDefault(); last.focus(); return true;
  }
  if (!e.shiftKey && root.activeElement === last) {
    e.preventDefault(); first.focus(); return true;
  }
  return false;
}
root.addEventListener('keydown', e => {
  if (!state.loaded) return;
  if ($('#codeModal').classList.contains('open')) {
    if (trapModalFocus(e, $('#codeModal'))) return;
    if (e.key === 'Escape') closeCode();
    return;
  }
  if ($('#tplModal').classList.contains('open')) {
    if (trapModalFocus(e, $('#tplModal'))) return;
    if (e.key === 'Escape') closeTemplateModal();
    return;
  }
  const tag = root.activeElement && root.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  handleEditorKeys(e);
});

function deleteElement() {
  const el = state.selected;
  if (!el) return;
  select(null);
  el.remove();
  commit('Element deleted');
  renderThumbs();
}

function duplicateElement() {
  const el = state.selected;
  if (!el) return;
  const copy = el.cloneNode(true);
  const t = parseTranslate(copy);
  setTranslate(copy, t.x + 24, t.y + 24, t.rest);
  el.after(copy);
  select(copy);
  commit('Element duplicated');
}

/* ---------------------------------------------------------------------
   Undo / redo (snapshot-based)
--------------------------------------------------------------------- */
function snapshot() {
  return { body: ibody().innerHTML, css: deckStyleEl().textContent, active: state.active };
}
function restore(s) {
  select(null);
  stopTextEdit();
  deckStyleEl().textContent = s.css;
  ibody().innerHTML = s.body;
  state.active = Math.min(s.active, slides().length - 1);
  setActive(state.active);
  renderThumbs();
  renderInspector();
}
function pushUndo() {
  state.undo.push(snapshot());
  if (state.undo.length > 120) state.undo.shift();
  state.redo = [];
  updateUndoButtons();
}
function commit(label) {
  const top = state.undo[state.undo.length - 1];
  const now = snapshot();
  if (top && top.body === now.body && top.css === now.css) return;
  state.undo.push(now);
  if (state.undo.length > 120) state.undo.shift();
  state.redo = [];
  updateUndoButtons();
  scheduleThumbs();
  markServerDirty();
}
let commitTimer;
function debouncedCommit() { clearTimeout(commitTimer); commitTimer = setTimeout(() => commit(), 600); }

function undo() {
  if (state.undo.length < 2) return;
  state.redo.push(state.undo.pop());
  restore(state.undo[state.undo.length - 1]);
  updateUndoButtons();
  markServerDirty();
}
function redo() {
  if (!state.redo.length) return;
  const s = state.redo.pop();
  state.undo.push(s);
  restore(s);
  updateUndoButtons();
  markServerDirty();
}
function updateUndoButtons() {
  $('#btnUndo').disabled = state.undo.length < 2;
  $('#btnRedo').disabled = !state.redo.length;
}
$('#btnUndo').addEventListener('click', undo);
$('#btnRedo').addEventListener('click', redo);

/* ---------------------------------------------------------------------
   Inspector
--------------------------------------------------------------------- */
$$('.tab').forEach(t => t.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  state.tab = t.dataset.tab;
  renderInspector();
}));

function rgbToHex(rgb) {
  const m = (rgb || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return /^#/.test(rgb) ? rgb : '#000000';
  return '#' + [m[1], m[2], m[3]].map(v => (+v).toString(16).padStart(2, '0')).join('');
}

function field(label, inner) {
  return `<div class="field"><label>${label}</label>${inner}</div>`;
}

function resolvedTextAlignment(computedStyle) {
  if (computedStyle.textAlign === 'start') return computedStyle.direction === 'rtl' ? 'right' : 'left';
  if (computedStyle.textAlign === 'end') return computedStyle.direction === 'rtl' ? 'left' : 'right';
  return computedStyle.textAlign;
}

function updateAlignmentButtonStates(buttons, alignment) {
  buttons.forEach(button => {
    const isSelected = button.dataset.v === alignment;
    button.classList.toggle('on', isSelected);
    button.setAttribute('aria-pressed', String(isSelected));
  });
}

function escapeHtmlAttribute(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderInspector() {
  const body = $('#inspBody');
  if (state.tab === 'element') renderElementTab(body);
  else if (state.tab === 'slide') renderSlideTab(body);
  else renderThemeTab(body);
}

function renderElementTab(body) {
  const el = state.selected;
  if (!el || !el.isConnected) {
    body.innerHTML = `<div class="empty-hint">${formatShortcutTranslation(
      'slide_presentation_editor_hint_html',
      'Double-click text to edit · <kbd>{keyboard_shortcut_ctrl}Z</kbd> undo · <kbd>{keyboard_shortcut_ctrl}S</kbd> save',
      primaryShortcutModifier(),
    )}</div>`;
    return;
  }
  const win = idoc().defaultView;
  const cs = win.getComputedStyle(el);
  const textAlignment = resolvedTextAlignment(cs);
  const isAbs = cs.position === 'absolute' || cs.position === 'fixed';
  const r = el.getBoundingClientRect();

  // breadcrumbs (up to slide)
  const chain = [];
  let n = el;
  while (n && !n.classList.contains('slide') && n !== ibody()) { chain.unshift(n); n = n.parentElement; }
  body.innerHTML = `
    <div class="crumbs" id="elementBreadcrumbs"></div>

    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_typography', 'Typography')}</div>
      <div class="row">
        ${field(tr('slide_presentation_editor_size', 'Size'), `<input type="number" id="iFontSize" value="${escapeHtmlAttribute(parseFloat(cs.fontSize) || '')}" step="1">`)}
        ${field(tr('slide_presentation_editor_weight', 'Weight'), `<select id="iFontWeight">${[100,200,300,400,500,600,700,800,900].map(w => `<option ${+cs.fontWeight === w ? 'selected' : ''}>${w}</option>`).join('')}</select>`)}
      </div>
      <div class="row">
        ${field(tr('slide_presentation_editor_line_height', 'Line height'), `<input type="text" id="iLineHeight" value="${escapeHtmlAttribute(el.style.lineHeight || (cs.lineHeight === 'normal' ? '' : (parseFloat(cs.lineHeight) / parseFloat(cs.fontSize)).toFixed(2)))}" placeholder="1.4">`)}
        ${field(tr('slide_presentation_editor_letter_spacing', 'Letter spacing'), `<input type="text" id="iLetterSpacing" value="${escapeHtmlAttribute(el.style.letterSpacing || (cs.letterSpacing === 'normal' ? '' : cs.letterSpacing))}" placeholder="0em">`)}
      </div>
      <div class="row">
        ${field(tr('slide_presentation_editor_color', 'Color'), `<div class="color-field"><input type="color" id="iColor" value="${escapeHtmlAttribute(rgbToHex(cs.color))}"><input type="text" id="iColorTxt" value="${escapeHtmlAttribute(rgbToHex(cs.color))}"></div>`)}
      </div>
      <div class="row">
        <div class="field"><label id="iAlignLabel">${tr('slide_presentation_editor_align', 'Align')}</label>
          <div class="seg" id="iAlign" role="group" aria-labelledby="iAlignLabel">
            <button type="button" data-v="left" aria-label="${escapeHtmlAttribute(tr('slide_presentation_editor_align_left', 'Align left'))}" aria-pressed="${textAlignment === 'left'}" ${textAlignment === 'left' ? 'class="on"' : ''}>${Icons.withSvgAttributes("markdownEditorIcons.alignLeft", { "aria-hidden": "true", "focusable": "false" })}</button>
            <button type="button" data-v="center" aria-label="${escapeHtmlAttribute(tr('slide_presentation_editor_align_center', 'Align center'))}" aria-pressed="${textAlignment === 'center'}" ${textAlignment === 'center' ? 'class="on"' : ''}>${Icons.withSvgAttributes("markdownEditorIcons.alignCenter", { "aria-hidden": "true", "focusable": "false" })}</button>
            <button type="button" data-v="right" aria-label="${escapeHtmlAttribute(tr('slide_presentation_editor_align_right', 'Align right'))}" aria-pressed="${textAlignment === 'right'}" ${textAlignment === 'right' ? 'class="on"' : ''}>${Icons.withSvgAttributes("markdownEditorIcons.alignRight", { "aria-hidden": "true", "focusable": "false" })}</button>
          </div>
        </div>
        ${field(tr('slide_presentation_editor_transform', 'Transform'), `<select id="iTextTransform">${[
          ['none', 'slide_presentation_editor_transform_none', 'None'],
          ['uppercase', 'slide_presentation_editor_transform_uppercase', 'Uppercase'],
          ['lowercase', 'slide_presentation_editor_transform_lowercase', 'Lowercase'],
          ['capitalize', 'slide_presentation_editor_transform_capitalize', 'Capitalize'],
        ].map(([value, key, fallback]) => `<option value="${value}" ${cs.textTransform === value ? 'selected' : ''}>${tr(key, fallback)}</option>`).join('')}</select>`)}
      </div>
    </div>

    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_fill_border', 'Fill & Border')}</div>
      <div class="row">
        ${field(tr('slide_presentation_editor_background', 'Background'), `<div class="color-field"><input type="color" id="iBg" value="${rgbToHex(cs.backgroundColor)}"><input type="text" id="iBgTxt" value="${escapeHtmlAttribute(el.style.background || el.style.backgroundColor || (cs.backgroundColor === 'rgba(0, 0, 0, 0)' ? 'transparent' : rgbToHex(cs.backgroundColor)))}"></div>`)}
      </div>
      <div class="row">
        ${field(tr('slide_presentation_editor_radius', 'Radius'), `<input type="text" id="iRadius" value="${escapeHtmlAttribute(el.style.borderRadius || (cs.borderRadius !== '0px' ? cs.borderRadius : ''))}" placeholder="0px">`)}
        ${field(tr('slide_presentation_editor_opacity', 'Opacity'), `<input type="number" id="iOpacity" value="${escapeHtmlAttribute(cs.opacity)}" min="0" max="1" step="0.05">`)}
      </div>
      <div class="row">
        ${field(tr('slide_presentation_editor_border', 'Border'), `<input type="text" id="iBorder" value="${escapeHtmlAttribute(el.style.border || (cs.borderStyle !== 'none' ? `${cs.borderWidth} ${cs.borderStyle} ${rgbToHex(cs.borderColor)}` : ''))}" placeholder="1px solid #000">`)}
      </div>
      <div class="row">
        ${field(tr('slide_presentation_editor_shadow', 'Shadow'), `<select id="iShadow">
          <option value="">${tr('slide_presentation_editor_none', 'None')}</option>
          <option value="0 2px 8px rgba(0,0,0,.12)">${tr('slide_presentation_editor_subtle', 'Subtle')}</option>
          <option value="0 8px 30px rgba(0,0,0,.18)">${tr('slide_presentation_editor_medium', 'Medium')}</option>
          <option value="0 20px 60px rgba(0,0,0,.3)">${tr('slide_presentation_editor_strong', 'Strong')}</option>
        </select>`)}
      </div>
    </div>

    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_layout', 'Layout')}</div>
      <div class="row">
        ${field(tr('slide_presentation_editor_width', 'Width'), `<input type="text" id="iW" value="${escapeHtmlAttribute(el.style.width || Math.round(r.width) + 'px')}">`)}
        ${field(tr('slide_presentation_editor_height', 'Height'), `<input type="text" id="iH" value="${escapeHtmlAttribute(el.style.height || Math.round(r.height) + 'px')}">`)}
      </div>
      ${isAbs ? `<div class="row">
        ${field(tr('slide_presentation_editor_left', 'Left'), `<input type="text" id="iLeft" value="${escapeHtmlAttribute(el.style.left || cs.left)}">`)}
        ${field(tr('slide_presentation_editor_top', 'Top'), `<input type="text" id="iTop" value="${escapeHtmlAttribute(el.style.top || cs.top)}">`)}
      </div>` : ''}
      <div class="row">
        ${field(tr('slide_presentation_editor_padding', 'Padding'), `<input type="text" id="iPad" value="${escapeHtmlAttribute(el.style.padding || (cs.padding !== '0px' ? cs.padding : ''))}" placeholder="0px">`)}
        ${field(tr('slide_presentation_editor_z_index', 'Z-index'), `<input type="number" id="iZ" value="${escapeHtmlAttribute(cs.zIndex === 'auto' ? '' : cs.zIndex)}" placeholder="auto">`)}
      </div>
      <div class="row">
        ${field(tr('slide_presentation_editor_rotate', 'Rotate (°)'), `<input type="number" id="iRot" value="${escapeHtmlAttribute((() => { const m = (el.style.transform || '').match(/rotate\((-?[\d.]+)deg\)/); return m ? m[1] : 0; })())}" step="1">`)}
      </div>
    </div>

    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_actions', 'Actions')}</div>
      <div class="btn-row">
        <button class="mini-btn" id="actDup">${Icons.resolveIcon("copy")}${tr('slide_presentation_editor_duplicate', 'Duplicate')}</button>
        <button class="mini-btn danger" id="actDel">${Icons.resolveIcon("trash")}${tr('common_delete', 'Delete')}</button>
      </div>
    </div>
  `;

  // Element IDs and class names originate in the editable deck. Build the
  // breadcrumb with DOM properties so that metadata remains text even when it
  // contains characters that would be meaningful to an HTML parser.
  const breadcrumbs = $('#elementBreadcrumbs', body);
  chain.forEach((element, index) => {
    const button = ownerDocument.createElement('button');
    button.type = 'button';
    button.className = `crumb ${element === el ? 'cur' : ''}`;
    button.dataset.ci = String(index);
    button.textContent = describe(element);
    breadcrumbs.appendChild(button);
  });

  // breadcrumb clicks
  $$('.crumb', body).forEach(c => c.addEventListener('click', () => select(chain[+c.dataset.ci])));

  const bindStyle = (id, prop, transform = v => v) => {
    const inp = $('#' + id, body);
    if (!inp) return;
    inp.addEventListener('change', () => {
      el.style[prop] = transform(inp.value);
      updateOverlay(); commit();
    });
  };
  bindStyle('iFontSize', 'fontSize', v => v ? v + 'px' : '');
  bindStyle('iFontWeight', 'fontWeight');
  bindStyle('iLineHeight', 'lineHeight');
  bindStyle('iLetterSpacing', 'letterSpacing');
  bindStyle('iTextTransform', 'textTransform');
  bindStyle('iRadius', 'borderRadius');
  bindStyle('iOpacity', 'opacity');
  bindStyle('iBorder', 'border');
  bindStyle('iShadow', 'boxShadow');
  bindStyle('iW', 'width');
  bindStyle('iH', 'height');
  bindStyle('iLeft', 'left');
  bindStyle('iTop', 'top');
  bindStyle('iPad', 'padding');
  bindStyle('iZ', 'zIndex');

  $('#iColor', body).addEventListener('input', e => { el.style.color = e.target.value; $('#iColorTxt', body).value = e.target.value; debouncedCommit(); });
  $('#iColorTxt', body).addEventListener('change', e => { el.style.color = e.target.value; commit(); });
  $('#iBg', body).addEventListener('input', e => { el.style.background = e.target.value; $('#iBgTxt', body).value = e.target.value; scheduleThumbs(); debouncedCommit(); });
  $('#iBgTxt', body).addEventListener('change', e => { el.style.background = e.target.value; commit(); });
  $('#iRot', body).addEventListener('change', e => {
    const t = parseTranslate(el);
    const deg = +e.target.value || 0;
    const rest = (t.rest || '').replace(/rotate\((-?[\d.]+)deg\)/, '').trim();
    setTranslate(el, t.x, t.y, (deg ? `rotate(${deg}deg)` : '') + (rest ? ' ' + rest : ''));
    updateOverlay(); commit();
  });
  const alignmentButtons = $$('#iAlign button', body);
  alignmentButtons.forEach(button => button.addEventListener('click', () => {
    el.style.textAlign = button.dataset.v;
    updateAlignmentButtonStates(alignmentButtons, button.dataset.v);
    commit();
  }));
  $('#actDup', body).addEventListener('click', duplicateElement);
  $('#actDel', body).addEventListener('click', deleteElement);
}

function renderSlideTab(body) {
  const s = activeSlide();
  if (!s) { body.innerHTML = `<div class="empty-hint">${tr('slide_presentation_editor_no_slide', 'No slide.')}</div>`; return; }
  const cs = idoc().defaultView.getComputedStyle(s);
  body.innerHTML = `
    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_slide', 'Slide')} ${state.active + 1}</div>
      <div class="row">${field(tr('slide_presentation_editor_title', 'Title'), `<input type="text" id="sTitle" value="${escapeHtmlAttribute(s.getAttribute('data-slide-title') || '')}">`)}</div>
      <div class="row">
        ${field(tr('slide_presentation_editor_background', 'Background'), `<div class="color-field"><input type="color" id="sBg" value="${escapeHtmlAttribute(rgbToHex(cs.backgroundColor))}"><input type="text" id="sBgTxt" value="${escapeHtmlAttribute(s.style.background || '')}" placeholder="${tr('slide_presentation_editor_inherit_css', 'Inherit from CSS')}"></div>`)}
      </div>
      <div class="row">${field(tr('slide_presentation_editor_classes', 'Classes'), `<input type="text" id="sClass" value="${escapeHtmlAttribute([...s.classList].filter(c => !c.startsWith('__amp')).join(' '))}" spellcheck="false">`)}</div>
    </div>
    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_arrange', 'Arrange')}</div>
      <div class="btn-row" style="margin-bottom:8px">
        <button class="mini-btn" id="sUp">↑ ${tr('slide_presentation_editor_move_up', 'Move up')}</button>
        <button class="mini-btn" id="sDown">↓ ${tr('slide_presentation_editor_move_down', 'Move down')}</button>
      </div>
      <div class="btn-row">
        <button class="mini-btn" id="sDup">${tr('slide_presentation_editor_duplicate', 'Duplicate')}</button>
        <button class="mini-btn danger" id="sDel">${tr('common_delete', 'Delete')}</button>
      </div>
    </div>
  `;
  $('#sTitle', body).addEventListener('change', e => { s.setAttribute('data-slide-title', e.target.value); commit(); });
  $('#sBg', body).addEventListener('input', e => { s.style.background = e.target.value; $('#sBgTxt', body).value = e.target.value; scheduleThumbs(); debouncedCommit(); });
  $('#sBgTxt', body).addEventListener('change', e => { s.style.background = e.target.value; commit(); });
  $('#sClass', body).addEventListener('change', e => {
    const classes = new Set(String(e.target.value || '').split(/\s+/).filter(Boolean));
    classes.add('slide');
    classes.add('__amp-active');
    s.className = [...classes].join(' ');
    e.target.value = [...classes].filter(name => !name.startsWith('__amp')).join(' ');
    commit(); renderThumbs();
  });
  $('#sUp', body).addEventListener('click', () => moveSlide(state.active, Math.max(0, state.active - 1)));
  $('#sDown', body).addEventListener('click', () => moveSlide(state.active, Math.min(slides().length, state.active + 2)));
  $('#sDup', body).addEventListener('click', () => duplicateSlide(state.active));
  $('#sDel', body).addEventListener('click', () => deleteSlide(state.active));
}

function parseRootVars(css) {
  const m = css.match(/:root\s*{([^}]*)}/);
  if (!m) return [];
  const vars = [];
  m[1].replace(/--([\w-]+)\s*:\s*([^;]+);/g, (_, name, val) => { vars.push({ name: '--' + name, value: val.trim() }); return _; });
  return vars;
}
function looksLikeColor(v) {
  return /^#([0-9a-f]{3,8})$/i.test(v) || /^(rgb|hsl)a?\(/i.test(v);
}
function colorToHex(v, doc) {
  // resolve any css color to hex via a temp element
  const probe = doc.createElement('div');
  probe.style.color = v;
  doc.body.appendChild(probe);
  const hex = rgbToHex(doc.defaultView.getComputedStyle(probe).color);
  probe.remove();
  return hex;
}

function renderThemeTab(body) {
  const css = deckStyleEl().textContent;
  const vars = parseRootVars(css);
  if (!vars.length) {
    body.innerHTML = `<div class="empty-hint">${tr('slide_presentation_editor_no_css_variables', 'No :root CSS variables found in this deck.')}</div>`;
    return;
  }
  body.innerHTML = `
    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_design_tokens', 'Design tokens')}</div>
      <div id="varList"></div>
    </div>
    <div class="insp-section">
      <div class="insp-title">${tr('slide_presentation_editor_deck_css', 'Deck CSS')}</div>
      <button class="mini-btn" id="editCssBtn" style="width:100%">${tr('slide_presentation_editor_open_stylesheet', 'Open full stylesheet…')}</button>
    </div>
  `;
  const list = $('#varList', body);
  vars.forEach(v => {
    const row = ownerDocument.createElement('div');
    row.className = 'var-row';
    const isColor = looksLikeColor(v.value);

    // CSS custom-property data comes from the deck. Assign it through DOM
    // properties instead of reparsing it as parent-document HTML.
    const name = ownerDocument.createElement('span');
    name.className = 'name';
    name.title = v.name;
    name.textContent = v.name;
    row.appendChild(name);

    let colorInp = null;
    if (isColor) {
      colorInp = ownerDocument.createElement('input');
      colorInp.type = 'color';
      colorInp.value = colorToHex(v.value, idoc());
      row.appendChild(colorInp);
    }

    const textInp = ownerDocument.createElement('input');
    textInp.type = 'text';
    textInp.value = v.value;
    row.appendChild(textInp);

    const setVar = val => {
      const cssNow = deckStyleEl().textContent;
      const re = new RegExp(`(${v.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*:\\s*)[^;]+;`);
      deckStyleEl().textContent = cssNow.replace(re, `$1${val};`);
      scheduleThumbs(); debouncedCommit();
    };
    if (colorInp) colorInp.addEventListener('input', e => { textInp.value = e.target.value; setVar(e.target.value); });
    textInp.addEventListener('change', e => { if (colorInp && looksLikeColor(e.target.value)) colorInp.value = colorToHex(e.target.value, idoc()); setVar(e.target.value); });
    list.appendChild(row);
  });
  $('#editCssBtn', body).addEventListener('click', () => openCode('css'));
}

/* ---------------------------------------------------------------------
   Code editor modal
--------------------------------------------------------------------- */
let codeScope = 'slide'; // 'slide' | 'deck' | 'css'
let modalReturnFocus = null;
function openCode(scope) {
  if ($('#codeModal').hidden) modalReturnFocus = root.activeElement;
  codeScope = scope || 'slide';
  $('#scopeSlide').classList.toggle('on', codeScope === 'slide');
  $('#scopeDeck').classList.toggle('on', codeScope !== 'slide');
  const area = $('#codeArea');
  if (codeScope === 'slide') {
    const s = activeSlide();
    area.value = s ? cleanSlideHTML(s) : '';
  } else if (codeScope === 'css') {
    area.value = deckStyleEl().textContent;
    $('#scopeDeck').classList.add('on'); $('#scopeSlide').classList.remove('on');
  } else {
    area.value = serializeDeck();
  }
  $('#codeModal').classList.add('open');
  $('#codeModal').removeAttribute('hidden');
  $('#codeModal').setAttribute('aria-hidden', 'false');
  $('#app').inert = true;
  requestAnimationFrame(() => area.focus());
}
function closeCode() {
  $('#codeModal').classList.remove('open');
  $('#codeModal').setAttribute('aria-hidden', 'true');
  $('#codeModal').setAttribute('hidden', '');
  $('#app').inert = false;
  modalReturnFocus?.focus?.();
  modalReturnFocus = null;
}

$('#btnCode').addEventListener('click', () => openCode('slide'));
$('#scopeSlide').addEventListener('click', () => openCode('slide'));
$('#scopeDeck').addEventListener('click', () => openCode('deck'));
$('#codeClose').addEventListener('click', closeCode);
$('#codeCancel').addEventListener('click', closeCode);
$('#codeModal').addEventListener('mousedown', e => { if (e.target === e.currentTarget) closeCode(); });

$('#codeApply').addEventListener('click', () => {
  const val = $('#codeArea').value;
  if (codeScope === 'slide') {
    const s = activeSlide();
    if (!s) return;
    const tmp = idoc().createElement('div');
    tmp.innerHTML = val;
    const el = tmp.querySelector('.slide') || tmp.firstElementChild;
    if (!el) { toast(tr('slide_presentation_editor_html_parse_failed', 'Could not parse that HTML.')); return; }
    el.classList.add('slide');
    el.classList.add('__amp-active');
    if (!el.getAttribute('data-slide-title')) {
      el.setAttribute('data-slide-title', `${tr('slide_presentation_editor_slide', 'Slide')} ${state.active + 1}`);
    }
    s.replaceWith(el);
    select(null);
  } else if (codeScope === 'css') {
    deckStyleEl().textContent = withSlideContractCss(val);
  } else {
    const parsed = parseDeckHTML(val);
    if (!parsed) { toast(tr('slide_presentation_editor_no_slide_sections', 'No .slide sections found.')); return; }
    if (parsed.slidesHTML.length > MAX_SLIDES) {
      toast(tr('slide_presentation_editor_slide_limit', 'A presentation can contain at most 50 slides.'));
      return;
    }
    deckStyleEl().textContent = parsed.css;
    ibody().innerHTML = parsed.slidesHTML.join('\n');
    state.active = Math.min(state.active, slides().length - 1);
    setActive(state.active);
  }
  closeCode();
  renderThumbs();
  renderInspector();
  commit('Code edited');
  toast(tr('slide_presentation_editor_changes_applied', 'Changes applied'));
});

/* ---------------------------------------------------------------------
   Serialization & download
--------------------------------------------------------------------- */
function cleanSlideHTML(slideEl) {
  const c = slideEl.cloneNode(true);
  c.classList.remove('__amp-active');
  if (!c.className.trim()) c.removeAttribute('class');
  $$('[contenteditable]', c).forEach(x => { x.removeAttribute('contenteditable'); x.removeAttribute('spellcheck'); });
  return c.outerHTML;
}

function serializeDeck() {
  const title = $('#deckTitle').value || 'Presentation';
  const css = deckStyleEl().textContent;
  const slidesHTML = slides().map(cleanSlideHTML).join('\n\n');
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title.replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))}</title>
  <style>
${css}
  </style>
</head>
<body>
${slidesHTML}
</body>
</html>`;
}

function markServerDirty() {
  if (!EMBEDDED || !server.loaded || server.conflict) return;
  server.dirty = true;
  server.editVersion += 1;
  setSaveState(tr('slide_presentation_editor_unsaved', 'Unsaved changes'));
  clearTimeout(server.renderTimer);
  clearTimeout(server.saveTimer);
  server.saveTimer = setTimeout(() => flushServerSave(), 900);
}

function scheduleServerRender(delay = 5000) {
  if (!EMBEDDED || server.conflict || server.dirty || server.saveInFlight || server.renderInFlight) return;
  clearTimeout(server.renderTimer);
  server.renderTimer = setTimeout(() => requestServerRender(), delay);
}

async function requestServerRender() {
  if (!EMBEDDED || server.conflict) return false;
  const sessionId = server.sessionId;
  let drainIterations = 0;
  clearTimeout(server.renderTimer);

  // A caller awaiting this function (notably Present and Export) must not be
  // released merely because *a* render completed. Keep draining renders until
  // the derivative revision matches the newest saved canvas revision. This
  // also joins an older render started by the autosave path without mistaking
  // that older promise for proof that the current revision is ready.
  while (sessionId === server.sessionId && !server.conflict) {
    drainIterations += 1;
    if (drainIterations > MAX_SERVER_RENDER_DRAIN_ITERATIONS) return false;
    if (server.dirty || server.saveInFlight) return false;
    if (server.renderRevision >= server.revision) {
      setSaveState(tr('slide_presentation_editor_saved', 'Saved'), 'saved');
      return true;
    }

    if (server.renderInFlight) {
      const joinedRevision = server.renderRequestedRevision;
      const revisionBeforeJoin = server.renderRevision;
      const rendered = await server.renderInFlight.catch(() => false);
      if (sessionId !== server.sessionId || server.conflict) return false;
      if (
        rendered
        && joinedRevision > revisionBeforeJoin
        && server.renderRevision <= revisionBeforeJoin
      ) {
        return false;
      }
      if (rendered && server.renderRevision < joinedRevision) return false;
      if (!rendered && !server.dirty && !server.saveInFlight && server.renderRevision < server.revision) {
        // The joined request may have lost a revision race while this caller
        // was waiting. Only retry when there is demonstrably a newer target;
        // a failure for the current target remains a real export blocker.
        if (server.revision > joinedRevision) continue;
        return false;
      }
      continue;
    }

    const requestedRevision = server.revision;
    const revisionBeforeRender = server.renderRevision;
    server.renderRequestedRevision = requestedRevision;
    setSaveState(tr('slide_presentation_editor_rendering', 'Updating preview…'));

    let renderTask;
    renderTask = (async () => {
      try {
        const result = await bridgeRequest('omlorix-presentation-editor-render', {
          expected_revision: requestedRevision,
        });
        if (sessionId !== server.sessionId) return false;
        const completedRevision = Number(result.render_revision) || 0;
        if (completedRevision < requestedRevision) {
          throw new Error('Presentation render completed below its requested revision.');
        }
        server.renderRevision = Math.max(server.renderRevision, completedRevision);
        return true;
      } catch (error) {
        if (sessionId !== server.sessionId) return false;
        // A newer local edit/save superseding this exact revision is a normal
        // race. Its save loop will request the newer render, so avoid showing
        // a transient failure toast for the obsolete request.
        if (error.status === 409 && (server.dirty || server.revision > requestedRevision)) {
          return false;
        }
        const message = tr('slide_presentation_editor_render_failed', 'Preview update failed');
        setSaveState(message, 'error');
        toast(message);
        return false;
      } finally {
        if (sessionId === server.sessionId && server.renderInFlight === renderTask) {
          server.renderInFlight = null;
          server.renderRequestedRevision = 0;
        }
      }
    })();
    server.renderInFlight = renderTask;

    const rendered = await renderTask;
    if (sessionId !== server.sessionId || server.conflict) return false;
    if (!rendered) {
      if (server.dirty || server.saveInFlight) return false;
      if (server.revision > requestedRevision) continue;
      return false;
    }
    if (server.renderRevision < requestedRevision) return false;
    if (server.renderRevision <= revisionBeforeRender) return false;
    // Loop once more: a save may have advanced server.revision while this
    // render was running, in which case the new revision must also render.
  }
  return false;
}

async function flushServerSave({ renderAfter = false } = {}) {
  if (!EMBEDDED || !server.loaded || server.conflict) return false;
  const sessionId = server.sessionId;
  clearTimeout(server.saveTimer);

  // Drain every edit that existed before or arrived while an earlier save was
  // running. Timer-only follow-up made Export race ahead of that second save;
  // this loop gives callers a single promise for the complete stable revision.
  while (sessionId === server.sessionId && !server.conflict) {
    if (server.saveInFlight) {
      const saved = await server.saveInFlight.catch(() => false);
      if (!saved || sessionId !== server.sessionId || server.conflict) return false;
      continue;
    }

    if (server.dirty) {
      stopTextEdit();
      const savedEditVersion = server.editVersion;
      setSaveState(tr('slide_presentation_editor_saving', 'Saving…'));

      let saveTask;
      saveTask = (async () => {
        try {
          const result = await bridgeRequest('omlorix-presentation-editor-save', {
            html: serializeDeck(),
            title: ($('#deckTitle').value || tr('slide_presentation_default_title', 'Presentation')).trim(),
            expected_revision: server.revision,
          });
          if (sessionId !== server.sessionId) return false;
          server.revision = Number(result.canvas_revision) || server.revision + 1;
          server.renderRevision = Number(result.render_revision) || server.renderRevision;
          server.dirty = server.editVersion !== savedEditVersion;
          setSaveState(
            server.dirty
              ? tr('slide_presentation_editor_unsaved', 'Unsaved changes')
              : tr('slide_presentation_editor_saved', 'Saved'),
            server.dirty ? '' : 'saved'
          );
          return true;
        } catch (error) {
          if (sessionId !== server.sessionId) return false;
          if (error.status === 409) server.conflict = true;
          const message = error.status === 409
            ? tr('slide_presentation_editor_conflict', 'This presentation changed elsewhere. Reload it before saving.')
            : tr('slide_presentation_editor_save_failed', 'Failed to save presentation changes.');
          setSaveState(message, 'error');
          toast(message);
          return false;
        } finally {
          if (sessionId === server.sessionId && server.saveInFlight === saveTask) {
            server.saveInFlight = null;
          }
        }
      })();
      server.saveInFlight = saveTask;

      const saved = await saveTask;
      if (!saved || sessionId !== server.sessionId || server.conflict) return false;
      continue;
    }

    if (!renderAfter) {
      scheduleServerRender();
      return true;
    }

    const rendered = await requestServerRender();
    if (sessionId !== server.sessionId || server.conflict) return false;
    // Editing can remain interactive during a slow render. If another edit
    // landed in that window, save and render it before Present/Export proceeds.
    if (server.dirty || server.saveInFlight) continue;
    if (!rendered) return false;
    if (server.renderRevision < server.revision) continue;
    return true;
  }
  return false;
}

/* ---------------------------------------------------------------------
   Shared presentation and export actions
--------------------------------------------------------------------- */
async function requestSharedPresent() {
  if (!state.loaded || typeof editorController?.present !== 'function') return;
  stopTextEdit();
  // Persist the editable source first, then let the parent open the slideshow
  // immediately while the expensive derivative render continues. Waiting for
  // rendering here kept the editor covering the loading UI, making Preview
  // appear unresponsive even though a purpose-built slideshow loader exists.
  const saved = await flushServerSave();
  if (!saved) return;
  clearTimeout(server.renderTimer);
  const renderPromise = requestServerRender();
  await editorController.present({
    slideIndex: state.active,
    renderPromise,
  });
}

async function requestSharedExport() {
  if (!state.loaded || typeof editorController?.export !== 'function') return;
  stopTextEdit();
  const saved = await flushServerSave({ renderAfter: true });
  if (!saved) return;
  await editorController.export({ format: $('#editorExportFormat').value });
}

$('#btnPresent').addEventListener('click', () => requestSharedPresent().catch(error => toast(error?.message || String(error))));
$('#btnExport').addEventListener('click', () => requestSharedExport().catch(error => toast(error?.message || String(error))));

/* ---------------------------------------------------------------------
   File open / landing / misc wiring
--------------------------------------------------------------------- */
const fileInput = $('#fileInput');
function openFilePicker() { fileInput.value = ''; fileInput.click(); }
fileInput.addEventListener('change', () => {
  const f = fileInput.files[0];
  if (!f) return;
  f.text().then(t => loadDeck(t, f.name));
});
$('#btnOpen').addEventListener('click', openFilePicker);
$('#dropZone').addEventListener('click', openFilePicker);
$('#btnNewDeck').addEventListener('click', () => loadDeck(BLANK_DECK, null));

['dragover', 'dragenter'].forEach(ev => root.addEventListener(ev, e => {
  e.preventDefault();
  $('#dropZone').classList.add('over');
}));
['dragleave', 'drop'].forEach(ev => root.addEventListener(ev, e => {
  e.preventDefault();
  $('#dropZone').classList.remove('over');
}));
root.addEventListener('drop', e => {
  const f = [...(e.dataTransfer?.files || [])].find(f => /\.html?$/i.test(f.name) || f.type === 'text/html');
  if (f) f.text().then(t => loadDeck(t, f.name));
});

function closeTemplateModal() {
  $('#tplModal').classList.remove('open');
  $('#tplModal').setAttribute('aria-hidden', 'true');
  $('#tplModal').setAttribute('hidden', '');
  $('#app').inert = false;
  modalReturnFocus?.focus?.();
  modalReturnFocus = null;
}
$('#addSlideBtn').addEventListener('click', () => {
  modalReturnFocus = root.activeElement;
  $('#tplModal').removeAttribute('hidden');
  $('#tplModal').classList.add('open');
  $('#tplModal').setAttribute('aria-hidden', 'false');
  $('#app').inert = true;
  requestAnimationFrame(() => $('#tplClose').focus());
});
$('#tplClose').addEventListener('click', closeTemplateModal);
$('#tplModal').addEventListener('mousedown', e => { if (e.target === e.currentTarget) closeTemplateModal(); });
$$('.tpl').forEach(t => t.addEventListener('click', () => insertTemplate(t.dataset.tpl)));

$('#deckTitle').addEventListener('input', markServerDirty);
$('#deckTitle').addEventListener('change', () => {
  if (!EMBEDDED) toast('Title updated');
});

function setButtonText(button, label) {
  if (!button) return;
  [...button.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).forEach(node => node.remove());
  button.appendChild(ownerDocument.createTextNode(` ${label}`));
}

function localizeEmbeddedChrome() {
  if (!EMBEDDED) return;
  try {
    host.lang = ownerDocument.documentElement.lang || 'en';
    host.dir = ownerDocument.documentElement.dir || 'ltr';
  } catch (_) {}
  const labelControl = (selector, key, fallback) => {
    const control = $(selector);
    if (!control) return;
    const label = tr(key, fallback);
    control.title = label;
    control.setAttribute('aria-label', label);
  };
  const setMenuLabel = (selector, key, fallback) => {
    const label = $(selector);
    if (label) label.textContent = tr(key, fallback);
  };

  $('#btnCloseEditor').title = tr('slide_presentation_editor_close', 'Close');
  $('#btnCloseEditor').setAttribute('aria-label', tr('slide_presentation_editor_close', 'Close'));
  setButtonText($('#btnCode'), tr('slide_presentation_editor_code', 'Code'));
  setButtonText($('#btnPresent'), tr('slide_presentation_present', 'Present'));
  setButtonText($('#btnExport'), tr('files_preview_download', 'Download'));
  $('#btnExport').title = tr('files_preview_download', 'Download');
  $('#editorExportFormat').setAttribute('aria-label', tr('slide_presentation_download_format_aria', 'Download format'));
  const imagesOption = $('#editorExportFormat option[value="slides_zip"]');
  if (imagesOption) imagesOption.textContent = tr('pdf_export_images', 'Images');
  const deckTitleLabel = tr('slide_presentation_editor_deck_title', 'Presentation title');
  $('#deckTitle').title = deckTitleLabel;
  $('#deckTitle').setAttribute('aria-label', deckTitleLabel);
  const shortcutModifier = primaryShortcutModifier();
  const localizedControls = [
    ['#btnUndo', 'slide_presentation_editor_undo', 'Undo', `${shortcutModifier}Z`],
    ['#btnRedo', 'slide_presentation_editor_redo', 'Redo', `Shift+${shortcutModifier}Z`],
    ['#zoomOut', 'slide_presentation_editor_zoom_out', 'Zoom out'],
    ['#zoomLabel', 'slide_presentation_editor_zoom_fit', 'Fit to screen'],
    ['#zoomIn', 'slide_presentation_editor_zoom_in', 'Zoom in'],
    ['#codeClose', 'common_close', 'Close'],
    ['#tplClose', 'common_close', 'Close'],
  ];
  localizedControls.forEach(([selector, key, fallback, shortcut]) => {
    const control = $(selector);
    const label = tr(key, fallback);
    control.title = shortcut ? `${label} (${shortcut})` : label;
    control.setAttribute('aria-label', label);
  });
  $('#saveState').title = formatShortcutTranslation(
    'slide_presentation_editor_save_shortcut',
    'Save and update preview ({keyboard_shortcut_ctrl}S)',
    shortcutModifier,
  );
  $('#slidesPanel .panel-head span:first-child').textContent = tr('slide_presentation_editor_slides', 'Slides');
  setButtonText($('#addSlideBtn'), tr('slide_presentation_editor_new_slide', 'New slide'));
  const tabs = $$('.tabs .tab');
  if (tabs[0]) tabs[0].textContent = tr('slide_presentation_editor_element', 'Element');
  if (tabs[1]) tabs[1].textContent = tr('slide_presentation_editor_slide', 'Slide');
  if (tabs[2]) tabs[2].textContent = tr('slide_presentation_editor_theme', 'Theme');
  $('#codeModal .modal-head h3').textContent = tr('slide_presentation_editor_html_source', 'HTML source');
  $('#scopeSlide').textContent = tr('slide_presentation_editor_current_slide', 'Current slide');
  $('#scopeDeck').textContent = tr('slide_presentation_editor_whole_deck', 'Whole deck');
  $('#codeCancel').textContent = tr('common_cancel', 'Cancel');
  $('#codeApply').textContent = tr('slide_presentation_editor_apply_changes', 'Apply changes');
  $('#tplModal .modal-head h3').textContent = tr('slide_presentation_editor_new_slide', 'New slide');
  labelControl('#deckFrame', 'slide_presentation_editor_slide_canvas', 'Slide canvas');
  labelControl('#tbSize', 'slide_presentation_editor_font_size', 'Font size (px)');
  labelControl('[data-menu="sizeMenu"]', 'slide_presentation_editor_font_size_presets', 'Font size presets');
  labelControl('#tbFamilyBtn', 'slide_presentation_editor_font_family', 'Font family');
  $('#tbFamilyLbl').textContent = tr('slide_presentation_editor_font', 'Font');
  labelControl('#tbForeBtn', 'markdown_editor_text_color', 'Text color');
  labelControl('#tbHiliteBtn', 'markdown_editor_highlight_color', 'Highlight color');
  [
    ['bold', 'markdown_editor_bold', 'Bold'],
    ['italic', 'markdown_editor_italic', 'Italic'],
    ['underline', 'markdown_editor_underline', 'Underline'],
    ['strikeThrough', 'markdown_editor_strikethrough', 'Strikethrough'],
    ['superscript', 'markdown_editor_superscript', 'Superscript'],
    ['subscript', 'markdown_editor_subscript', 'Subscript'],
    ['removeFormat', 'markdown_editor_clear_formatting', 'Clear formatting'],
  ].forEach(([command, key, fallback]) => labelControl(`[data-cmd="${command}"]`, key, fallback));
  labelControl('[data-menu="alignMenu"]', 'markdown_editor_alignment', 'Text align');
  setMenuLabel('[data-align="left"] .mi-label', 'markdown_editor_align_left', 'Left');
  setMenuLabel('[data-align="center"] .mi-label', 'markdown_editor_align_center', 'Center');
  setMenuLabel('[data-align="right"] .mi-label', 'markdown_editor_align_right', 'Right');
  setMenuLabel('[data-align="justify"] .mi-label', 'markdown_editor_align_justify', 'Justify');
  labelControl('[data-menu="valignMenu"]', 'slide_presentation_editor_vertical_align', 'Vertical align');
  setMenuLabel('[data-valign="top"] .mi-label', 'slide_presentation_editor_top', 'Top');
  setMenuLabel('[data-valign="middle"] .mi-label', 'slide_presentation_editor_middle', 'Middle');
  setMenuLabel('[data-valign="bottom"] .mi-label', 'slide_presentation_editor_bottom', 'Bottom');
  labelControl('[data-menu="spacingMenu"]', 'slide_presentation_editor_spacing', 'Line and letter spacing');
  labelControl('[data-menu="listMenu"]', 'slide_presentation_editor_lists', 'Lists');
  setMenuLabel('[data-list="ul"] .mi-label', 'markdown_editor_slash_bulleted_list', 'Bulleted list');
  setMenuLabel('[data-list="ol"] .mi-label', 'markdown_editor_slash_numbered_list', 'Numbered list');
  labelControl('[data-menu="layerMenu"]', 'slide_presentation_editor_layers', 'Arrange layers');
  setMenuLabel('[data-layer="front"]', 'slide_presentation_editor_bring_front', 'Bring to front');
  setMenuLabel('[data-layer="fwd"]', 'slide_presentation_editor_bring_forward', 'Bring forward');
  setMenuLabel('[data-layer="bwd"]', 'slide_presentation_editor_send_backward', 'Send backward');
  setMenuLabel('[data-layer="back"]', 'slide_presentation_editor_send_back', 'Send to back');
  labelControl('#codeClose', 'slide_presentation_editor_close', 'Close');
  labelControl('#tplClose', 'slide_presentation_editor_close', 'Close');

  const templateLabels = {
    'blank-light': ['slide_presentation_editor_template_blank_light', 'Blank light'],
    'blank-dark': ['slide_presentation_editor_template_blank_dark', 'Blank dark'],
    title: ['slide_presentation_editor_template_title_slide', 'Title slide'],
    'two-col': ['slide_presentation_editor_template_two_columns', 'Two columns'],
    cards: ['slide_presentation_editor_template_three_cards', 'Three cards'],
    duplicate: ['slide_presentation_editor_template_duplicate_current', 'Duplicate current slide'],
  };
  Object.entries(templateLabels).forEach(([template, [key, fallback]]) => {
    const button = $(`.tpl[data-tpl="${template}"]`);
    const translated = tr(key, fallback);
    if (!button) return;
    button.setAttribute('aria-label', translated);
    const visibleLabel = $('.lbl', button);
    if (visibleLabel) visibleLabel.textContent = translated;
    const previewLabel = $('.prev', button);
    if (previewLabel) previewLabel.textContent = translated;
  });

  // These menus are generated in JavaScript and live inside Shadow DOM, so
  // the application's normal data-i18n traversal cannot update them.
  buildMenus();
  if (state.loaded) renderInspector();
}

ownerDocument.addEventListener('i18n:updated', localizeEmbeddedChrome);

async function closeEmbeddedEditor() {
  if (!EMBEDDED) return;
  clearTimeout(server.saveTimer);
  clearTimeout(server.renderTimer);
  let discardedUnsavedChanges = false;
  if (server.dirty) {
    const saved = await flushServerSave();
    if (!saved || server.dirty) {
      const confirmed = typeof window.showWarningConfirm !== 'function'
        || await window.showWarningConfirm({
          title: tr('modal_discard_changes_title', 'Discard changes?'),
          message: tr('modal_discard_changes_desc', 'You have unsaved changes. Are you sure you want to leave without saving?'),
          confirmLabel: tr('modal_discard_btn', 'Discard changes'),
          danger: true,
        });
      if (!confirmed) return;
      discardedUnsavedChanges = true;
      server.dirty = false;
      server.conflict = false;
    }
  }
  // The parent sidebar owns close-time rendering. Hand it the exact saved
  // revision plus any already-running render so it can wait, reconcile, and
  // refresh without depending on editor state that is reset as the overlay
  // closes. This also covers the race where an older revision was rendering
  // while the final save completed.
  editorController?.onClose?.({
    discardedUnsavedChanges,
    sourceChanged: server.revision !== server.openedRevision,
    canvasRevision: server.revision,
    renderRevision: server.renderRevision,
    renderRequestedRevision: server.renderRequestedRevision,
    renderPromise: server.renderInFlight,
  });
}

$('#btnCloseEditor').addEventListener('click', closeEmbeddedEditor);

// click empty canvas → deselect
$('#canvasScroller').addEventListener('mousedown', e => {
  if (e.target === e.currentTarget) { stopTextEdit(); select(null); }
});

// keep overlay in sync while things settle (fonts/images loading)
setInterval(() => { if (state.loaded && (state.selected || state.hovered)) updateOverlay(); }, 500);

renderInspector();
localizeEmbeddedChrome();

/**
 * Reset transient state before mounting another presentation in the same native
 * editor instance. The component remains attached to avoid rebinding hundreds
 * of controls every time the user opens it.
 */
function resetNativeEditorState() {
  frameLoadId += 1;
  frame.onload = null;
  server.sessionId += 1;
  clearTimeout(server.saveTimer);
  clearTimeout(server.renderTimer);
  clearTimeout(commitTimer);
  server.revision = 0;
  server.openedRevision = 0;
  server.renderRevision = 0;
  server.dirty = false;
  server.editVersion = 0;
  server.loaded = false;
  server.saveInFlight = null;
  server.renderInFlight = null;
  server.renderRequestedRevision = 0;
  server.saveTimer = null;
  server.renderTimer = null;
  server.conflict = false;
  pendingServerPayload = null;
  state.loaded = false;
  state.active = 0;
  state.selected = null;
  state.hovered = null;
  state.editing = false;
  state.undo = [];
  state.redo = [];
  frame.srcdoc = '';
  $$('.modal-back').forEach(modal => {
    modal.classList.remove('open');
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
  });
  $('#app').inert = false;
  $('#landing').classList.add('hidden');
  $('#app').classList.add('hidden');
}

function openNativeEditor(options = {}) {
  resetNativeEditorState();
  localizeEmbeddedChrome();
  const exportFormat = String(options.exportFormat || 'pptx');
  $('#editorExportFormat').value = ['pptx', 'pdf', 'slides_zip'].includes(exportFormat) ? exportFormat : 'pptx';
  editorController = {
    save: options.save,
    render: options.render,
    present: options.present,
    export: options.export,
    onReady: options.onReady,
    onClose: options.onClose,
  };
  setSaveState(tr('slide_presentation_editor_loading', 'Opening presentation editor…'));
  try {
    loadEmbeddedDeck(options.payload || {});
  } catch (error) {
    const message = error?.message || tr(
      'slide_presentation_editor_load_failed',
      'Failed to open the presentation editor.'
    );
    showEmbeddedLoadFailure(message);
    editorController?.onReady?.();
  }
}

function cancelNativeEditor() {
  frameLoadId += 1;
  frame.onload = null;
  server.sessionId += 1;
  clearTimeout(server.saveTimer);
  clearTimeout(server.renderTimer);
  server.loaded = false;
  server.dirty = false;
  server.saveInFlight = null;
  server.renderInFlight = null;
  server.renderRequestedRevision = 0;
  editorController = null;
  stopTextEdit();
  select(null);
  frame.srcdoc = '';
}

window.slidePresentationNativeEditor = Object.freeze({
  open: openNativeEditor,
  requestClose: closeEmbeddedEditor,
  cancel: cancelNativeEditor,
  focus() {
    $('#deckTitle')?.focus();
  },
  getRoot() {
    return root;
  },
});
})();
