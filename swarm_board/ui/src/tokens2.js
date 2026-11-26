// ===== tokens2.js =====
const cubD = (P) => `M${P[0].map(f1)}C${P[1].map(f1)} ${P[2].map(f1)} ${P[3].map(f1)}`.replace(/,/g, ' '),
  bz = (P, t) => { const u = 1 - t; return [0, 1].map((k) => u * u * u * P[0][k] + 3 * u * u * t * P[1][k] + 3 * u * t * t * P[2][k] + t * t * t * P[3][k]); },
  bzT = (P, t) => { const u = 1 - t; return [0, 1].map((k) => 3 * u * u * (P[1][k] - P[0][k]) + 6 * u * t * (P[2][k] - P[1][k]) + 3 * t * t * (P[3][k] - P[2][k])); };
function cbl(g, a, o = {}) {
  const L = LB(a.lab), st = a.state, id = E(a.id), w = o.w || 1.5, d = isArr(g) ? cubD(g) : g, stop = isStop(a);
  let col = L.base, op = .55, dash = `.01 ${f1(MX(3, 1.5 * w))}`, sw = w + .4;
  if (st === 'running') { op = .3; dash = ''; sw = w; } else if (st === 'interrupted') { op = .55; dash = '5 4'; sw = w; } else if (stop) { col = C.line; op = .8; dash = '3 5'; sw = w; } else if (st === 'queued') { col = C.line; op = .45; }
  if (o.op) op = o.op;
  let s = `<path class="cab${o.cls ? ' ' + o.cls : ''}${o.on ? ' wit' : ''}${o.dim ? ' dim' : ''}" data-to="${id}" d="${d}"${FN} stroke="${col}" ${SW}="${f1(sw)}" ${SO}="${op}"${dash ? ` ${SD}="${dash}"` : ''}${RND}/>`;
  if (st === 'running' && !o.nomarch) s += `<path class="march${o.cls ? ' ' + o.cls : ''}" d="${d}" stroke="${L.hi}"/>`;
  if (stop && isArr(g)) {
    const p = bz(g, .86), t = bzT(g, .86), n = Math.hypot(t[0], t[1]) || 1, ux = t[0] / n, uy = t[1] / n;
    s += `<path d="${JN([-2.5, 2.5], (e) => `M${f1(p[0] + ux * e - uy * 4)} ${f1(p[1] + uy * e + ux * 4)}l${f1(uy * 8)} ${f1(-ux * 8)}`)}" stroke="${C.bad}" ${SW}="1.5"/>`;
  }
  return s;
}
function spks(a, x, y, R) {
  const n = errN(a), m = MN(12, n);
  if (!m) return '';
  const ang = (i) => (m === 1 ? 270 : 215 + i * 110 / (m - 1)) * Math.PI / 180, pt = (g, rr) => f1(x + Math.cos(g) * rr) + ' ' + f1(y + Math.sin(g) * rr);
  let s = '';
  for (let i = 0; i < m; i++) s += `<path class="sp" d="M${pt(ang(i), R + 4)}L${pt(ang(i), R + 10)}" stroke="${C.bad}" ${SW}="2"${RND}/><circle cx="${pt(ang(i), R + 13.5).replace(' ', '" cy="')}" r="1" fill="${C.bad}" fill-opacity=".7"/>`;
  const a0 = m === 1 ? ang(0) - .3 : ang(0), a1 = m === 1 ? ang(0) + .3 : ang(m - 1);
  s += `<path d="M${pt(a0, R + 7.5)}A${R + 7.5} ${R + 7.5} 0 0 1 ${pt(a1, R + 7.5)}" stroke="transparent" ${SW}="12"${FN}/>`;
  if (n > 12) s += `<text x="${x}" y="${f1(y - R - 26)}" text-anchor="middle" font-size="15" fill="${C.bad}">+${n - 12}</text>`;
  return `<g class="spk" data-agent="${E(a.id)}" ${BATR(`${n} error${n > 1 ? 's' : ''} on ${aLb(a)}`)}>${s}</g>`;
}
function bd_(p, x, y, to, out) {
  const open = S.view === 'output' && S.arg === 'post-' + p.id;
  return `<g class="bd" data-post="${p.id}" data-to="${E(to)}" ${BATR(`Board post #${p.id}, ${p.author} to ${(p.mentions || []).join(', ')}`)}><g transform="translate(${f1(x)} ${f1(y)})"><circle r="9" fill="transparent"/>${SC(7, C.ink, ` ${SO}=".22" ${SD}=".01 2.2"${RND} ${SW}="1.3"`)}` +
    (out ? `<circle class="bb" r="4" fill="${C.ink}"/>` : `<circle class="bb" r="3.6" fill="${C.bg}" stroke="${C.ink}" ${SW}="1.6"/>`) + (open ? SC(8, C.ink) : '') + '</g></g>';
}
function mDep(f) {
  let mx = 0;
  (f.agents || []).forEach((a) => {
    let d = 0, x = a, seen = new Set([a.id]);
    while (x && x.parent && !seen.has(x.parent) && d < 12) { seen.add(x.parent); x = agOf(f, x.parent); if (x) d++; }
    if (d > mx) mx = d;
  });
  return MX(1, mx);
}
function rMap() {
  const f = flow();
  if (!f) { IH('mapfr', ''); return; }
  const ag = f.agents || [], n = ag.length, r = rOf(f), mdp = mDep(f),
    sum = !n ? '0 agents'
      : f.kind === 'thread' && n === 1 ? `1 thread · ${stWord(ag[0])}`
      : f.kind === 'tree' && r && kids(f, r.id).some((l) => kids(f, l.id).length)
        ? (() => { const L = kids(f, r.id), Wk = ag.filter((a) => a.role === 'worker'); return `${L.length} leads · ${Wk.length} workers · ${Wk.filter((a) => a.state === 'running').length} running · ${Wk.filter((a) => a.state === 'done').length} done`; })()
      : f.kind === 'swarm' ? `${n} agents · ${(f.workstreams || []).length} lanes`
      : f.kind === 'committee' ? `${n} seats · one per lab`
      : `${n} agent${n === 1 ? '' : 's'} · ${mdp} level${mdp === 1 ? '' : 's'}`;
  IH('mapfr', D_('shd', S_('kick', 'Map') + S_('sum', sum) + `<button class="tbtn mt" data-act="details"${ap(S.dt)} title="Models, timings and last actions (d)">Details</button><button class="tbtn mt" data-act="key" aria-expanded="${!!S.key}" aria-controls="keyb"><span class="lbox${S.key ? ' on' : ''}"></span>Key</button>`) +
    `<div id="keyb"${S.key ? '' : ' hidden'}>${S.key ? kHtml(f) : ''}</div><div class="mapw" id="mapw"></div>`);
  dMap();
}
function kHtml(f) {
  const states = [['running', 'running'], ['done', 'done'], ['interrupted', 'interrupted'], ['killed', 'stopped'], ['waiting', 'waiting for you'], ['queued', 'queued']],
    ico = (b) => `<svg class="emb" width="22" height="22" viewBox="-11 -11 22 22" aria-hidden="true">${b}</svg>`,
    lk = f.kind === 'swarm' ? `<span>${ico(`<circle r="4" fill="${C.ink}"/>`)}post from coordinator</span><span>${ico(`<circle r="3.6" fill="${C.bg}" stroke="${C.ink}" ${SW}="1.6"/>`)}post to coordinator</span><span>${ico(`<path d="M-4 4L4 -4" stroke="${C.bad}" ${SW}="2"${RND}/>`)}spark = 1 error</span><span>${ico(`<path d="M-10 0H10" stroke="${C.line}" ${SD}="3 5"/><path d="M-2 -4v8M2 -4v8" stroke="${C.bad}" ${SW}="1.5"/>`)}stopped link</span>` : '';
  return D_('key', JN(states, ([s, w]) => `<span>${sIc(s, { running: C.ink, done: C.mist, interrupted: C.mist, killed: C.bad, waiting: C.acc, queued: C.haze }[s])}${w}</span>`)) +
    D_('key', JN(['google', 'anthropic', 'openai', 'xai', 't3'], (l) => `<span style="color:${LAB[l].txt}">${emb(l, 'done')}${labN(l)}</span>`)) + (lk ? D_('key', lk) : '');
}