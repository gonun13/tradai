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
  --paper: #f3f0e8;
  --line: #d4cfc3;
  --accent: #2f5d50;
  --panel: rgba(255, 255, 255, 0.55);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  background:
    radial-gradient(ellipse 80% 50% at 10% 0%, #e7efe8 0%, transparent 55%),
    radial-gradient(ellipse 60% 40% at 100% 10%, #efe6d8 0%, transparent 50%),
    var(--paper);
  color: var(--ink);
}

.shell {
  min-height: 100vh;
  width: 100%;
  padding: 2.5rem clamp(1rem, 3vw, 2.5rem);
}

.hero-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.header-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  flex-shrink: 0;
}

.brand {
  margin: 0;
  font-size: clamp(2.5rem, 7vw, 4rem);
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 0.95;
  color: var(--accent);
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

.panel {
  margin-top: 1.5rem;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: var(--panel);
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

.info-needs {
  margin: 0.75rem 0 1rem;
  padding: 0.75rem;
  border: 1px dashed var(--line);
}
.info-needs h2 { margin: 0 0 0.5rem; font-size: 1rem; }
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
  table { display: block; overflow-x: auto; }
}
</style>
