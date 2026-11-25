// ===== tokens2.js: cables, sparks, beads, map frame + Key =====
const cubD = (P) => `M${P[0].map(f1)}C${P[1].map(f1)} ${P[2].map(f1)} ${P[3].map(f1)}`.replace(/,/g, ' ');
const bz = (P, t) => { const u = 1 - t; return [0, 1].map((k) => u * u * u * P[0][k] + 3 * u * u * t * P[1][k] + 3 * u * t * t * P[2][k] + t * t * t * P[3][k]); };
const bzT = (P, t) => { const u = 1 - t; return [0, 1].map((k) => 3 * u * u * (P[1][k] - P[0][k]) + 6 * u * t * (P[2][k] - P[1][k]) + 3 * t * t * (P[3][k] - P[2][k])); };
function cable(g, a, o) {
  o = o || {};
  const L = LB(a.lab), st = a.state, id = esc(a.id), w = o.w || 1.5, d = Array.isArray(g) ? cubD(g) : g, stop = isStop(a);
  let col = L.base, op = .55, dash = `.01 ${f1(Math.max(3, 1.5 * w))}`, sw = w + .4;
  if (st === 'running') { op = .3; dash = ''; sw = w; } else if (st === 'interrupted') { op = .55; dash = '5 4'; sw = w; } else if (stop) { col = C.line; op = .8; dash = '3 5'; sw = w; } else if (st === 'queued') { col = C.line; op = .45; }
  if (o.op) op = o.op;
  const k = (o.cls ? ' ' + o.cls : '') + (o.on ? ' wit' : '') + (o.dim ? ' dim' : '');
  let s = `<path class="cab${k}" data-to="${id}" d="${d}" fill="none" stroke="${col}" stroke-width="${f1(sw)}" stroke-opacity="${op}"${dash ? ` stroke-dasharray="${dash}"` : ''} stroke-linecap="round"/>`;
  if (st === 'running' && !o.nomarch) s += `<path class="march${o.cls ? ' ' + o.cls : ''}" d="${d}" stroke="${L.hi}"/>`;
  if (stop && Array.isArray(g)) {
    const p = bz(g, .86), t = bzT(g, .86), n = Math.hypot(t[0], t[1]) || 1, ux = t[0] / n, uy = t[1] / n;
    s += `<path d="${[-2.5, 2.5].map((e) => `M${f1(p[0] + ux * e - uy * 4)} ${f1(p[1] + uy * e + ux * 4)}l${f1(uy * 8)} ${f1(-ux * 8)}`).join('')}" stroke="${C.bad}" stroke-width="1.5"/>`;
  }
  return s;
}
function sparks(a, x, y, R) {
  const n = errN(a), m = Math.min(12, n);
  if (!m) return '';
  const ang = (i) => (m === 1 ? 270 : 215 + i * 110 / (m - 1)) * Math.PI / 180, pt = (g, rr) => f1(x + Math.cos(g) * rr) + ' ' + f1(y + Math.sin(g) * rr);
  let s = '';
  for (let i = 0; i < m; i++) s += `<path class="sp" d="M${pt(ang(i), R + 4)}L${pt(ang(i), R + 10)}" stroke="${C.bad}" stroke-width="2" stroke-linecap="round"/><circle cx="${pt(ang(i), R + 13.5).replace(' ', '" cy="')}" r="1" fill="${C.bad}" fill-opacity=".7"/>`;
  const a0 = m === 1 ? ang(0) - .3 : ang(0), a1 = m === 1 ? ang(0) + .3 : ang(m - 1);
  s += `<path d="M${pt(a0, R + 7.5)}A${R + 7.5} ${R + 7.5} 0 0 1 ${pt(a1, R + 7.5)}" stroke="transparent" stroke-width="12" fill="none"/>`;
  if (n > 12) s += `<text x="${x}" y="${f1(y - R - 26)}" text-anchor="middle" font-size="15" fill="${C.bad}">+${n - 12}</text>`;
  return `<g class="spk" data-agent="${esc(a.id)}" ${BATR(`${n} error${n > 1 ? 's' : ''} on ${aName(a)}`)}>${s}</g>`;
}
function bead(p, x, y, to, out) {
  const open = S.view === 'output' && S.arg === 'post-' + p.id;
  return `<g class="bd" data-post="${p.id}" data-to="${esc(to)}" ${BATR(`Board post #${p.id}, ${p.author} to ${(p.mentions || []).join(', ')}`)}><g transform="translate(${f1(x)} ${f1(y)})"><circle r="9" fill="transparent"/>${SC(7, C.ink, ' stroke-opacity=".22" stroke-dasharray=".01 2.2" stroke-linecap="round" stroke-width="1.3"')}` +
    (out ? `<circle class="bb" r="4" fill="${C.ink}"/>` : `<circle class="bb" r="3.6" fill="${C.bg}" stroke="${C.ink}" stroke-width="1.6"/>`) + (open ? SC(8, C.ink) : '') + '</g></g>';
}
function maxDepth(f) {
  let mx = 0;
  (f.agents || []).forEach((a) => {
    let d = 0, x = a, seen = new Set([a.id]);
    while (x && x.parent && !seen.has(x.parent) && d < 12) {
      seen.add(x.parent);
      x = agentOf(f, x.parent);
      if (x) d++;
    }
    if (d > mx) mx = d;
  });
  return Math.max(1, mx);
}
function renderMapFrame() {
  const f = flow();
  if (!f) { $('mapfr').innerHTML = ''; return; }
  const ag = f.agents || [], n = ag.length, r = rootOf(f);
  const sum = !n ? '0 agents'
    : f.kind === 'tree' && r && kids(f, r.id).some((l) => kids(f, l.id).length)
      ? (() => { const L = kids(f, r.id), Wk = ag.filter((a) => a.role === 'worker'); return `${L.length} leads · ${Wk.length} workers · ${Wk.filter((a) => a.state === 'running').length} running · ${Wk.filter((a) => a.state === 'done').length} done`; })()
    : f.kind === 'swarm' ? `${n} agents · ${(f.workstreams || []).length} lanes`
    : f.kind === 'committee' ? `${n} seats · one per lab`
    : `${n} agents · ${maxDepth(f)} level${maxDepth(f) === 1 ? '' : 's'}`;
  $('mapfr').innerHTML = `<div class="shd"><span class="kick">Map</span><span class="sum">${sum}</span><button class="tbtn mt" data-act="details" aria-pressed="${!!S.details}" title="Models, timings and last actions (d)">Details</button><button class="tbtn mt" data-act="key" aria-expanded="${!!S.key}" aria-controls="keyb"><span class="lbox${S.key ? ' on' : ''}"></span>Key</button></div>
    <div id="keyb"${S.key ? '' : ' hidden'}>${S.key ? keyHTML(f) : ''}</div>
    <div class="mapw" id="mapw"></div>`;
  drawMap();
}
function keyHTML(f) {
  const states = [['running', 'running'], ['done', 'done'], ['interrupted', 'interrupted'], ['killed', 'stopped'], ['waiting', 'waiting for you'], ['queued', 'queued']];
  const ico = (b) => `<svg class="emb" width="22" height="22" viewBox="-11 -11 22 22" aria-hidden="true">${b}</svg>`;
  const lanesKey = f.kind === 'swarm' ? `<span>${ico(`<circle r="4" fill="${C.ink}"/>`)}post from coordinator</span><span>${ico(`<circle r="3.6" fill="${C.bg}" stroke="${C.ink}" stroke-width="1.6"/>`)}post to coordinator</span>` +
    `<span>${ico(`<path d="M-4 4L4 -4" stroke="${C.bad}" stroke-width="2" stroke-linecap="round"/>`)}spark = 1 error</span><span>${ico(`<path d="M-10 0H10" stroke="${C.line}" stroke-dasharray="3 5"/><path d="M-2 -4v8M2 -4v8" stroke="${C.bad}" stroke-width="1.5"/>`)}stopped link</span>` : '';
  return `<div class="key">${states.map(([s, w]) => `<span>${sIcon(s, { running: C.ink, done: C.mist, interrupted: C.mist, killed: C.bad, waiting: C.acc, queued: C.haze }[s])}${w}</span>`).join('')}</div>
    <div class="key">${['google', 'anthropic', 'openai', 'xai', 't3'].map((l) => `<span style="color:${LAB[l].txt}">${emb(l, 'done')}${labN(l)}</span>`).join('')}</div>${lanesKey ? `<div class="key">${lanesKey}</div>` : ''}`;
}
