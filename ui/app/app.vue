<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>

<style>
:root {
  color-scheme: light;
  --ink: #1a1f1c;
  --mute: #5c6b63;
  --ok: #1f6f4a;
  --bad: #8b2e2e;
  --paper: #f4f3ef;
  --line: #d7d5cf;
  --accent: #46514a;
  --accent-strong: #28302b;
  --accent-soft: #eceeea;
  --panel: rgba(255, 255, 255, 0.55);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  background: #f4f3ef;
  color: var(--ink);
}

.app-frame[data-module="portfolio"] {
  --ink: #17231e;
  --mute: #5a6a62;
  --paper: #f4f1e7;
  --line: #cdd5cc;
  --accent: #2b6751;
  --accent-strong: #174634;
  --accent-soft: #e4eee7;
  --panel: rgba(253, 252, 247, 0.78);
}

.app-frame[data-module="tracker"] {
  --ink: #17233a;
  --mute: #5b677c;
  --paper: #eff3f9;
  --line: #cad3e2;
  --accent: #4167a6;
  --accent-strong: #25487f;
  --accent-soft: #e2eaf6;
  --panel: rgba(250, 252, 255, 0.8);
}

.app-frame[data-module="neutral"] {
  --paper: #f4f3ef;
}

.module-canvas {
  min-height: calc(100vh - 5rem);
  background:
    radial-gradient(ellipse 70% 50% at 8% 0%, var(--accent-soft) 0%, transparent 58%),
    linear-gradient(145deg, var(--paper) 0%, #faf9f6 100%);
}

.shell {
  width: 100%;
  max-width: 100rem;
  margin: 0 auto;
  padding: clamp(1.5rem, 4vw, 3.5rem) clamp(1rem, 3vw, 2.5rem) 4rem;
}

.tag { margin: 0.5rem 0 0; color: var(--mute); }
.nav {
  margin: 0.75rem 0 0;
  display: flex;
  gap: 0.75rem;
  align-items: center;
}
.nav a { color: var(--accent); }
.badge {
  display: inline-block;
  padding: 0.1rem 0.45rem;
  border: 1px solid var(--bad);
  color: var(--bad);
  font-size: 0.85rem;
}
.note, .ok, .bad, .mute { margin: 1rem 0; }
.ok { color: var(--ok); }
.bad { color: var(--bad); }
.mute { color: var(--mute); font-size: 0.95rem; }
.warn { color: #8a5a20; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }

.module-hero {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2rem;
  align-items: end;
  margin-bottom: 2rem;
  padding: clamp(1.4rem, 3vw, 2.5rem);
  overflow: hidden;
  border: 1px solid var(--line);
  background: var(--panel);
  box-shadow: 0 18px 45px rgba(24, 35, 30, 0.07);
}

.app-frame[data-module="portfolio"] .module-hero {
  border-left: 0.45rem solid var(--accent);
}

.app-frame[data-module="tracker"] .module-hero {
  border-top: 0.45rem solid var(--accent);
  border-radius: 0 0 1.4rem 1.4rem;
}

.module-kicker {
  margin: 0 0 0.55rem;
  color: var(--accent);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.module-title {
  margin: 0;
  color: var(--accent-strong);
  font-size: clamp(2.8rem, 8vw, 6rem);
  font-weight: 700;
  letter-spacing: -0.055em;
  line-height: 0.9;
}

.module-description {
  max-width: 52rem;
  margin: 1rem 0 0;
  color: var(--mute);
  font-size: clamp(1rem, 1.7vw, 1.2rem);
  line-height: 1.5;
}

.module-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(7.5rem, 1fr));
  min-width: min(34rem, 48vw);
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.5);
}

.module-metric {
  min-height: 6rem;
  padding: 0.8rem 0.9rem;
  border-right: 1px solid var(--line);
}

.module-metric:last-child { border-right: 0; }

.module-metric-label {
  display: block;
  margin-bottom: 0.6rem;
  color: var(--mute);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.module-metric strong {
  color: var(--accent-strong);
  font-size: clamp(1.15rem, 2vw, 1.6rem);
  font-weight: 600;
}

.module-guide {
  margin: -0.75rem 0 1.5rem;
  padding: 0 0.25rem;
  color: var(--mute);
  line-height: 1.5;
}

.module-guide a { color: var(--accent); }

.panel {
  margin-top: 1.5rem;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: var(--panel);
  box-shadow: 0 10px 30px rgba(25, 37, 31, 0.035);
}

.panel.warn {
  border-color: #c4a27a;
  background: rgba(255, 244, 230, 0.7);
}

.panel h1 { margin: 0 0 1rem; font-size: 1.15rem; }

.missing {
  margin: 0.5rem 0 0;
  padding-left: 1.1rem;
}
.missing li { margin: 0.35rem 0; }

.lots {
  list-style: none;
  margin: 0;
  padding: 0;
}
.lots li { margin: 0.4rem 0; }
.lot-btn {
  width: 100%;
  text-align: left;
}

.form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: 0.75rem 1rem;
}

label {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.9rem;
}

.wide { grid-column: 1 / -1; }

input, select, button {
  font: inherit;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
}

input:focus-visible,
select:focus-visible,
textarea:focus-visible,
button:focus-visible,
a:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--accent) 38%, transparent);
  outline-offset: 2px;
}

button {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
  cursor: pointer;
}

button.ghost { background: transparent; color: var(--accent); }
button.danger { background: transparent; border-color: var(--bad); color: var(--bad); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
a.ghost {
  display: inline-block;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--accent);
  color: var(--accent);
  text-decoration: none;
  font: inherit;
}
a.ghost.icon-btn { font-size: 0.85rem; padding: 0.25rem 0.5rem; }
a.ghost[aria-disabled="true"] { opacity: 0.45; pointer-events: none; cursor: not-allowed; }
.actions { display: flex; gap: 0.75rem; flex-wrap: wrap; }

.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.rec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.75rem;
}
.icon-btn { font-size: 0.85rem; padding: 0.25rem 0.5rem; }

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}

th, td {
  text-align: left;
  padding: 0.55rem 0.35rem;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}

.sub {
  display: block;
  color: var(--mute);
  font-size: 0.85rem;
}
.lenses { margin: 0.15rem 0 0; }

.row-actions {
  display: flex;
  gap: 0.4rem;
  white-space: nowrap;
}

.ctx {
  padding: 0.85rem 0;
  border-bottom: 1px solid var(--line);
}
.ctx:last-child { border-bottom: 0; }
.ctx h2 {
  margin: 0 0 0.35rem;
  font-size: 1.05rem;
}
.tech { margin: 0.25rem 0; display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: baseline; }
.rationale { margin: 0.35rem 0 0; font-size: 0.95rem; }
.news {
  margin: 0.4rem 0 0;
  padding-left: 1.1rem;
}
.news li { margin: 0.35rem 0; }
.news a { color: var(--accent); }

.pill {
  display: inline-block;
  padding: 0.15rem 0.45rem;
  border: 1px solid var(--line);
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}
.pill[data-action="buy"] { border-color: var(--ok); color: var(--ok); }
.pill[data-action="sell"] { border-color: var(--bad); color: var(--bad); }
.pill[data-action="watch"] { border-color: #8a5a20; color: #8a5a20; }
/* 0019: tracker-only — stop spending attention on a name never owned. */
.pill[data-action="drop"] { border-color: var(--mute); color: var(--mute); }

.info-needs,
.market-read {
  margin: 0.75rem 0 1rem;
  padding: 0.75rem;
  border: 1px dashed var(--line);
}
.info-needs h2,
.market-read h2 { margin: 0 0 0.5rem; font-size: 1rem; }
.info-needs ul { margin: 0; padding-left: 1.1rem; }
.info-needs li { margin: 0.35rem 0; }

.log-panel { border-color: var(--accent); }
.turns {
  list-style: none;
  margin: 0;
  padding: 0;
}
.turn {
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--line);
}
.turn:last-child { border-bottom: 0; }
.turn[data-role="researcher"] { border-left: 3px solid var(--accent); padding-left: 0.75rem; }
.turn[data-role="decider"] { border-left: 3px solid var(--ok); padding-left: 0.75rem; }
.turn-meta { margin: 0 0 0.35rem; font-size: 0.9rem; }
.turn-answers { margin-top: 0.35rem; }
.turn-answers li { margin: 0.2rem 0; font-size: 0.9rem; }

.log {
  margin: 0.5rem 0 1rem;
  padding: 0.75rem;
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid var(--line);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8rem;
  white-space: pre-wrap;
  max-height: 12rem;
  overflow: auto;
}

@media (max-width: 720px) {
  .module-hero {
    grid-template-columns: 1fr;
  }

  .module-metrics {
    min-width: 0;
    width: 100%;
    grid-template-columns: 1fr;
  }

  .module-metric {
    min-height: auto;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .module-metric:last-child { border-bottom: 0; }
  table { display: block; overflow-x: auto; }
}
</style>
