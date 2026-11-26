"""Tests for building and verifying the live Halftone Plate + Stats UI."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_DIR = REPO_ROOT / "swarm_board" / "ui"
SRC_DIR = UI_DIR / "src"
BUILD_PY = UI_DIR / "build.py"
INDEX_HTML = UI_DIR / "index.html"
SIZE_LIMIT = 153_600

FORBIDDEN_FILES = (
    "fixtures.py",
    "fixtures_base.py",
    "fixtures2.py",
    "fixtures_stats.py",
    "run.py",
    "run2.py",
    "check.js",
    "check2.js",
)
FORBIDDEN_PATTERNS = (
    "window.__DATA",
    "/home/",
    "fixtures",
    "check2",
    "check.js",
)
REQUIRED_ENDPOINTS = (
    "/api/flows",
    "/api/active",
    "/api/flow/",
    "/api/stats",
    "/api/output/",
    "/api/file",
    "/api/raw",
    "/api/thread/",
    "/events",
    "EventSource",
)
REQUIRED_MARKERS = (
    "#0b0c0e",
    "htScreen",
    'aria-orientation="vertical"',
    "sw-n",
    "Loading flows",
    "No active or recent multi-agent flows found yet.",
    "data-msgs",
    "<video",
    "<audio",
    "swq",
    'id="activePage"',
    "data-page=active",
    'class="acnt"',
    "Live now",
    "Unsettled",
    'id="aq"',
    "Inspect →",
    "Open chat ↗",
    "qlive",
    'data-filt="live"',
    "Active threads on / off",
    "1 thread",
)


class UiLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        proc = subprocess.run(
            [sys.executable, str(BUILD_PY)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"build.py failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
            )
        cls.build_output = proc.stdout
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_index_exists_and_under_budget(self) -> None:
        self.assertTrue(INDEX_HTML.is_file())
        size = INDEX_HTML.stat().st_size
        self.assertGreater(size, 40_000)
        self.assertLess(size, SIZE_LIMIT, f"index.html size {size} B >= {SIZE_LIMIT} B")

    def test_no_scratch_files_or_forbidden_tokens(self) -> None:
        for fname in FORBIDDEN_FILES:
            self.assertFalse((UI_DIR / fname).exists(), f"Unexpected file: {UI_DIR / fname}")
            self.assertFalse((SRC_DIR / fname).exists(), f"Unexpected src file: {SRC_DIR / fname}")

        files_to_scan = [INDEX_HTML, BUILD_PY, *sorted(SRC_DIR.iterdir())]
        for path in files_to_scan:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for pat in FORBIDDEN_PATTERNS:
                self.assertNotIn(
                    pat,
                    text,
                    f"Forbidden token {pat!r} found in {path.relative_to(REPO_ROOT)}",
                )

    def test_all_live_endpoints_and_ui_markers_present(self) -> None:
        for ep in REQUIRED_ENDPOINTS:
            self.assertIn(ep, self.html, f"Missing live endpoint wiring: {ep}")
        for marker in REQUIRED_MARKERS:
            self.assertIn(marker, self.html, f"Missing expected UI marker: {marker}")
        self.assertIn("Cache hit", self.html)
        self.assertIn("Tool calls", self.html)
        self.assertNotIn("Cache sav.", self.html)
        self.assertNotIn("Cache savings", self.html)

    def test_css_descendant_pseudo_selectors_preserved(self) -> None:
        self.assertIn(".app.stp.stp :is(.fhead,.cols,.filt>.lfold){display:none}", self.html)
        self.assertNotIn(".app.stp.stp:is(", self.html)
        self.assertNotIn(".md:is(", self.html)
        self.assertNotIn(".mapw:is(", self.html)
        self.assertNotIn("STATS_CACHE.clear()", self.html)

    def test_inline_script_passes_node_syntax_check(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is not installed")
        scripts = re.findall(r"<script>([\s\S]*?)</script>", self.html)
        self.assertEqual(len(scripts), 1, "Expected exactly one inline <script> block")
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as tmp:
            tmp.write(scripts[0])
            tmp.flush()
            proc = subprocess.run(
                [node_bin, "--check", tmp.name],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"node --check failed:\n{proc.stderr}\n{proc.stdout}",
            )

    def test_active_page_flow_state_and_single_thread_layout_contracts(self) -> None:
        self.assertIn("x.state==='waiting'", self.html)
        self.assertIn("x.state==='queued'", self.html)
        self.assertIn("f.kind==='thread'", self.html)

    def test_headless_node_vm_smoke_check(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is not installed")
        scripts = re.findall(r"<script>([\s\S]*?)</script>", self.html)
        self.assertEqual(len(scripts), 1)
        smoke_js = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');

function runSim({ active404 = false, initialHash = '' } = {}) {
  const elements = new Map();
  const activeIntervals = new Set();
  let nextIv = 1;

  function makeClassList() {
    const s = new Set();
    return {
      add: (...cs) => cs.forEach((c) => s.add(c)),
      remove: (...cs) => cs.forEach((c) => s.delete(c)),
      toggle: (c, f) => {
        if (f === undefined) {
          if (s.has(c)) { s.delete(c); return false; }
          s.add(c); return true;
        }
        if (f) s.add(c); else s.delete(c);
        return !!f;
      },
      contains: (c) => s.has(c),
    };
  }
  function makeEl(tag, id) {
    const attrs = {};
    const el = {
      tagName: tag.toUpperCase(), id: id || '', dataset: {},
      style: { setProperty: () => {}, removeProperty: () => {} },
      classList: makeClassList(), hidden: false, inert: false,
      clientWidth: 1200, clientHeight: 800, offsetWidth: 200, offsetHeight: 40, scrollTop: 0,
      isConnected: true, children: [], firstChild: null, textContent: '', value: '', _html: '',
      _liveSecEls: [],
      get innerHTML() { return this._html; },
      set innerHTML(v) {
        this._html = String(v);
        if (this._html.startsWith('<svg')) {
          const svg = makeEl('svg'); const sub = makeEl('g');
          svg.querySelector = () => sub; svg.querySelectorAll = () => [];
          this.firstChild = svg;
        }
        for (const subId of ['alist','aq','swlList','swq','schart','mapw','rmain','rbody','findrow','progi','fcnt','fq','keyb','errs']) {
          if (this._html.includes(`id="${subId}"`) && !elements.has(subId)) makeEl('div', subId);
        }
        this._liveSecEls = [];
        for (const m of this._html.matchAll(/data-live-sec="(\d+)"/g)) {
          const sp = makeEl('span'); sp.dataset.liveSec = m[1]; this._liveSecEls.push(sp);
        }
      },
      setAttribute(k, v) { attrs[k] = String(v); },
      getAttribute(k) { return attrs[k] ?? null; },
      removeAttribute(k) { delete attrs[k]; },
      addEventListener() {}, dispatchEvent() {},
      getBoundingClientRect: () => ({ left: 0, top: 0, right: 1200, bottom: 800, width: 1200, height: 800 }),
      querySelector(sel) { return sel === '[data-live-sec]' ? (this._liveSecEls[0] || null) : null; },
      querySelectorAll(sel) { return sel === '[data-live-sec]' ? this._liveSecEls : []; },
      closest: () => null, matches: () => false,
      focus() {}, blur() {}, select() {}, append() {}, remove() {}, normalize() {}, scrollIntoView() {},
      setPointerCapture() {}, insertAdjacentHTML() {},
      getContext: () => ({ font: '', measureText: (s) => ({ width: String(s).length * 55 }) }),
    };
    if (id) elements.set(id, el);
    return el;
  }
  ['app','fbar','filt','statsPage','activePage','fhead','cols','left','mapfr','outs','split','side','vpop'].forEach((id) => makeEl('div', id));
  elements.get('app').dataset.w = 'wide';

  const docListeners = {};
  const fetchedUrls = [];
  let lastES = null;

  const flowsPayload = {
    ok: true, envId: 'env-1', snapshot: '26 Nov 16:40', activeCount: 3, errors: {},
    flows: [
      {
        id: 't3:thr-1', title: 'Standalone Live Thread', project: 'demo', projectRoot: '/tmp/demo',
        harness: 't3', kind: 'thread', startedAt: '2026-11-26T15:00:00Z', updatedAt: '2026-11-26T15:30:00Z',
        goal: 'Fix bug', decision: null, workstreams: [], posts: [],
        outputs: [{ id: 'tr-1', kind: 'transcript', title: 'Transcript', by: 'thr-1', messages: [{ role: 'assistant', text: 'Working' }] }],
        links: [{ kind: 'thread', label: 'Open chat', href: 't3-thread://v2/env-1/thr-1', id: 'thr-1' }],
        agents: [{
          id: 'thr-1', parent: null, name: 'Standalone', label: 'Standalone', task: 'Fix bug', role: 'root',
          lab: 'google', model: 'gemini-4-neon', state: 'running', durS: 45, tokens: { in: 10, cache: 20, out: 5 },
          costUsd: 0.01, errors: [], trace: [['read_file', 2]], last: 'Running tests', result: null,
          links: [{ kind: 'thread', label: 'Open chat', href: 't3-thread://v2/env-1/thr-1', id: 'thr-1' }], outputIds: ['tr-1'],
        }],
      },
      {
        id: 'agy:sw-1', title: 'Demo Swarm', project: 'demo', projectRoot: '/tmp/demo',
        harness: 'agy', kind: 'swarm', startedAt: '2026-11-26T14:00:00Z', updatedAt: '2026-11-26T14:30:00Z',
        goal: 'Build feature', decision: null, workstreams: [{ id: 'ws1', name: 'Core' }],
        posts: [{ id: 1, author: 'coord', at: '14:01', mentions: ['w1'], text: 'Brief @w1 check `src/app.py:10`' }],
        outputs: [
          { id: 'o-md', kind: 'markdown', title: 'Doc', by: 'w1', body: '# Heading\n\n## Sec\nOk\n', size: 30 },
          { id: 'o-json', kind: 'json', title: 'Data', by: 'w1', body: '{"a":1}', size: 7 },
          { id: 'o-tbl', kind: 'table', title: 'Table', by: 'w1', body: '{"rows":[{"name":"a.wav","score":1}]}', size: 37 },
          { id: 'o-vid', kind: 'video', title: 'Clip', by: 'w1', path: 'clip.mp4', size: 1000 },
          { id: 'o-aud', kind: 'audio', title: 'Audio', by: 'w1', path: 'a.wav', size: 500 },
        ],
        links: [],
        agents: [
          { id: 'coord', parent: null, name: 'coordinator', role: 'coordinator', lab: 'google', model: 'gemini-4-neon', state: 'waiting', durS: 30, errors: [], trace: [['swarm', 1]], last: 'Waiting', links: [], outputIds: [] },
          { id: 'w1', parent: 'coord', name: 'worker-1', role: 'worker', workstream: 'ws1', lab: 'anthropic', model: 'claude-opus-5', state: 'killed', durS: 20, errors: [{ kind: 'perm_timeout', count: 1, msg: 'timeout', at: '14:05' }], trace: [['edit_file', 3]], last: 'Stopped', links: [], outputIds: ['o-md'] },
        ],
      },
      {
        id: 't3:tree-1', title: 'Big Tree', project: 'demo', projectRoot: '/tmp/demo',
        harness: 't3', kind: 'tree', startedAt: '2026-11-26T13:00:00Z', updatedAt: '2026-11-26T13:30:00Z',
        goal: null, decision: null, workstreams: [], posts: [], outputs: [], links: [],
        agents: [
          { id: 'r1', parent: null, name: 'root', role: 'root', lab: 'google', state: 'done', durS: 10, errors: [], trace: [], links: [], outputIds: [] },
          { id: 'l1', parent: 'r1', name: 'lead-1', role: 'lead', lab: 'anthropic', state: 'done', durS: 10, errors: [], trace: [], links: [], outputIds: [] },
          { id: 'leaf1', parent: 'l1', name: 'leaf-1', role: 'worker', lab: 'openai', state: 'queued', durS: 5, errors: [], trace: [], links: [], outputIds: [] },
        ],
      },
      {
        id: 'committee:c1', title: 'Committee Run', project: 'demo', projectRoot: '/tmp/demo',
        harness: 'committee', kind: 'committee', startedAt: '2026-11-26T12:00:00Z', updatedAt: '2026-11-26T12:20:00Z',
        goal: 'Ship?', decision: { word: 'PASS', qualifier: '2/2 unanimous', source: 'docs/plan.md:5' }, workstreams: [], posts: [],
        outputs: [
          { id: 'v1', kind: 'verdict', title: 'Seat 1', by: 's1', body: JSON.stringify({ cli: 'claude', model: 'claude-opus-5', duration_seconds: 12.3, response: '## Verdict\n**PASS**' }), size: 90 },
          { id: 'v2', kind: 'verdict', title: 'Seat 2', by: 's2', body: JSON.stringify({ cli: 'codex', model: 'gpt-6', duration_seconds: 15.0, response: '## Verdict\n**PASS**' }), size: 90 },
        ],
        links: [],
        agents: [
          { id: 's1', parent: null, name: 'Seat 1', role: 'reviewer', lab: 'anthropic', model: 'claude-opus-5', state: 'done', durS: 12.3, weight: 0.5, verdict: 'PASS', errors: [], trace: [], links: [], outputIds: ['v1'] },
          { id: 's2', parent: null, name: 'Seat 2', role: 'reviewer', lab: 'openai', model: 'gpt-6', state: 'done', durS: 15.0, weight: 0.5, verdict: 'PASS', errors: [], trace: [], links: [], outputIds: ['v2'] },
        ],
      },
    ],
  };
  const activePayload = {
    ok: true, envId: 'env-1', snapshot: '26 Nov 16:40', liveCount: 3, errors: {},
    threads: [
      {
        id: 'thr-1', flowId: 't3:thr-1', rootThreadId: 'thr-1', parentThreadId: null,
        title: 'Standalone Live Thread', project: 'demo', projectRoot: '/tmp/demo', harness: 't3',
        state: 'running', isLive: true, settled: false, model: 'gemini-4-neon', lab: 'google',
        startedAt: '2026-11-26T15:00:00Z', updatedAt: '2026-11-26T15:30:00Z', durS: 45,
        activeChildren: 0, totalChildren: 0, last: 'Running tests',
        href: 't3-thread://v2/env-1/thr-1', links: [{ kind: 'thread', label: 'Open chat', href: 't3-thread://v2/env-1/thr-1', id: 'thr-1' }],
      },
      {
        id: 'committee:c1', flowId: 'committee:c1', rootThreadId: null, parentThreadId: null,
        title: 'Committee Run', project: 'demo', projectRoot: '/tmp/demo', harness: 'committee',
        state: 'done', isLive: false, settled: false, model: 'claude-opus-5', lab: 'anthropic',
        startedAt: '2026-11-26T12:00:00Z', updatedAt: '2026-11-26T12:20:00Z', durS: 15,
        activeChildren: 0, totalChildren: 2, last: null, href: null, links: [],
      },
    ],
  };

  class MockEventSource {
    constructor(url) { this.url = url; this.readyState = 1; this._ls = {}; lastES = this; }
    addEventListener(t, fn) { (this._ls[t] = this._ls[t] || []).push(fn); }
    emit(t, ev) { (this._ls[t] || []).forEach((fn) => fn(ev || {})); }
    close() { this.readyState = 2; }
  }

  const sandbox = {
    console, setTimeout, clearTimeout,
    setInterval: () => { const id = nextIv++; activeIntervals.add(id); return id; },
    clearInterval: (id) => { activeIntervals.delete(id); },
    URLSearchParams, Map, Set, Array, Object, Number, String, Math, Date, JSON, Promise, RegExp, Error,
    encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN, isFinite,
    innerWidth: 1400, innerHeight: 900, scrollY: 0, scrollX: 0, scrollTo: () => {},
    requestAnimationFrame: () => 1, cancelAnimationFrame: () => {},
    matchMedia: () => ({ matches: false }), ResizeObserver: class { observe() {} },
    CSS: { escape: (s) => String(s) }, NodeFilter: { SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2 },
    EventSource: MockEventSource, performance: { now: () => Date.now() },
    navigator: { onLine: true, clipboard: { writeText: () => Promise.resolve() } },
    localStorage: { getItem: () => null, setItem: () => {} },
    location: { search: '', hash: initialHash, href: 'http://127.0.0.1:18420/' + initialHash },
    history: { pushState: (_s, _t, h) => { sandbox.location.hash = h; } },
    document: {
      documentElement: makeEl('html'), body: makeEl('body'), activeElement: null, fonts: null,
      getElementById: (id) => elements.get(id) || null, createElement: (t) => makeEl(t),
      createDocumentFragment: () => ({ append() {} }), createTreeWalker: () => ({ nextNode: () => false }),
      querySelector: () => null, querySelectorAll: () => [],
      addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
    },
    addEventListener: () => {},
    fetch: async (url) => {
      fetchedUrls.push(url);
      if (url.startsWith('/api/flows')) return { ok: true, status: 200, json: async () => flowsPayload };
      if (url.startsWith('/api/active')) {
        if (active404) return { ok: false, status: 404, json: async () => ({ ok: false, error: 'Not found' }) };
        return { ok: true, status: 200, json: async () => activePayload };
      }
      if (url.startsWith('/api/flow/')) {
        const fid = decodeURIComponent(url.slice('/api/flow/'.length));
        return { ok: true, status: 200, json: async () => ({ ok: true, flow: flowsPayload.flows.find((x) => x.id === fid) || flowsPayload.flows[0] }) };
      }
      if (url.startsWith('/api/file')) return { ok: true, status: 200, json: async () => ({ ok: true, path: 'src/app.py', lines: [{ num: 10, text: 'x = 1', cited: true }] }) };
      if (url.startsWith('/api/stats')) return { ok: true, status: 200, json: async () => ({ ok: true, stats: { scope: 'all', rng: 'all', flowsCount: 4, agentsCount: 8, ratedAgents: 1, now: 1795620000, costUsd: 0.01, cacheSavingsUsd: 0, cacheHitPct: 50, toolCalls: 6, costByType: { cached: 0, uncached: 0.01, output: 0 }, buckets: { t0: 1795616400, step: 3600, b: [{ t: 0, l: { google: [0.01, 10, 20, 5, 0, 45, 0, 0, 0] } }] }, byModel: [{ model: 'gemini-4-neon', lab: 'google', agents: 1, durS: 45, in: 10, cache: 20, cacheRead: 20, out: 5, toolCalls: 2, costUsd: 0.01, rated: 1 }], topAgents: [['root', 'Standalone', 'gemini-4-neon', 'google', 'running', 45, 10, 20, 5, 0.01, 1795616400, 1795616445, 't3:thr-1', 'thr-1', 2]], dupRows: 0 } }) };
      return { ok: true, status: 200, json: async () => ({ ok: true, messages: [] }) };
    },
  };
  sandbox.window = sandbox; sandbox.self = sandbox; sandbox.top = sandbox;
  const click = (ds) => {
    const b = makeEl('button'); Object.assign(b.dataset, ds);
    b.closest = (s) => (s.includes('button') ? b : null);
    (docListeners.click || []).forEach((fn) => fn({ target: b }));
  };
  const key = (k) => (docListeners.keydown || []).forEach((fn) => fn({ key: k, target: {}, preventDefault() {} }));
  const inputOn = (id, val) => (docListeners.input || []).forEach((fn) => fn({ target: { id, value: val } }));
  vm.runInNewContext(code, sandbox);
  return { elements, fetchedUrls, activeIntervals, click, key, inputOn, sandbox, getES: () => lastES, activePayload };
}

(async () => {
  const assert = (cond, msg) => { if (!cond) throw new Error(msg); };
  const s1 = runSim();
  await new Promise((r) => setTimeout(r, 40));
  assert(!s1.fetchedUrls.some((u) => u.startsWith('/api/stats')), 'Must not fetch /api/stats on Board');
  assert(s1.elements.get('fbar').innerHTML.includes('class="acnt">3</b>'), 'Missing activeCount badge');
  assert(s1.elements.get('filt').innerHTML.includes('qlive'), 'Missing quick Live only toggle');
  assert(s1.elements.get('mapfr').innerHTML.includes('1 thread'), 'Missing 1 thread summary');
  assert(s1.elements.get('mapw').innerHTML.includes('<svg'), 'Missing rendered SVG map');

  // Quick Live only filter on Board, then open Active via hotkey 'a'
  s1.click({ filt: 'live', val: 'true' });
  assert(s1.elements.get('filt').innerHTML.includes('qlive" data-filt="live" data-val="true" aria-pressed="true"'), 'Live only toggle did not activate');
  s1.key('a');
  await new Promise((r) => setTimeout(r, 40));
  assert(s1.sandbox.location.hash.endsWith('v=active'), 'Hotkey a did not navigate to #v=active');
  assert(s1.elements.get('activePage').hidden === false, 'activePage stayed hidden');
  assert(s1.activeIntervals.size === 1, 'Expected 1s live timer on Active page');
  assert(s1.elements.get('alist').innerHTML.includes('Inspect →'), 'Missing Inspect button');
  assert(s1.elements.get('alist').innerHTML.includes('Open chat ↗'), 'Missing Open chat link');

  // Filter search + Unsettled tab clears timer
  s1.inputOn('aq', 'Committee');
  assert(s1.elements.get('alist').innerHTML.includes('Committee Run'), 'Search did not match Committee Run');
  assert(!s1.elements.get('alist').innerHTML.includes('Standalone Live Thread'), 'Search failed to filter out Standalone');
  s1.inputOn('aq', '');
  s1.click({ atab: 'unsettled' });
  assert(s1.activeIntervals.size === 0, 'Unsettled tab should clear live timer');
  s1.click({ atab: 'all' });
  assert(s1.activeIntervals.size === 1, 'All tab should restore live timer');

  // SSE update on Active updates activeCount and refetches /api/flows + /api/active
  s1.fetchedUrls.length = 0;
  s1.activePayload.liveCount = 5;
  s1.getES().emit('update');
  await new Promise((r) => setTimeout(r, 250));
  assert(s1.fetchedUrls.includes('/api/flows'), 'SSE update did not fetch /api/flows');
  assert(s1.fetchedUrls.includes('/api/active'), 'SSE update on Active did not fetch /api/active');
  assert(s1.elements.get('fbar').innerHTML.includes('class="acnt">5</b>'), 'SSE update did not refresh active badge');

  // Inspect -> on committee:c1 clears live filter and cleans up 1s timer
  s1.click({ inspect: 'committee:c1' });
  await new Promise((r) => setTimeout(r, 40));
  assert(s1.sandbox.location.hash === '#f=committee:c1&v=overview', 'Inspect did not switch flow');
  assert(s1.elements.get('activePage').hidden === true, 'activePage stayed visible after Inspect');
  assert(s1.activeIntervals.size === 0, 'Live timer leaked after Inspect');

  // Exercise remaining layouts, outputs, compare, needs, feed, stats
  for (let i = 0; i < 4; i++) {
    s1.click({ flow: String(i) });
    s1.click({ act: 'details' });
    s1.click({ act: 'key' });
    s1.click({ act: 'key' });
    s1.click({ act: 'details' });
  }
  s1.click({ flow: '1' });
  s1.click({ go: 'feed' });
  s1.click({ agent: 'w1' });
  for (const oid of ['o-md','o-json','o-tbl','o-vid','o-aud']) s1.click({ out: oid });
  s1.click({ file: 'src/app.py', line: '10' });
  await new Promise((r) => setTimeout(r, 30));
  s1.click({ flow: '3' });
  s1.click({ go: 'compare' });
  s1.click({ act: 'sbs' });
  s1.click({ act: 'align' });
  s1.click({ go: 'needs' });
  s1.click({ go: 'keys' });
  s1.click({ page: 'stats' });
  await new Promise((r) => setTimeout(r, 30));
  s1.click({ stab: 'agent' });
  s1.click({ stab: 'flow' });
  s1.click({ scope: 'all' });
  await new Promise((r) => setTimeout(r, 30));

  // Fallback check when /api/active returns 404
  const s2 = runSim({ active404: true, initialHash: '#v=active' });
  await new Promise((r) => setTimeout(r, 40));
  assert(s2.elements.get('activePage').hidden === false, 'Fallback activePage hidden');
  assert(s2.elements.get('alist').innerHTML.includes('Standalone Live Thread'), '404 fallback did not derive live T3 thread');
  assert(s2.elements.get('alist').innerHTML.includes('Demo Swarm'), '404 fallback did not derive waiting AGY swarm');
})().catch((err) => { console.error(err); process.exit(1); });
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as bundle_tmp:
            bundle_tmp.write(scripts[0])
            bundle_tmp.flush()
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as driver_tmp:
                driver_tmp.write(smoke_js)
                driver_tmp.flush()
                proc = subprocess.run(
                    [node_bin, driver_tmp.name, bundle_tmp.name],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15.0,
                )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"Headless UI smoke check failed:\n{proc.stderr}\n{proc.stdout}",
                )


if __name__ == "__main__":
    unittest.main()
