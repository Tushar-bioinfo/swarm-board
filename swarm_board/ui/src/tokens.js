// ===== tokens.js: Halftone Plate tokens: lab inks, dot screen, two-plate marks, node tokens =====
const CR = '#F5EAD3', LAB = {};
[['google', '#0cf574', '#0cd265'], ['anthropic', '#D97757', '#ba674c'], ['openai', '#c3cdd9', '#70767e'],
  ['xai', '#f4f4f2', '#8b8c8b'], ['t3', '#2f97c1', '#2a82a6'], ['other', '#aab4bf', '#6f7c8a']].forEach(([k, base, lo]) => (LAB[k] = { k, txt: base, base, lo, wire: base, hi: base }));
const LB = (l) => LAB[l] || LAB.other;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const f1 = (n) => Math.round(n * 10) / 10;
const outR = (l, r) => r;
const MK = { t3: 'M-2.4 -3.8L-2.4 3.8M3 -1.9C2.98 .57 -2.41 -.23 -2.41 2.06', google: 'M0 -5.73C.75 -.75 .75 -.75 5.73 0C.75 .75 .75 .75 0 5.73C-.75 .75 -.75 .75 -5.73 0C-.75 -.75 -.75 -.75 0 -5.73Z',
  anthropic: 'M0 -2.1L0 -5.7M1.5 -1.5L3 -3M2.1 0L5.7 0M1.5 1.5L3 3M0 2.1L0 5.7M-1.5 1.5L-3 3M-2.1 0L-5.7 0M-1.5 -1.5L-3 -3',
  openai: 'M0 -5.7L5 -2.9L5 2.9L0 5.7L-5 2.9L-5 -2.9ZM0 0L0 5.7M0 0L-5 -2.9M0 0L5 -2.9', xai: 'M-4.82 4.82L4.82 -4.82', other: 'M0 -2.2h0' };
const MKC = new Map();
function mark(l, r, col) {
  const q = r / 12.2, k = l + r, w = f1(Math.max(1.1, 1.45 * q));
  let d = MKC.get(k);
  if (d == null) { d = (MK[l] || MK.other).replace(/-?[\d.]+/g, (n) => f1(n * q)); MKC.set(k, d); }
  let s = `<path d="${d}" fill="none" stroke="${col}" stroke-width="${l === 'other' ? 3 * w : w}" stroke-linecap="round" stroke-linejoin="round"/>`;
  if (l === 't3') s += [[-2.41, -3.78], [-2.41, 3.78], [2.98, -1.95]].map(([x, y]) => `<circle cx="${f1(x * q)}" cy="${f1(y * q)}" r="${w}" fill="${col}"/>`).join('');
  if (l === 'xai') s += `<circle r="${f1(2.75 * q)}" fill="none" stroke="${col}" stroke-width="${w}"/>`;
  return s;
}
const plates = (l, r, op) => { const o = f1(Math.max(.6, .075 * r)); return `<g opacity="${op}"><g transform="translate(${o} ${o})">${mark(l, r, LB(l).lo)}</g>${mark(l, r, CR)}</g>`; };
const HTG = new Map(), HTC = new Map();
function htGrid(r) {
  let G = HTG.get(r);
  if (G) return G;
  G = [];
  const st = clamp(.197 * r, 1.9, 3), c = Math.cos(Math.PI / 12), s = Math.sin(Math.PI / 12), n = Math.ceil(r / st) + 1;
  for (let i = -n; i <= n; i++) for (let j = -n; j <= n; j++) {
    const x = st * (i * c - j * s), y = st * (i * s + j * c), d = Math.hypot(x, y) / r, u = (x + y) / r;
    if (d > .885 || d < .38) continue;
    const z = Math.round(st * (.73 + .23 * u) * clamp((d - .38) / .18, .2, 1) * 5) / 5;
    if (z >= .3) G.push({ x: f1(x), y: f1(y), z, u, v: y / r, e: (i + j) & 1 });
  }
  HTG.set(r, G);
  return G;
}
function htDots(P, col, op, cls, k = 1) {
  const by = {};
  P.forEach((p) => { const z = f1(Math.max(.4, p.z * k)); (by[z] = by[z] || []).push(`M${p.x} ${p.y}h0`); });
  return `<g${cls ? ` class="${cls}"` : ''} fill="none" stroke="${col}"${op < 1 ? ` stroke-opacity="${op}"` : ''} stroke-linecap="round">${Object.keys(by).map((z) => `<path stroke-width="${z}" d="${by[z].join('')}"/>`).join('')}</g>`;
}
function htScreen(r, col, st) {
  const key = r + st + col;
  let s = HTC.get(key);
  if (s != null) return s;
  const G = htGrid(r);
  if (st === 'running') s = [-.6, 0, .6, 9].map((t, i, T) => htDots(G.filter((p) => p.u < t && p.u >= (i ? T[i - 1] : -9)), col, 1, 'wv w' + i)).join('');
  else if (st === 'interrupted') s = htDots(G.filter((p) => p.v > .03), col, .78) + htDots(G.filter((p) => p.v <= .03), col, .16, '', .45);
  else if (st === 'queued') s = htDots(G.filter((p) => p.e), col, .35, '', .5);
  else s = htDots(G, col, st === 'killed' || st === 'error' ? .3 : st === 'waiting' ? .5 : .72);
  HTC.set(key, s);
  return s;
}
const SC = (r, c, x = '') => `<circle r="${f1(r)}" fill="none" stroke="${c}"${x}/>`;
const rim = (r, col, op, d) => d ? `<path d="${d}" fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="1.1" stroke-dasharray="1.7 1.25"/>` : SC(r, col, ` stroke-opacity="${op}" stroke-width="1.1" stroke-dasharray="1.7 1.25"`);
function medal(r, ring) {
  let t = '', M = '';
  for (let i = 0; i < 36; i++) {
    const a = i * Math.PI / 18, c = Math.cos(a), s = Math.sin(a);
    t += `M${f1(c * (r + 11))} ${f1(s * (r + 11))}L${f1(c * (r + 14))} ${f1(s * (r + 14))}`;
    if (i % 9 === 0) M += `M${f1(c * (r + 9))} ${f1(s * (r + 9))}L${f1(c * (r + 17))} ${f1(s * (r + 17))}`;
  }
  return SC(r + 8, CR, ' stroke-opacity=".4" stroke-width=".9"') + `<path d="${t}" stroke="${C.line}" stroke-width=".9"/><path d="${M}" stroke="${CR}" stroke-opacity=".6" stroke-width=".9"/>` +
    (ring ? SC(r + 20, C.thin, ' stroke-dasharray="1 4"') : '');
}
function pipIn(a, r, L) {
  const st = a.state, dot = (c, op, w) => SC(r - .6, c, ` stroke-opacity="${op}" stroke-width="${w || 1.3}" stroke-linecap="round" stroke-dasharray=".01 2.2"`);
  if (st === 'running') return SC(r + 1.6, L.base, ' class="aura" stroke-opacity=".3" stroke-width="1.4"') + `<circle r="${f1(.48 * r)}" fill="${L.base}"/>${dot(L.base, .9)}`;
  if (isStop(a)) return dot(C.bad, .85) + `<circle r="${f1(.22 * r)}" fill="${L.base}" fill-opacity=".45"/>`;
  return dot(st === 'queued' ? C.line : L.base, st === 'done' ? .75 : .5);
}
function tokIn(a, s, o) {
  o = o || {};
  const l = LAB[a.lab] ? a.lab : 'other', L = LAB[l], r = s / 2, st = a.state, B = L.base, stop = isStop(a);
  if (s < 14) return pipIn(a, r, L);
  let m = '';
  if (o.med) m += medal(r, o.ring);
  if (st === 'running') m += SC(1.21 * r, B, ' class="aura" stroke-opacity=".18" stroke-width="2.6"') + SC(1.4 * r, B, ' class="aura" stroke-opacity=".07" stroke-width="2.4"');
  m += SC(r + 2, B, ' class="ping"') + SC(r + 3.5, CR, ' class="bz" stroke-opacity=".5"');
  if (st === 'waiting') m += SC(r + 5, C.acc, ' stroke-width="2" stroke-linecap="round" stroke-dasharray=".01 3.4"');
  if (st === 'stale') m += SC(r + 4, C.mist, ' stroke-dasharray="1 3"');
  m += `<circle r="${f1(r)}" fill="${C.bg}"/>` + htScreen(r, B, stop ? 'killed' : st);
  if (st === 'interrupted') m += rim(r, B, .16) + rim(r, B, .75, `M${f1(r)} .6A${f1(r)} ${f1(r)} 0 0 1 ${f1(-r)} .6`);
  else m += rim(r, stop ? C.bad : st === 'queued' ? C.line : B, stop ? .85 : st === 'running' ? .95 : st === 'queued' ? .6 : .55);
  if (st !== 'queued') m += plates(l, r, stop ? .45 : st === 'interrupted' ? .62 : 1);
  const h = f1(r + 7), q = f1(r + 1);
  m += `<path class="ret" d="M${-h} ${-q}V${-h}H${-q}M${q} ${-h}H${h}V${-q}M${h} ${q}V${h}H${q}M${-q} ${h}H${-h}V${q}" fill="none" stroke="${C.ink}" stroke-width="2"/>`;
  return st === 'stale' ? `<g opacity=".55">${m}</g>` : m;
}
function token(a, x, y, s, o) {
  o = o || {};
  const cls = 'nd' + (a.state === 'running' ? ' run' : '') + (o.sel ? ' sel' : '') + (o.dim ? ' dim' : '') + (o.lit ? ' lit' : '') + (o.cls ? ' ' + o.cls : '');
  return `<g class="${cls}" data-agent="${esc(a.id)}" ${BATR(`${aName(a)}, ${a.role}, ${stateWord(a)}`)}${o.attr || ''}><g transform="translate(${f1(x)} ${f1(y)})">${tokIn(a, s, o)}<circle class="fx" r="${f1(Math.max(s / 2 + 6, 14))}" fill="transparent"/></g></g>`;
}
const emb = (l, st, s = 16) => `<svg class="emb" width="${s + 4}" height="${s + 4}" viewBox="${-(s + 4) / 2} ${-(s + 4) / 2} ${s + 4} ${s + 4}" aria-hidden="true">${tokIn({ lab: l, state: st || 'done' }, s)}</svg>`;
