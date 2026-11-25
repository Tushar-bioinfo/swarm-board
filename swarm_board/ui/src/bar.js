// ===== bar.js: flow bar, searchable flow switcher, filter, flow head =====
function swlRowsHTML(vis) {
  const q = (S.swq || '').trim().toLowerCase();
  const rows = vis.filter((i) => {
    if (!q) return true;
    const g = FL[i];
    return `${g.title} ${g.project} ${g.harness} ${g.id}`.toLowerCase().includes(q);
  });
  if (!rows.length) return EMP('No matching flows.');
  return rows.map((i) => {
    const g = FL[i], meta = [g.project, HARN[g.harness] || g.harness, g.age].filter(Boolean).join(' · ');
    return `<button role="option" aria-selected="${i === S.fi}" data-flow="${i}">${flowIcon(g, 16)}<span class="mono mist">${i + 1}</span><span class="sw-t">${esc(g.title)}</span><span class="sw-m">${esc(meta)}</span></button>`;
  }).join('');
}
function renderSwlList() {
  const el = $('swlList');
  if (el) el.innerHTML = swlRowsHTML(visibleFlows());
}
function renderBar() {
  const w = $('app').dataset.w, f = flow(), n = allNeeds().length, vis = visibleFlows();
  const pg = (k, l) => `<button class="pgb" data-page="${k}" aria-pressed="${S.page === k}">${l}</button>`;
  const cl = { live: ['●', 'Live'], reconnecting: ['○', 'Reconnecting…'], offline: ['○', 'Offline'], connecting: ['○', 'Connecting…'] }[S.conn] || ['○', 'Connecting…'];
  const snap = D.snapshot ? ` · ${esc(D.snapshot)}` : '';
  const right = `<div class="fbr"><span class="pgs">${pg('board', 'Board')}${pg('stats', 'Stats')}</span><button class="needs" data-go="needs">Needs you<b>${n}</b></button><span class="conn" data-conn="${esc(S.conn)}"><span class="cdot">${cl[0]}</span>${cl[1]}${snap}</span><button class="tbtn rfr" data-act="refresh" title="Refresh flows" aria-label="Refresh flows">↻</button></div>`;
  let left = '';
  if (!FL.length) {
    left = `<div class="sw"><span class="t mist">${S.loading ? 'Loading flows…' : '0 flows'}</span></div>`;
  } else if (!vis.length) {
    left = `<div class="sw"><span class="t mist">0 matching flows</span><button class="tbtn" data-act="clrfilt">Clear filters</button></div>`;
  } else if (w === 'wide') {
    const pos = Math.max(0, vis.indexOf(S.fi)), show = vis.length > 6 ? vis.slice(0, 9) : vis;
    if (vis.length > 6 && pos >= 9 && !show.includes(S.fi)) show.push(S.fi);
    const tabs = `<div class="tabs" role="tablist" aria-label="Flows">` + show.map((i, k) => {
      const g = FL[i], rk = vis.indexOf(i) + 1;
      return `<button class="tab" role="tab" aria-selected="${i === S.fi}" data-flow="${i}" title="${esc(g.title)}">${flowIcon(g, 16)}<span class="k">${k < 9 ? k + 1 : rk}</span><span class="t">${esc(g.title)}</span></button>`;
    }).join('') + `</div>`;
    const sw = vis.length > 6 ? `<div class="fsw"><button class="tbtn" data-step="-1" aria-label="Previous flow">‹</button><span class="mono mist sw-n">${pos + 1} / ${vis.length}</span><button class="tbtn" data-step="1" aria-label="Next flow">›</button><button class="tbtn" data-act="swl" aria-expanded="${!!S.swl}">All ▾</button></div>` : '';
    left = tabs + sw;
  } else {
    const pos = Math.max(0, vis.indexOf(S.fi)), cur = f || FL[vis[0]];
    left = `<div class="sw"><button class="tbtn" data-step="-1" aria-label="Previous flow">‹</button><span class="mono mist sw-n">${pos + 1} / ${vis.length}</span>
      <button class="tbtn fx" data-act="swl" aria-expanded="${!!S.swl}" style="color:var(--ink);min-width:0">${flowIcon(cur, 16)}<span class="t">${esc(cur.title)}</span> ▾</button>
      <button class="tbtn" data-step="1" aria-label="Next flow">›</button></div>`;
  }
  $('fbar').innerHTML = left + right;
  const fl = S.swl && vis.length ? `<div class="swl"><input id="swq" type="search" aria-label="Filter flows" placeholder="Filter ${vis.length} flows by title, project, harness…" value="${esc(S.swq)}"><div class="swl-list" id="swlList" role="listbox">${swlRowsHTML(vis)}</div></div>` : '';
  const tg = (k, v, lab) => `<button class="tbtn" aria-pressed="${S.filt[k] === v}" data-filt="${k}" data-val="${esc(v)}">${lab}</button>`;
  const hs = [...new Set(FL.map((x) => x.harness).filter(Boolean))], ls = [...new Set(FL.flatMap((x) => (x.agents || []).map((a) => a.lab)))].filter((l) => l && l !== 't3');
  $('filt').innerHTML = fl + (FL.length ? `<details class="lfold" ${S.fopen ? 'open' : ''} data-fold="fopen"><summary>${LX}Filter</summary>
    <div class="row">${tg('live', true, 'Live only')}${hs.length ? `<span class="kick">Harness</span>${hs.map((h) => tg('h', h, HARN[h] || h)).join('')}` : ''}${ls.length ? `<span class="kick">Lab</span>${ls.map((l) => tg('lab', l, labN(l))).join('')}` : ''}</div></details>` : '');
}
function stats(f) {
  const a = (f && f.agents) || [], c = (p) => a.filter(p).length;
  const parts = [['all', a.length, 'agents'], ['running', c((x) => x.state === 'running'), 'running'], ['killed', c(isStop), 'stopped'],
    ['done', c((x) => x.state === 'done'), 'done'], ['interrupted', c((x) => x.state === 'interrupted'), 'interrupted']].filter((p) => p[0] === 'all' || p[1]);
  return parts.map((p) => `<button data-dim="${p[0]}" aria-pressed="${S.dim === p[0]}"><b>${p[1]}</b> ${p[2]}</button>`).join('<span>·</span>');
}
function renderHead() {
  const f = flow();
  if (!f) {
    if (S.loading) $('fhead').innerHTML = `<div class="kick">Board</div><h1>Loading flows…</h1>`;
    else if (S.loadErr) $('fhead').innerHTML = `<div class="kick">Board</div><h1>Could not load flows</h1><p class="meta">${esc(S.loadErr)}</p><div class="acts"><button class="tbtn" data-act="refresh">↻ Retry</button></div>`;
    else $('fhead').innerHTML = `<div class="kick">Board</div><h1>No active or recent multi-agent flows found yet.</h1><p class="meta">Start a T3 delegation, Antigravity swarm, committee, or CLI subagent run to inspect it here.</p>`;
    return;
  }
  $('fhead').innerHTML = `<div class="kick">${esc(f.project || HARN[f.harness] || f.harness || '')}</div><h1>${esc(f.title)}</h1>${f.goal ? `<p class="goal md" title="${esc(f.goal)}">${inl(f.goal)}</p>` : ''}
    <div class="stats">${stats(f)}</div>
    ${f.decision ? `<div class="dec"><span class="kick">Decision</span><span class="w">${esc((f.decision.qualifier ? f.decision.qualifier + ' ' : '') + (f.decision.word || ''))}</span><button class="tbtn" data-go="compare">Read the verdicts →</button></div>` : ''}`;
}
