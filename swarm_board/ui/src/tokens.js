// ===== tokens.js =====
const CR = '#F5EAD3', RND = ' stroke-linecap="round"', LB = (l) => LAB[l] || LAB.other,
  clp = (v, a, b) => MX(a, MN(b, v)), f1 = (n) => RD(n * 10) / 10, outR = (l, r) => r,
  MK = { t3: 'M-2.4 -3.8L-2.4 3.8M3 -1.9C2.98 .57 -2.41 -.23 -2.41 2.06', google: 'M0 -5.73C.75 -.75 .75 -.75 5.73 0C.75 .75 .75 .75 0 5.73C-.75 .75 -.75 .75 -5.73 0C-.75 -.75 -.75 -.75 0 -5.73Z',
    anthropic: 'M0 -2.1L0 -5.7M1.5 -1.5L3 -3M2.1 0L5.7 0M1.5 1.5L3 3M0 2.1L0 5.7M-1.5 1.5L-3 3M-2.1 0L-5.7 0M-1.5 -1.5L-3 -3',
    openai: 'M0 -5.7L5 -2.9L5 2.9L0 5.7L-5 2.9L-5 -2.9ZM0 0L0 5.7M0 0L-5 -2.9M0 0L5 -2.9', xai: 'M-4.82 4.82L4.82 -4.82', other: 'M0 -2.2h0' },
  MK_ = new Map(), HG = new Map(), HC_ = new Map(),
  SC = (r, c, x = '') => `<circle r="${f1(r)}"${FN} stroke="${c}"${x}/>`,
  rim = (r, col, op, d) => (d ? `<path d="${d}"${FN} stroke="${col}" ${SO}="${op}" ${SW}="1.1" ${SD}="1.7 1.25"/>` : SC(r, col, ` ${SO}="${op}" ${SW}="1.1" ${SD}="1.7 1.25"`));
function mark(l, r, col) {
  const q = r / 12.2, k = l + r, w = f1(MX(1.1, 1.45 * q));
  let d = MK_.get(k);
  if (d == null) { d = (MK[l] || MK.other).replace(/-?[\d.]+/g, (n) => f1(n * q)); MK_.set(k, d); }
  let s = `<path d="${d}"${FN} stroke="${col}" ${SW}="${l === 'other' ? 3 * w : w}"${RND} stroke-linejoin="round"/>`;
  if (l === 't3') s += JN([[-2.41, -3.78], [-2.41, 3.78], [2.98, -1.95]], ([x, y]) => `<circle cx="${f1(x * q)}" cy="${f1(y * q)}" r="${w}" fill="${col}"/>`);
  if (l === 'xai') s += `<circle r="${f1(2.75 * q)}"${FN} stroke="${col}" ${SW}="${w}"/>`;
  return s;
}
const plts = (l, r, op) => { const o = f1(MX(.6, .075 * r)); return `<g opacity="${op}"><g transform="translate(${o} ${o})">${mark(l, r, LB(l).lo)}</g>${mark(l, r, CR)}</g>`; };
function htG(r) {
  let G = HG.get(r); if (G) return G;
  G = [];
  const st = clp(.197 * r, 1.9, 3), c = Math.cos(Math.PI / 12), s = Math.sin(Math.PI / 12), n = Math.ceil(r / st) + 1;
  for (let i = -n; i <= n; i++) for (let j = -n; j <= n; j++) {
    const x = st * (i * c - j * s), y = st * (i * s + j * c), d = Math.hypot(x, y) / r, u = (x + y) / r;
    if (d > .885 || d < .38) continue;
    const z = RD(st * (.73 + .23 * u) * clp((d - .38) / .18, .2, 1) * 5) / 5;
    if (z >= .3) G.push({ x: f1(x), y: f1(y), z, u, v: y / r, e: (i + j) & 1 });
  }
  HG.set(r, G); return G;
}
function htD(P, col, op, cls, k = 1) {
  const by = {};
  P.forEach((p) => { const z = f1(MX(.4, p.z * k)); (by[z] = by[z] || []).push(`M${p.x} ${p.y}h0`); });
  return `<g${cls ? ` class="${cls}"` : ''}${FN} stroke="${col}"${op < 1 ? ` ${SO}="${op}"` : ''}${RND}>${JN(OK(by), (z) => `<path ${SW}="${z}" d="${by[z].join('')}"/>`)}</g>`;
}
function htScreen(r, col, st) {
  const key = r + st + col; let s = HC_.get(key); if (s != null) return s;
  const G = htG(r);
  s = st === 'running' ? JN([-.6, 0, .6, 9], (t, i, T) => htD(G.filter((p) => p.u < t && p.u >= (i ? T[i - 1] : -9)), col, 1, 'wv w' + i))
    : st === 'interrupted' ? htD(G.filter((p) => p.v > .03), col, .78) + htD(G.filter((p) => p.v <= .03), col, .16, '', .45)
    : st === 'queued' ? htD(G.filter((p) => p.e), col, .35, '', .5)
    : htD(G, col, st === 'killed' || st === 'error' ? .3 : st === 'waiting' ? .5 : .72);
  HC_.set(key, s); return s;
}
function mdl(r, ring) {
  let t = '', M = '';
  for (let i = 0; i < 36; i++) {
    const a = i * Math.PI / 18, c = Math.cos(a), s = Math.sin(a);
    t += `M${f1(c * (r + 11))} ${f1(s * (r + 11))}L${f1(c * (r + 14))} ${f1(s * (r + 14))}`;
    if (i % 9 === 0) M += `M${f1(c * (r + 9))} ${f1(s * (r + 9))}L${f1(c * (r + 17))} ${f1(s * (r + 17))}`;
  }
  return SC(r + 8, CR, ` ${SO}=".4" ${SW}=".9"`) + `<path d="${t}" stroke="${C.line}" ${SW}=".9"/><path d="${M}" stroke="${CR}" ${SO}=".6" ${SW}=".9"/>` +
    (ring ? SC(r + 20, C.thin, ` ${SD}="1 4"`) : '');
}
function pIn(a, r, L) {
  const st = a.state, dot = (c, op, w) => SC(r - .6, c, ` ${SO}="${op}" ${SW}="${w || 1.3}"${RND} ${SD}=".01 2.2"`);
  return st === 'running' ? SC(r + 1.6, L.base, ` class="aura" ${SO}=".3" ${SW}="1.4"`) + `<circle r="${f1(.48 * r)}" fill="${L.base}"/>${dot(L.base, .9)}`
    : isStop(a) ? dot(C.bad, .85) + `<circle r="${f1(.22 * r)}" fill="${L.base}" fill-opacity=".45"/>`
    : dot(st === 'queued' ? C.line : L.base, st === 'done' ? .75 : .5);
}
function tIn(a, s, o = {}) {
  const l = LAB[a.lab] ? a.lab : 'other', L = LAB[l], r = s / 2, st = a.state, B = L.base, stop = isStop(a);
  if (s < 14) return pIn(a, r, L);
  let m = (o.med ? mdl(r, o.ring) : '') +
    (st === 'running' ? SC(1.21 * r, B, ` class="aura" ${SO}=".18" ${SW}="2.6"`) + SC(1.4 * r, B, ` class="aura" ${SO}=".07" ${SW}="2.4"`) : '') +
    SC(r + 2, B, ' class="ping"') + SC(r + 3.5, CR, ` class="bz" ${SO}=".5"`) +
    (st === 'waiting' ? SC(r + 5, C.acc, ` ${SW}="2"${RND} ${SD}=".01 3.4"`) : '') +
    (st === 'stale' ? SC(r + 4, C.mist, ` ${SD}="1 3"`) : '') +
    `<circle r="${f1(r)}" fill="${C.bg}"/>` + htScreen(r, B, stop ? 'killed' : st);
  m += st === 'interrupted' ? rim(r, B, .16) + rim(r, B, .75, `M${f1(r)} .6A${f1(r)} ${f1(r)} 0 0 1 ${f1(-r)} .6`)
    : rim(r, stop ? C.bad : st === 'queued' ? C.line : B, stop ? .85 : st === 'running' ? .95 : st === 'queued' ? .6 : .55);
  if (st !== 'queued') m += plts(l, r, stop ? .45 : st === 'interrupted' ? .62 : 1);
  const h = f1(r + 7), q = f1(r + 1);
  m += `<path class="ret" d="M${-h} ${-q}V${-h}H${-q}M${q} ${-h}H${h}V${-q}M${h} ${q}V${h}H${q}M${-q} ${h}H${-h}V${q}"${FN} stroke="${C.ink}" ${SW}="2"/>`;
  return st === 'stale' ? `<g opacity=".55">${m}</g>` : m;
}
const tTok = (a, x, y, s, o = {}) => `<g class="nd${a.state === 'running' ? ' run' : ''}${o.sel ? ' sel' : ''}${o.dim ? ' dim' : ''}${o.lit ? ' lit' : ''}${o.cls ? ' ' + o.cls : ''}" data-agent="${E(a.id)}" ${BATR(`${aLb(a)}, ${a.role}, ${stWord(a)}`)}${o.attr || ''}><g transform="translate(${f1(x)} ${f1(y)})">${tIn(a, s, o)}<circle class="fx" r="${f1(MX(s / 2 + 6, 14))}" fill="transparent"/></g></g>`,
  emb = (l, st, s = 16) => `<svg class="emb" width="${s + 4}" height="${s + 4}" viewBox="${-(s + 4) / 2} ${-(s + 4) / 2} ${s + 4} ${s + 4}" aria-hidden="true">${tIn({ lab: l, state: st || 'done' }, s)}</svg>`;