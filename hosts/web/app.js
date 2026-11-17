// The mirror host: Web MIDI (Chrome on the Mac) or the Android shell's bridge (window.CircuitBridge), one renderer for both.

import { NLED, KEEP_MS, TYPE_FULL, TYPE_KEEP, Mirror, keepalive, targets, parse, css, luma } from './proto.js';

const $ = (s) => document.querySelector(s);
const panel = $('#panel');
const statusEl = $('#status');
const devEl = $('#dev');
const portsEl = $('#ports');
const statsEl = $('#stats');

const HOST_BUILD = '2026-11-16 · 7-bit SEQ · CT2';   // stamped in the footer, the ?diag log and the console: tells us which host is running

const mirror = new Mirror();
const params = new URLSearchParams(location.search);
const DIAG = params.has('diag');
let cells = [];                 // the DOM, indexed by LED
let out = null;                 // send(bytes)
let transport = null;
let timer = null, seq = 0, lastRx = 0, lastFull = 0, stale = true, demoMode = false;
let devVer = 0;                 // the device's command base: 1 = "CT " (pre-fix), 2 = "CT2" (7-bit SEQ), 0 = not heard from yet
let sent = 0, received = 0;
let diag = null;                 // ?diag: the measurements (see diagInit)

// ---------------------------------------------------------------- the transport

// Web MIDI (Chrome / Edge): the SysEx port; messages go to the output that matches the input's name.
async function webMidi() {
  if (!navigator.requestMIDIAccess) throw new Error('no Web MIDI in this browser (use Chrome or Edge)');
  const access = await navigator.requestMIDIAccess({ sysex: true });
  let cur = null;                                  // the input in use: its own state says whether the device is still there
  const t = {
    kind: 'webmidi',
    onChange: null,
    ports: () => [...access.inputs.values()].map((i) => ({ id: i.id, name: i.name })),
    open: (id, onMessage) => {
      const input = access.inputs.get(id);
      if (!input) throw new Error('the input went away');
      const o = [...access.outputs.values()].find((x) => x.name === input.name) || [...access.outputs.values()][0];
      if (!o) throw new Error('no MIDI output');
      input.onmidimessage = (ev) => {
        const d = ev.data;
        if (d.length > 2 && d[0] === 0xF0) onMessage(d);
      };
      out = (bytes) => o.send(bytes);
      cur = input;
      return `${input.name} (out: ${o.name})`;
    },
    close: () => {
      for (const i of access.inputs.values()) i.onmidimessage = null;
      cur = null;
      out = null;
    },
  };
  access.onstatechange = () => {
    // a port that goes away while in use: stop pretending the picture is live (nothing else can tell, the device never acks)
    if (cur && cur.state === 'disconnected') {
      cur = null; out = null;
      setStatus('the MIDI device disconnected', 'err');
    }
    if (!out && t.onChange) t.onChange();
  };
  return t;
}

// The Android shell: android.media.midi.MidiManager behind a WebView bridge (Android WebView has no Web MIDI).
function androidBridge() {
  const B = window.CircuitBridge;
  if (!B) return null;
  const b64 = (bin) => btoa(String.fromCharCode(...new Uint8Array(bin)));
  window.__midiMessage = (s) => {
    const bin = atob(s), a = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    handle(a);
  };
  window.__midiPorts = () => { if (!out) listPorts(); };
  window.__midiGone = () => { out = null; setStatus('the MIDI device disconnected', 'err'); };
  return {
    kind: 'android',
    onChange: null,
    ports: () => JSON.parse(B.listPorts() || '[]'),
    open: (id, onMessage) => { B.open(id); out = (bytes) => B.send(b64(bytes)); return B.name(id) || id; },
    close: () => { B.close(); out = null; },
  };
}

// ---------------------------------------------------------------- the panel

function buildPanel(man) {
  panel.innerHTML = '';
  cells = new Array(NLED);
  for (const sec of man.sections) {
    const block = document.createElement('section');
    block.className = 'block ' + sec.id + ' ' + sec.kind;
    const h = document.createElement('h2');
    h.innerHTML = `${sec.title}<small>${sec.hint}</small>`;
    block.appendChild(h);
    const grid = document.createElement('div');
    grid.className = 'grid';
    grid.style.setProperty('--cols', sec.columns);
    for (const i of sec.leds) {
      const led = man.leds[i];
      const c = document.createElement('div');
      c.className = 'cell ' + sec.kind + (led.label.length > 5 ? ' wide' : '');
      c.title = led.name;
      c.dataset.led = i;
      const l = document.createElement('span');
      l.className = 'lbl';
      l.textContent = led.label;
      c.appendChild(l);
      grid.appendChild(c);
      cells[i] = { el: c, lbl: l };
    }
    block.appendChild(grid);
    panel.appendChild(block);
  }
}

function paint() {
  const t0 = performance.now();
  for (const i of mirror.take()) {
    const c = cells[i];
    if (!c) continue;
    const w = mirror.words[i];
    c.el.style.setProperty('--c', css(w));
    c.el.toggleAttribute('data-on', w !== 0);
    c.lbl.style.color = w && luma(w) > 0.55 ? '#0b0d12' : '';
  }
  if (DIAG) diagPaint(performance.now() - t0);
}

// ---------------------------------------------------------------- the stream

function send(bytes) { if (!out) return; try { out(bytes); sent++; } catch (e) { console.error(e); } }

// Until the device has spoken we keepalive on both command bases: a pre-2026-11-16 unit only understands "CT ".  Once it has told us which
// build it runs, talk to that one only.  (A device rebooted by a flash still accepts the old base, so this heals itself if it changes.)
function sendKeep(type, s) { for (const v of targets(devVer)) send(keepalive(type, s, v)); }

// The device's command base is the firmware's own signature: "CT2" = the 7-bit SEQ build, "CT " = the one that froze every 128 frames.
function setDev(v) {
  devVer = v;
  if (!devEl) return;
  devEl.className = 'dev' + (v === 1 ? ' err' : v === 2 ? ' ok' : '');
  devEl.textContent = v === 1 ? 'device firmware "CT" — PRE-FIX (8-bit SEQ): flash SC57.3'
    : v === 2 ? 'device firmware "CT2" · 7-bit SEQ'
    : '';
}

function requestFull() {
  const now = performance.now();
  if (now - lastFull < 400) return;            // a burst of gaps asks once
  lastFull = now;
  sendKeep(TYPE_FULL, seq = (seq + 1) & 0x7F);
}

function handle(data) {
  const m = parse(data);
  if (!m) return;
  if (m.ver !== devVer) setDev(m.ver);
  lastRx = performance.now(); received++;
  if (DIAG) diagMessage(lastRx, m);
  const r = mirror.apply(m);
  if (r.needFull) requestFull();
  paint();
}

function tickStats() {
  // Silence is not death: the firmware sends nothing while no LED word changes and never acks a keepalive, so a static panel
  // (sequencer stopped, nothing touched) is silent for as long as it is static.  Dim only when there is no stream at all: no
  // port open, or a port that has not delivered a single frame yet (stock firmware, or the wrong port).
  const live = (!!out || demoMode) && lastRx > 0;
  const next = !live;
  if (next !== stale) { stale = next; document.body.classList.toggle('stale', stale); }
  if (statsEl) statsEl.textContent = (out || demoMode) ? `${received} msgs${out ? ` · ${sent} sent` : ''} · seq ${mirror.seq === null ? '—' : mirror.seq} · gaps ${mirror.gaps}` : '';
}

function startStream() {
  stopStream();
  requestFull();
  timer = setInterval(() => {
    tickStats();
    if (!out) return;
    sendKeep(TYPE_KEEP, seq = (seq + 1) & 0x7F);   // this is what keeps the device streaming (2 s window)
  }, KEEP_MS);
}

function stopStream() { if (timer) clearInterval(timer); timer = null; }

// Chrome throttles or freezes the timers of a background/occluded tab, so the keepalives stop and the device pauses 2 s later; on
// return the panel would sit on an old picture until some delta happened to arrive.  Coming back, resynchronise at once.
function resync() {
  if (!out && !demoMode) return;
  lastFull = 0;
  requestFull();
}
addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') resync(); });
addEventListener('focus', resync);

// ---------------------------------------------------------------- diagnostics (?diag)
// Turns "laggy and flaky" into numbers.  Every PROBE_MS it asks for a full frame and times the round trip (request -> the device's
// LED tick -> the stock router -> USB -> CoreMIDI -> here), which is the device's real latency; every message is logged with its
// arrival time (clumping, gaps, bytes/s); and it records the paint cost, long tasks and whether the tab was visible and focused,
// because Chrome defers painting (and throttles timers) in a background or occluded window.  Copy log / Download give the JSON.

const PROBE_MS = 1000;

function diagInit() {
  const el = document.createElement('div');
  el.id = 'diag';
  el.innerHTML = '<b>LED mirror diagnostics</b><pre id="diagOut">starting…</pre>'
    + '<div class="diagrow"><button id="diagCopy">Copy log</button><button id="diagSave">Download</button>'
    + '<button id="diagReset">Reset</button></div>';
  document.body.appendChild(el);
  const out = el.querySelector('#diagOut');

  diag = {
    t0: performance.now(), msgs: [], probes: [], pending: null, paintLast: 0, paintMax: 0, paints: 0,
    longs: [], hiddenMs: 0, hiddenAt: null, seqGaps: 0, lastSeq: null, lastT: 0, maxGap: 0, holes: 0, holeSeqs: [],
  };
  diag.visible = document.visibilityState === 'visible' && document.hasFocus();
  if (!diag.visible) diag.hiddenAt = diag.t0;

  const vis = () => {
    const now = performance.now();
    const v = document.visibilityState === 'visible' && document.hasFocus();
    if (diag.visible && !v) diag.hiddenAt = now;
    else if (!diag.visible && v && diag.hiddenAt != null) { diag.hiddenMs += now - diag.hiddenAt; diag.hiddenAt = null; }
    diag.visible = v;
  };
  addEventListener('visibilitychange', vis);
  addEventListener('focus', vis);
  addEventListener('blur', vis);

  if (window.PerformanceObserver) {
    try {
      new PerformanceObserver((l) => { for (const e of l.getEntries()) diag.longs.push(Math.round(e.duration)); })
        .observe({ entryTypes: ['longtask'] });
    } catch { /* no longtask support: the rest still works */ }
  }

  setInterval(() => {
    if (!out) return;
    const p = { t: performance.now(), rtt: null, frame: null, seq: null };
    diag.probes.push(p);
    diag.pending = p;
    sendKeep(TYPE_FULL, seq = (seq + 1) & 0x7F);
  }, PROBE_MS);

  setInterval(() => diagRender(out), 500);

  el.querySelector('#diagReset').onclick = () => {
    diag.t0 = performance.now(); diag.msgs = []; diag.probes = []; diag.pending = null;
    diag.paintMax = 0; diag.paints = 0; diag.longs = []; diag.hiddenMs = 0;
    diag.hiddenAt = diag.visible ? null : diag.t0; diag.seqGaps = 0; diag.lastSeq = null;
    diag.lastT = 0; diag.maxGap = 0; diag.holes = 0; diag.holeSeqs = [];
  };
  el.querySelector('#diagCopy').onclick = async () => {
    const txt = diagLog();
    try { await navigator.clipboard.writeText(txt); out.textContent = 'log copied to the clipboard'; }
    catch (e) { out.textContent = 'clipboard refused (' + e.message + '): use Download'; }
  };
  el.querySelector('#diagSave').onclick = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([diagLog()], { type: 'application/json' }));
    a.download = 'ledmirror-diag-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  };
}

function diagMessage(t, m) {
  if (!diag) return;
  // 127 -> 0 is the normal 7-bit wrap, not a gap.  The real symptom of the old firmware is silence: its SEQ reached 0x80, the
  // stock router dropped every message until the counter wrapped, and the panel froze.  With a full-frame probe every second, a hole
  // over 1.5 s cannot be a static panel — and a hole that runs exactly 127 -> 0 is that old firmware, with no ambiguity.
  const prev = diag.lastSeq;
  if (prev != null && ((prev + 1) & 0x7F) !== m.seq) diag.seqGaps++;
  diag.lastSeq = m.seq;
  if (diag.lastT) {
    const dt = t - diag.lastT;
    if (dt > diag.maxGap) diag.maxGap = dt;
    if (dt > 1500) {
      diag.holes++;
      if (diag.holeSeqs.length < 8) diag.holeSeqs.push(`${prev == null ? '—' : prev}→${m.seq}`);
    }
  }
  diag.lastT = t;
  if (diag.msgs.length < 40000) diag.msgs.push({ t: Math.round(t), type: m.type, seq: m.seq, start: m.start, count: m.count });
  const p = diag.pending;
  if (!p || m.type !== TYPE_FULL) return;
  if (p.rtt == null && m.start === 0) { p.rtt = t - p.t; p.seq = m.seq; return; }
  if (p.rtt != null && p.frame == null && m.seq === ((p.seq + 1) & 0x7F)) p.frame = t - p.t;
}

function diagPaint(ms) {
  if (!diag) return;
  diag.paintLast = ms; diag.paints++;
  if (ms > diag.paintMax) diag.paintMax = ms;
}

function diagRender(out) {
  const now = performance.now();
  const dur = (now - diag.t0) / 1000;
  const win = diag.msgs.filter((x) => now - x.t < 5000);
  const span = Math.max(0.5, Math.min(5, dur));
  const num = (v, d = 1) => (v == null ? '—' : v.toFixed(d));
  const stat = (xs) => ({ last: xs.length ? xs[xs.length - 1] : null, avg: xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null, max: xs.length ? Math.max(...xs) : null });
  const rtt = stat(diag.probes.map((p) => p.rtt).filter((x) => x != null));
  const frm = stat(diag.probes.map((p) => p.frame).filter((x) => x != null));
  const bytes = win.reduce((s, x) => s + 10 + (x.count || 0) * (x.type === TYPE_FULL ? 6 : 7), 0);
  const hidden = diag.hiddenMs + (diag.hiddenAt != null ? now - diag.hiddenAt : 0);
  const longMax = diag.longs.length ? Math.max(...diag.longs) : null;
  out.textContent = [
    `run ${num(dur, 0)} s · host ${HOST_BUILD} · tab ${diag.visible ? 'visible+focused' : 'BACKGROUND/occluded'} · hidden ${num(hidden / 1000, 1)} s`,
    `device ${devVer === 2 ? '"CT2" · 7-bit SEQ' : devVer === 1 ? '"CT" — PRE-FIX (8-bit SEQ), FLASH SC57.3' : 'not heard from yet'}`,
    `rtt   last ${num(rtt.last)}  avg ${num(rtt.avg)}  max ${num(rtt.max)} ms  (${diag.probes.filter((p) => p.rtt != null).length}/${diag.probes.length} answered)`,
    `frame last ${num(frm.last)}  avg ${num(frm.avg)}  max ${num(frm.max)} ms`,
    `msgs  ${num(win.length / span)}/s · ${num(bytes / span, 0)} B/s · seq gaps ${diag.seqGaps} · seq ${mirror.seq == null ? '—' : mirror.seq}`,
    `gap   max ${num(diag.maxGap, 0)} ms · holes over 1.5 s ${diag.holes}${diag.holeSeqs.length ? ' (' + diag.holeSeqs.join(', ') + ')' : ''}  (about 1 s on a still panel = this probe; a 127→0 hole = the old firmware)`,
    `paint last ${num(diag.paintLast, 2)}  max ${num(diag.paintMax, 2)} ms (${diag.paints}) · long tasks ${diag.longs.length}${longMax ? ` (max ${longMax} ms)` : ''}`,
  ].join('\n');
}

function diagLog() {
  const now = performance.now();
  return JSON.stringify({
    kind: 'ledmirror-diag', host: HOST_BUILD, at: new Date().toISOString(), url: location.href, ua: navigator.userAgent,
    durationMs: Math.round(now - diag.t0),
    hiddenMs: Math.round(diag.hiddenMs + (diag.hiddenAt != null ? now - diag.hiddenAt : 0)),
    visible: diag.visible, seqGaps: diag.seqGaps, maxGapMs: Math.round(diag.maxGap), holes: diag.holes,
    paintMax: diag.paintMax, paints: diag.paints,
    longTasks: diag.longs, probes: diag.probes.map((p) => ({ t: Math.round(p.t), rtt: p.rtt, frame: p.frame })),
    msgs: diag.msgs,
  });
}

// ---------------------------------------------------------------- the UI

function setStatus(text, cls = '') { statusEl.textContent = text; statusEl.className = 'status ' + cls; }

function connect(id) {
  try {
    stopStream();
    mirror.reset(); paint();
    const name = transport.open(id, handle);
    lastRx = 0; seq = 0; mirror.gaps = 0; sent = 0; received = 0; lastFull = 0; stale = true;
    setDev(0);
    startStream();
    setStatus(`connected · ${name}`, 'on');
    listPorts(id);
  } catch (e) {
    setStatus(String(e.message || e), 'err');
  }
}

function listPorts(current) {
  if (!transport) return;
  let ports = [];
  try { ports = transport.ports(); } catch (e) { console.error(e); }
  portsEl.innerHTML = '';
  if (!ports.length) {
    setStatus('no MIDI device (is the Circuit Tracks plugged in and switched on?)', 'err');
    return;
  }
  for (const p of ports) {
    const b = document.createElement('button');
    b.textContent = p.name;
    b.className = 'port' + (p.id === current ? ' sel' : '');
    b.onclick = () => connect(p.id);
    portsEl.appendChild(b);
  }
}

async function boot() {
  console.log('LED mirror host', HOST_BUILD);
  const stamp = $('#build');
  if (stamp) stamp.textContent = 'Host ' + HOST_BUILD + '.';
  if (DIAG) diagInit();
  const man = await (await fetch('panel.json')).json();
  buildPanel(man);
  mirror.reset(); paint();
  document.body.classList.add('stale');
  $('#full').onclick = () => requestFull();
  $('#disconnect').onclick = () => {
    stopStream();
    if (transport) transport.close();
    out = null; stale = true;
    document.body.classList.add('stale');
    setStatus('disconnected'); listPorts();
  };

  if (window.CircuitBridge) {
    transport = androidBridge();
  } else if (params.has('demo')) {
    const demo = await import('./demo.js');
    demo.start(handle);
    demoMode = true;
    setStatus('demo (no device)', 'on');
    document.body.classList.remove('stale');
    setInterval(tickStats, 500);
    return;
  } else {
    try {
      transport = await webMidi();
    } catch (e) {
      setStatus('Web MIDI unavailable: ' + (e.message || e), 'err');
      return;
    }
  }
  transport.onChange = () => listPorts();
  setStatus('choose the Circuit Tracks MIDI port');
  listPorts();
}

boot();
