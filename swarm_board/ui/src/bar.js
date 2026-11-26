// ===== bar.js =====
function swRows(vis) {
  const q = (S.sq || '').trim().toLowerCase(),
    rs = vis.filter((i) => !q || `${FL[i].title} ${FL[i].project} ${FL[i].harness} ${FL[i].id}`.toLowerCase().includes(q));
  if (!rs.length) return EMP('No matching flows.');
  return JN(rs, (i) => {
    const g = FL[i], m = [g.project, HN[g.harness] || g.harness, g.age].filter(Boolean).join(' · ');
    return `<button role="option" aria-selected="${i === S.fi}" data-flow="${i}">${flIc(g, 16)}${S_('mono mist', i + 1)}${S_('sw-t', E(g.title))}${S_('sw-m', E(m))}</button>`;
  });
}
const rSwl = () => IH('swlList', swRows(visFl()));
function rBar() {
  const w = $('app').dataset.w, f = flow(), n = allN().length, vis = visFl(), lc = lvCnt(),
    pg = (k, l) => `<button class="pgb" data-page="${k}"${ap(S.page === k)}>${l}</button>`,
    cl = { live: ['●', 'Live'], reconnecting: ['○', 'Reconnecting…'], offline: ['○', 'Offline'], connecting: ['○', 'Connecting…'] }[S.conn] || ['○', 'Connecting…'],
    snap = D.snapshot ? ` · ${E(D.snapshot)}` : '', actL = 'Active' + (lc > 0 ? `<b class="acnt">${lc}</b>` : ''),
    right = D_('fbr', S_('pgs', pg('board', 'Board') + pg('active', actL) + pg('stats', 'Stats')) +
      `<button class="needs" data-go="needs">Needs you<b>${n}</b></button>` +
      `<span class="conn" data-conn="${E(S.conn)}">${S_('cdot', cl[0])}${cl[1]}${snap}</span>` +
      `<button class="tbtn rfr" data-act="refresh" title="Refresh flows" aria-label="Refresh flows">↻</button>`);
  let left = '';
  if (!FL.length) left = D_('sw', S_('t mist', S.ld ? 'Loading flows…' : '0 flows'));
  else if (!vis.length) left = D_('sw', S_('t mist', '0 matching flows') + TB('data-act="clrfilt"', 'Clear filters'));
  else if (w === 'wide') {
    const pos = MX(0, vis.indexOf(S.fi)), show = vis.length > 6 ? vis.slice(0, 9) : vis;
    if (vis.length > 6 && pos >= 9 && !show.includes(S.fi)) show.push(S.fi);
    const tabs = `<div class="tabs" role="tablist" aria-label="Flows">` + JN(show, (i, k) => {
      const g = FL[i], rk = vis.indexOf(i) + 1;
      return `<button class="tab" role="tab" aria-selected="${i === S.fi}" data-flow="${i}" title="${E(g.title)}">${flIc(g, 16)}${S_('k', k < 9 ? k + 1 : rk)}${S_('t', E(g.title))}</button>`;
    }) + `</div>`,
      sw = vis.length > 6 ? D_('fsw', TB('data-step="-1" aria-label="Previous flow"', '‹') + S_('mono mist sw-n', `${pos + 1} / ${vis.length}`) + TB('data-step="1" aria-label="Next flow"', '›') + TB(`data-act="swl" aria-expanded="${!!S.sl}"`, 'All ▾')) : '';
    left = tabs + sw;
  } else {
    const pos = MX(0, vis.indexOf(S.fi)), cur = f || FL[vis[0]];
    left = D_('sw', TB('data-step="-1" aria-label="Previous flow"', '‹') + S_('mono mist sw-n', `${pos + 1} / ${vis.length}`) +
      `<button class="tbtn fx" data-act="swl" aria-expanded="${!!S.sl}" style="color:var(--ink);min-width:0">${flIc(cur, 16)}${S_('t', E(cur.title))} ▾</button>` + TB('data-step="1" aria-label="Next flow"', '›'));
  }
  IH('fbar', left + right);
  const fl = S.sl && vis.length ? D_('swl', `<input id="swq" type="search" aria-label="Filter flows" placeholder="Filter ${vis.length} flows by title, project, harness…" value="${E(S.sq)}"><div class="swl-list" id="swlList" role="listbox">${swRows(vis)}</div>`) : '',
    tg = (k, v, lab) => TB(`data-filt="${k}" data-val="${E(v)}"${ap(S.filt[k] === v)}`, lab),
    hs = [...new Set(FL.map((x) => x.harness).filter(Boolean))], ls = [...new Set(FL.flatMap((x) => (x.agents || []).map((a) => a.lab)))].filter((l) => l && l !== 't3'),
    liveN = FL.filter(isFL).length,
    ql = liveN > 0 || S.filt.live ? `<button class="tbtn qlive" data-filt="live" data-val="true"${ap(S.filt.live === true)}>${S_('ldot', '●')}Live only${liveN ? `<b class="acnt">${liveN}</b>` : ''}</button>` : '';
  IH('filt', fl + (FL.length ? `<details class="lfold" ${S.fopen ? 'open' : ''} data-fold="fopen"><summary>${LX}Filter</summary>${D_('row', tg('live', true, 'Live only') + (hs.length ? S_('kick', 'Harness') + JN(hs, (h) => tg('h', h, HN[h] || h)) : '') + (ls.length ? S_('kick', 'Lab') + JN(ls, (l) => tg('lab', l, labN(l))) : ''))}</details>${ql}` : ''));
}
function hdSt(f) {
  const a = (f && f.agents) || [], c = (p) => a.filter(p).length,
    ps = [['all', a.length, 'agents'], ['running', c((x) => x.state === 'running'), 'running'], ['waiting', c((x) => x.state === 'waiting'), 'waiting'],
      ['queued', c((x) => x.state === 'queued'), 'queued'], ['killed', c(isStop), 'stopped'], ['done', c((x) => x.state === 'done'), 'done'],
      ['interrupted', c((x) => x.state === 'interrupted'), 'interrupted']].filter((p) => p[0] === 'all' || p[1]);
  return JN(ps, (p) => `<button data-dim="${p[0]}"${ap(S.dm === p[0])}><b>${p[1]}</b> ${p[2]}</button>`, '<span>·</span>');
}
function rHd() {
  const f = flow();
  if (!f) {
    IH('fhead', S.ld ? `${KK('Board')}<h1>Loading flows…</h1>` : S.le ? `${KK('Board')}<h1>Could not load flows</h1><p class="meta">${E(S.le)}</p>${D_('acts', TB('data-act="refresh"', '↻ Retry'))}`
      : `${KK('Board')}<h1>No active or recent multi-agent flows found yet.</h1><p class="meta">Start a T3 delegation, Antigravity swarm, committee, or CLI subagent run to inspect it here.</p>`);
    return;
  }
  IH('fhead', `${KK(E(f.project || HN[f.harness] || f.harness || ''))}<h1>${E(f.title)}</h1>${f.goal ? `<p class="goal md" title="${E(f.goal)}">${inl(f.goal)}</p>` : ''}${D_('stats', hdSt(f))}${f.decision ? D_('dec', S_('kick', 'Decision') + S_('w', E((f.decision.qualifier ? f.decision.qualifier + ' ' : '') + (f.decision.word || ''))) + TB('data-go="compare"', 'Read the verdicts →')) : ''}`);
}