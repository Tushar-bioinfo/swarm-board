// ===== map.js =====
let MC = null;
const FUI = '"Inter",system-ui,-apple-system,"Segoe UI",sans-serif', FMO = '"JetBrains Mono",ui-monospace,Menlo,monospace', TC_ = new Map();
function tw(t, size, wt, mono) {
  const f = `${mono ? 400 : wt || 400} 100px ${mono ? FMO : FUI}`, k = f + '|' + t;
  let w = TC_.get(k);
  if (w == null) { MC = MC || CE('canvas').getContext('2d'); if (MC.font !== f) MC.font = f; w = MC.measureText(String(t)).width; if (TC_.size > 500) TC_.clear(); TC_.set(k, w); }
  return w * size / 100;
}
function shNm(sibs) {
  const names = sibs.map(aLb); let p = 0;
  while (names.length > 1 && names.every((x) => x[p] && x[p] === names[0][p])) p++;
  const cut = p >= 3 ? p : 0;
  return Object.fromEntries(sibs.map((a, i) => [a.id, trunc((names[i].slice(cut) || String(i + 1)).replace(/-/g, ' '), 18)]));
}
function MapB(W, det, wit) { Object.assign(this, { W, det, wit, nodes: [], labels: [], boxes: [], bg: '', cab: '', seal: '', pods: '', beads: '', top: '' }); }
MapB.prototype.node = function (a, x, y, s, o = {}) { this.nodes.push({ a, x, y, s, o }); const q = MX(s / 2 + 6, 14); this.boxes.push([x - q, y - q, x + q, y + q, 4]); };
MapB.prototype.cable = function (g, a, o = {}) { o.on = o.on || this.wit.includes(a.id); o.dim = dOf_(a); this.cab += cbl(g, a, o); };
MapB.prototype.label = function (o) { this.labels.push(o); };
MapB.prototype.place = function () {
  const out = [], hit = (b) => this.boxes.some((c) => b[0] < c[2] + c[4] && b[2] + c[4] > c[0] && b[1] < c[3] + c[4] && b[3] + c[4] > c[1]);
  for (const o of this.labels) for (const st of o.steps) {
    if (st == null || st === '' || (isArr(st) && !st.length)) continue;
    const P = isArr(st) ? st : [[st]], mo = (p) => (p[2] == null ? o.mono : p[2]),
      w = P.reduce((s, p) => s + tw(p[0], o.size, p[3] || o.wt, mo(p)) + (o.ls ? o.ls * p[0].length : 0), 0);
    let x = o.x, x0 = o.anchor === 'middle' ? x - w / 2 : o.anchor === 'end' ? x - w : x;
    if (o.edge && x0 < 1) { x += 1 - x0; x0 = 1; }
    const b = [x0, o.y - o.size * 0.82, x0 + w, o.y + o.size * 0.26, 1];
    if (b[0] < -2 || b[2] > this.W + 2 || (o.maxX && b[2] > o.maxX) || hit(b)) continue;
    this.boxes.push(b);
    const body = isArr(st) ? JN(P, (p) => `<tspan${p[1] ? ` fill="${p[1]}"` : ''}${mo(p) !== o.mono ? ` class="${mo(p) ? 'mo' : 'ui'}"` : ''}${p[3] ? ` font-weight="${p[3]}"` : ''}>${E(p[0])}</tspan>`) : E(st);
    out.push(`<text x="${f1(x)}" y="${f1(o.y)}" text-anchor="${o.anchor || 'start'}" font-size="${o.size}" font-weight="${o.wt || 400}"${o.mono ? ' class="mo"' : ''} fill="${o.fill || C.ink}"${o.ls ? ` letter-spacing="${o.ls}"` : ''} data-for="${E(o.for || '')}" pointer-events="${o.for ? 'auto' : 'none'}">${body}</text>`);
    break;
  }
  return out.join('');
};
function wit_(f, sel) { const p = [], seen = new Set(); let a = sel && agOf(f, sel); while (a && !seen.has(a.id)) { seen.add(a.id); p.push(a.id); a = a.parent && agOf(f, a.parent); } return p; }
const dOf_ = (a) => { const d = S.dm; return !d || d === 'all' ? false : d === 'killed' ? !isStop(a) : a.state !== d; },
  lOf_ = (a) => !!S.dm && S.dm !== 'all' && !dOf_(a);
function dMap() {
  const f = flow(), host = $('mapw');
  if (!f || !host) return;
  if (!f.agents || !f.agents.length) { host.innerHTML = EMP('No agents recorded in this flow.'); return; }
  const W = MX(260, S.mw || FLR(geo().mapW)), det = W < 460 ? 'S' : W < 800 ? 'M' : 'L', k = det === 'S' ? 1 : clp(W / 700, 1, 1.45);
  lMW = W;
  const B = new MapB(W, det, f.kind === 'committee' ? (S.sel ? [S.sel] : []) : wit_(f, S.sel));
  B.k = k;
  const H = MX(60, (({ swarm: lns, committee: cnl, tree: cnp })[f.kind] || fan)(f, B, det) || 80),
    nd = JN(B.nodes, (n) => tTok(n.a, n.x, n.y, n.s, Object.assign({ sel: n.a.id === S.sel, dim: dOf_(n.a), lit: lOf_(n.a) }, n.o))),
    sp = JN(B.nodes, (n) => spks(n.a, n.x, n.y, n.o.R || outR(n.a.lab, n.s / 2))),
    unf = f.kind === 'tree' && S.uf;
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="Agent map"${unf ? ' class="unf"' : ''}>${B.bg}${B.cab}${B.seal}${B.pods}${nd}${B.beads}${sp}${B.top}${B.place()}</svg>`;
  qa('text[data-for]', host).forEach((t) => { const a = agOf(f, t.dataset.for); if (a && dOf_(a)) CL(t).add('dim'); });
  if (f.kind === 'tree') uAnim(host);
}
function hl(svg, ids, post) {
  if (!svg) return;
  CL(svg).add('hl');
  ids.forEach((id) => { const q = CSS.escape(id); qa(`[data-agent="${q}"],.cab[data-to="${q}"],.sec[data-to="${q}"],text[data-for="${q}"]`, svg).forEach((e) => CL(e).add('on')); });
  if (post) qa(`.bd[data-post="${CSS.escape(String(post))}"]`, svg).forEach((e) => CL(e).add('on'));
}
const unhl = () => qa('.mapw svg.hl').forEach((s) => { CL(s).remove('hl'); qa('.on', s).forEach((e) => CL(e).remove('on')); });