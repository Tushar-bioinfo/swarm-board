// ===== map.js: map builder, part labels, drawMap layers, highlight =====
let MC = null;
const FUI = '"Inter",system-ui,-apple-system,"Segoe UI",sans-serif', FMO = '"JetBrains Mono",ui-monospace,Menlo,monospace';
const TWC = new Map();
function tw(t, size, wt, mono) {
  const f = `${mono ? 400 : wt || 400} 100px ${mono ? FMO : FUI}`, k = f + '|' + t;
  let w = TWC.get(k);
  if (w == null) { MC = MC || CE('canvas').getContext('2d'); if (MC.font !== f) MC.font = f; w = MC.measureText(String(t)).width; TWC.set(k, w); }
  return w * size / 100;
}
function shortNames(sibs) {
  const names = sibs.map((a) => aName(a));
  let p = 0;
  while (names.length > 1 && names.every((x) => x[p] && x[p] === names[0][p])) p++;
  const cut = p >= 3 ? p : 0;
  return Object.fromEntries(sibs.map((a, i) => [a.id, trunc((names[i].slice(cut) || String(i + 1)).replace(/-/g, ' '), 18)]));
}
function MapB(W, det, wit) { Object.assign(this, { W, det, wit, nodes: [], labels: [], boxes: [], bg: '', cab: '', seal: '', pods: '', beads: '', top: '' }); }
MapB.prototype.node = function (a, x, y, s, o) { this.nodes.push({ a, x, y, s, o: o || {} }); const q = Math.max(s / 2 + 6, 14); this.boxes.push([x - q, y - q, x + q, y + q, 4]); };
MapB.prototype.cable = function (g, a, o) { o = o || {}; o.on = o.on || this.wit.includes(a.id); o.dim = dimOf(a); this.cab += cable(g, a, o); };
MapB.prototype.label = function (o) { this.labels.push(o); };
MapB.prototype.place = function () {
  const out = [], hit = (b) => this.boxes.some((c) => b[0] < c[2] + c[4] && b[2] + c[4] > c[0] && b[1] < c[3] + c[4] && b[3] + c[4] > c[1]);
  for (const o of this.labels) {
    for (const st of o.steps) {
      if (st == null || st === '' || (Array.isArray(st) && !st.length)) continue;
      const P = Array.isArray(st) ? st : [[st]], mo = (p) => (p[2] == null ? o.mono : p[2]);
      const w = P.reduce((s, p) => s + tw(p[0], o.size, p[3] || o.wt, mo(p)) + (o.ls ? o.ls * p[0].length : 0), 0);
      const x0 = o.anchor === 'middle' ? o.x - w / 2 : o.anchor === 'end' ? o.x - w : o.x;
      const b = [x0, o.y - o.size * 0.82, x0 + w, o.y + o.size * 0.26, 1];
      if (b[0] < -2 || b[2] > this.W + 2 || (o.maxX && b[2] > o.maxX) || hit(b)) continue;
      this.boxes.push(b);
      const body = Array.isArray(st) ? P.map((p) => `<tspan${p[1] ? ` fill="${p[1]}"` : ''}${mo(p) !== o.mono ? ` class="${mo(p) ? 'mo' : 'ui'}"` : ''}${p[3] ? ` font-weight="${p[3]}"` : ''}>${esc(p[0])}</tspan>`).join('') : esc(st);
      out.push(`<text x="${f1(o.x)}" y="${f1(o.y)}" text-anchor="${o.anchor || 'start'}" font-size="${o.size}" font-weight="${o.wt || 400}"${o.mono ? ' class="mo"' : ''} fill="${o.fill || C.ink}"${o.ls ? ` letter-spacing="${o.ls}"` : ''} data-for="${esc(o.for || '')}" pointer-events="${o.for ? 'auto' : 'none'}">${body}</text>`);
      break;
    }
  }
  return out.join('');
};
function witness(f, sel) { const p = [], seen = new Set(); let a = sel && agentOf(f, sel); while (a && !seen.has(a.id)) { seen.add(a.id); p.push(a.id); a = a.parent && agentOf(f, a.parent); } return p; }
function dimOf(a) { const d = S.dim; if (!d || d === 'all') return false; if (d === 'killed') return !isStop(a); return a.state !== d; }
const litOf = (a) => !!S.dim && S.dim !== 'all' && !dimOf(a);
function drawMap() {
  const f = flow(), host = $('mapw');
  if (!f || !host) return;
  if (!f.agents || !f.agents.length) { host.innerHTML = EMP('No agents recorded in this flow.'); return; }
  const W = Math.max(260, S.mapW || Math.floor(geo().mapW)), det = W < 460 ? 'S' : W < 800 ? 'M' : 'L', k = det === 'S' ? 1 : clamp(W / 700, 1, 1.45);
  lastMapW = W;
  const B = new MapB(W, det, f.kind === 'committee' ? (S.sel ? [S.sel] : []) : witness(f, S.sel));
  B.k = k;
  const H = Math.max(60, (({ swarm: lanes, committee: council, tree: canopy })[f.kind] || fan)(f, B, det) || 80);
  const nd = B.nodes.map((n) => token(n.a, n.x, n.y, n.s, Object.assign({ sel: n.a.id === S.sel, dim: dimOf(n.a), lit: litOf(n.a) }, n.o))).join('');
  const sp = B.nodes.map((n) => sparks(n.a, n.x, n.y, n.o.R || outR(n.a.lab, n.s / 2))).join('');
  const unf = f.kind === 'tree' && S.unfold;
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="Agent map"${unf ? ' class="unf"' : ''}>${B.bg}${B.cab}${B.seal}${B.pods}${nd}${B.beads}${sp}${B.top}${B.place()}</svg>`;
  qa('text[data-for]', host).forEach((t) => { const a = agentOf(f, t.dataset.for); if (a && dimOf(a)) t.classList.add('dim'); });
  if (f.kind === 'tree') unfoldAnim(host);
}
function hl(svg, ids, post) {
  if (!svg) return;
  svg.classList.add('hl');
  ids.forEach((id) => { const q = CSS.escape(id); qa(`[data-agent="${q}"],.cab[data-to="${q}"],.sec[data-to="${q}"],text[data-for="${q}"]`, svg).forEach((e) => e.classList.add('on')); });
  if (post) qa(`.bd[data-post="${post}"]`, svg).forEach((e) => e.classList.add('on'));
}
function unhl() { qa('.mapw svg.hl').forEach((s) => { s.classList.remove('hl'); qa('.on', s).forEach((e) => e.classList.remove('on')); }); }
