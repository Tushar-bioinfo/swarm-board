// ===== views2.js =====
let AT = 0;
function fHtml(f) {
  const P = f.posts || [], seen = store.get('seen.' + f.id, null);
  if (P.length) store.set('seen.' + f.id, P[P.length - 1].id);
  return KK(`Feed · ${P.length} posts`, 'mt20') + JN(P, (p) => {
    const a = agOf(f, p.author), nw = seen != null && p.id > seen;
    return `${nw && p.id === seen + 1 ? D_('newdiv', 'New since you looked') : ''}<div class="post bar${nw ? ' on' : ''}" data-post-row="${p.id}"><div class="by" style="color:${labC(a && a.lab)}">${E(a ? aLb(a) : p.author)}${S_('t', `#${p.id}${p.at ? ' · ' + E(p.at) : ''}`)}</div>${D_('md', md(p.text).html)}</div>`;
  });
}
const KY = [['1–9', 'Switch flow'], ['a', 'Active threads on / off'], ['j / k', 'Next / previous agent'], ['Enter', 'Open the focused node'], ['o', 'Open chat of the selected agent'], ['c', 'Compare (committee) or pick outputs'], ['d', 'Map details on / off'], ['s', 'Stats page on / off'],
  ['/', 'Find in Reader or Active'], ['f', 'Reading sheet on / off'], ['[ ]', 'Move the split 5%'], ['← →', 'Previous / next seat in Compare'], ['Esc', 'Close Find, sheet or view'], ['?', 'This list']],
  kHtml2 = () => KK('Keyboard', 'mt20') + D_('keys mt12', JN(KY, (k) => `<b>${k[0]}</b><span>${k[1]}</span>`)),
  aSec = (t) => { if (t.durS != null && isF(+t.durS)) return MX(0, +t.durS); const ts = Date.parse(t.startedAt || ''); return isF(ts) ? MX(0, (Date.now() - ts) / 1000) : null; };
function rActL() {
  clearInterval(AT); AT = 0;
  const host = $('alist');
  if (!host || S.page !== 'active') return;
  if (!AT_.loaded && AT_.loading) { host.innerHTML = NTE('Loading active threads…'); return; }
  const q = (S.aq || '').trim().toLowerCase(),
    list = (AT_.threads || []).filter((t) => !q || `${t.title || ''} ${t.project || ''} ${t.harness || ''} ${t.model || ''} ${t.last || ''} ${t.state || ''} ${t.id || ''}`.toLowerCase().includes(q)),
    live = list.filter((t) => t.isLive), uns = list.filter((t) => !t.isLive),
    linkB = (t) => { const u = safeUrl(t.href || ((t.links || []).find((l) => l.kind === 'thread' && l.href) || {}).href); return u ? `<a class="tbtn" href="${E(u)}">Open chat ↗</a>` : ''; };
  let h = '';
  if (AT_.err) h += NTE(`${E(AT_.err)} ${TB('data-act="refresh"', '↻ Retry')}`);
  else if (AT_.fallback && !AT_.threads.length && S.ld) h += NTE('Loading flows…');
  if (S.at !== 'unsettled') {
    h += KK(`Live now · ${live.length}`, 'mt20') + (live.length ? D_('acg', JN(live, (t) => {
      const sec = aSec(t), col = stCol(t), lb = t.lab || 'other', hn = HN[t.harness] || t.harness || '',
        tmr = sec != null ? `<span class="mono atm" data-live-sec="${RD(sec)}">${fDur(sec)}</span>` : S_('mono atm mist', '—'),
        ch = t.totalChildren > 0 ? S_(`apl achd${t.activeChildren > 0 ? ' on' : ''}`, `${t.activeChildren || 0}/${t.totalChildren} active`) : '',
        prj = [t.project, hn].filter(Boolean).join(' · ');
      return `<article class="ac" style="--lc:${labC(lb)}">${D_('ach', sIc(t.state || 'running', col, 18) + `<div class="act" title="${E(t.title || t.id)}">${E(t.title || t.id)}</div>` + S_('meta', E(stWord({ state: t.state || 'running' }))) + tmr)}${D_('aps', (prj ? S_('apl', E(prj)) : '') + S_('apl alb', `<i style="background:${labC(lb)}"></i>${E(t.model || labN(lb))}`) + ch)}${t.last ? D_('al cl2', E(t.last)) : ''}${D_('aa', (t.flowId ? TB(`data-inspect="${E(t.flowId)}"`, 'Inspect →') : '') + linkB(t))}</article>`;
    })) : EMP(q ? 'No live threads match your search.' : 'No live threads running right now.'));
  }
  if (S.at !== 'live') {
    h += KK(`Recent unsettled · ${uns.length}`, 'mt20') + (uns.length ? D_('ars', JN(uns, (t) => {
      const age = fAge(t.updatedAt || t.startedAt), st = stWord({ state: t.state || 'done' }), lb = t.lab || 'other',
        hasFl = !!(t.flowId && FL.some((x) => x.id === t.flowId)),
        meta = [age ? E(age) : '', t.project ? E(t.project) : '', t.model ? `<span class="mono" style="color:${labC(lb)}">${E(t.model)}</span>` : `<span style="color:${labC(lb)}">${E(labN(lb))}</span>`, E(st), t.totalChildren > 0 ? `${t.totalChildren} subagent${t.totalChildren === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
      return D_('ar', sIc(t.state || 'done', stCol(t), 16) + D_('arb', D_('act', E(t.title || t.id)) + D_('am', meta)) + D_('aa', (hasFl ? TB(`data-inspect="${E(t.flowId)}"`, 'Inspect →') : '') + linkB(t)));
    })) : EMP(q ? 'No unsettled threads match your search.' : 'No recent unsettled threads.'));
  }
  host.innerHTML = h;
  if (S.page !== 'active' || RM() || !q1('[data-live-sec]', host)) return;
  const t0 = Date.now();
  AT = setInterval(() => {
    if (S.page !== 'active' || !host.isConnected) { clearInterval(AT); AT = 0; return; }
    const d = (Date.now() - t0) / 1000;
    qa('[data-live-sec]', host).forEach((e) => { e.textContent = fDur(+e.dataset.liveSec + d); });
  }, 1000);
}
function rAct() {
  clearInterval(AT); AT = 0;
  const on = S.page === 'active', el = $('activePage');
  TG(APP, 'actp', on); el.hidden = !on;
  if (!on) return void (el.innerHTML = '');
  if (!AT_.loaded && !AT_.loading) getAct(false);
  const ts = AT_.threads || [], nA = ts.length, nL = ts.filter((t) => t.isLive).length, nU = nA - nL,
    tb = (k, l, n) => `<button class="s" data-atab="${k}"${ap(S.at === k)}>${l} ${S_('mono mist', n)}</button>`;
  el.innerHTML = D_('actl', D_('sbc', S_('ink', 'Active threads') + S_('sl', '/') + S_('sg', tb('all', 'All', nA) + tb('live', 'Live now', nL) + tb('unsettled', 'Unsettled', nU))) +
    D_('sgs', `<input class="aq" id="aq" type="search" aria-label="Filter active threads" placeholder="Search threads, project, model…" value="${E(S.aq)}">${TB('data-page="board"', '← Board')}`)) + '<div id="alist"></div>';
  rActL();
}