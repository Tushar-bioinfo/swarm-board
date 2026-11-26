// ===== views.js =====
const trc = (T) => { const N = T.filter((t) => typeof t[1] === 'number'), m = MX(1, ...N.map((t) => t[1])); return D_('trace', JN(N, (t) => `${S_('mono', E(t[0]))}<i style="width:${(100 * t[1] / m).toFixed(0)}%"></i>${S_('num', t[1])}`)); },
  pOut = (f, p) => ({ id: 'post-' + p.id, kind: 'post', title: `Post #${p.id}`, by: p.author, at: p.at, post: p }),
  RK = { verdict: 0, markdown: 1, result: 1, brief: 1, table: 2, json: 2, code: 3, file: 3, video: 4, audio: 4, transcript: 5 };
function gOut(f, id, view) {
  if (!f || !id) return null;
  if (id.startsWith('post-')) { const p = (f.posts || []).find((x) => 'post-' + x.id === id); return p && pOut(f, p); }
  if (view === 'file' || id.startsWith('file:')) {
    const raw = id.startsWith('file:') ? id.slice(5) : id, m = raw.match(/^(.*):(\d+(?:-\d+)?)$/), rel = m ? m[1] : raw, ln = m ? m[2] : '1',
      [a, b] = ln.split('-').map(Number), cf = f.cited && f.cited.files && f.cited.files[rel];
    return { id: 'file:' + rel + ':' + ln, arg: id, kind: 'cfile', title: rel.split('/').pop() || rel, path: rel, line: a || 1, cf, hit: [a || 1, b || a || 1], by: null };
  }
  return oOf(f, id);
}
function oRow(f, o, open) {
  const a = agOf(f, o.by), dir = o.path && o.path.includes('/') ? o.path.replace(/\/[^/]*$/, '').replace(/^.*\/(?=[^/]+\/[^/]+$)/, '') : '';
  let n = fKB(o.size);
  if (o.kind === 'table' && typeof o.body === 'string') try { const j = Jp(o.body), tk = o.tableKey || OK(j).find((k) => isArr(j[k])); if (tk && isArr(j[tk])) n = j[tk].length + ' rows'; } catch (e) {}
  const picking = S.pk != null, on = picking && S.pk.includes(o.id),
    mt = [E(dir), n, a ? `<span style="color:${labC(a.lab)}">${E(aLb(a))}</span>` : '', E(o.at)].filter(Boolean).join(' · ');
  return `<li class="bar${open ? ' on' : ''}"><button class="row1" data-out="${E(o.id)}"${picking ? ap(on) : ''}>${S_('ic', picking ? `<span class="lbox${on ? ' on' : ''}"></span>` : IC[o.kind] || '▤')}<span>${S_('nm', E(o.title))}<br>${S_('mt', mt)}</span></button></li>`;
}
function rOuts() {
  const f = flow();
  if (!f) { IH('outs', ''); return; }
  const outs = (f.outputs || []).slice().sort((a, b) => (RK[a.kind] || 5) - (RK[b.kind] || 5)), P = f.posts || [], cur = S.view === 'output' ? S.arg : null;
  let h = D_('shd', S_('kick', 'Outputs') + S_('sum', outs.length + P.length) +
    (outs.length > 1 ? (S.pk ? TB(`data-act="cmpgo"${S.pk.length < 2 ? ' disabled' : ''}`, `Compare ${S.pk.length} →`) + TB('data-act="cmpx"', 'Cancel') : TB('data-act="cmppick"', 'Compare')) : '')) + '<ul class="rows">';
  if (f.decision) h += `<li class="bar${S.view === 'compare' ? ' on' : ''}"><button class="row1" data-go="compare">${S_('ic', '⚖')}<span>${S_('nm', 'Verdicts, side by side')}<br>${S_('mt', `${(f.agents || []).length} seats · ${E((f.decision.qualifier ? f.decision.qualifier + ' ' : '') + (f.decision.word || ''))}`)}</span></button></li>`;
  h += JN(outs, (o) => oRow(f, o, o.id === cur)) + '</ul>';
  if (P.length) h += `<details class="lfold" data-fold="posts" ${S.posts ? 'open' : ''}><summary>${LX}${P.length} board posts</summary><ul class="rows">${JN(P, (p) => oRow(f, pOut(f, p), 'post-' + p.id === cur))}</ul></details>`;
  if (!outs.length && !P.length) h += EMP('No outputs recorded yet.');
  IH('outs', h);
}
function nHtml(items, all) {
  if (!items.length) return EMP(`Nothing is waiting on you${all ? '' : ' in this flow'}.`);
  return JN(items, (it) => {
    const nm = JN(it.ag.slice(0, 3), (a) => `<button class="ment" data-agent="${E(a.id)}" data-flowid="${E(it.flow.id)}" style="color:${labC(a.lab)}">${E(trunc(aLb(a), 26))}</button>`, ', '),
      r = rOf(it.flow), acts = JN(it.acts, (k) => (k === 'errors' && it.ag[0] ? TB(`data-agent="${E(it.ag[0].id)}" data-flowid="${E(it.flow.id)}"`, 'Read errors')
        : k === 'chat' ? it.ag.map((a) => { const t = (a.links || []).find((l) => l.kind === 'thread') || ((r && r.links) || []).find((l) => l.kind === 'thread'), u = t && safeUrl(t.href); return u ? `<a class="tbtn" href="${E(u)}">Open chat ↗</a>` : ''; }).find(Boolean) || ''
        : k === 'compare' ? TB(`data-go="compare" data-flowid="${E(it.flow.id)}"`, 'Read side by side') : ''));
    return D_('nitem', sIc(it.st, it.st === 'killed' ? C.bad : it.st === 'waiting' || it.st === 'cond' ? C.acc : C.mist) +
      D_('', `<p>${all ? S_('mist', E(it.flow.title) + ' · ') : ''}${E(it.text)}${nm ? ' — ' + nm : ''}</p>` + D_('acts', acts + TB(`data-done="${E(it.key)}"`, 'Done'))));
  });
}
function oHtml(f) {
  const latest = (f.posts || []).slice(-5).reverse().map((p) => ({ by: p.author, at: '#' + p.id + (p.at ? ' · ' + p.at : ''), text: p.text }))
      .concat(f.posts && f.posts.length ? [] : (f.agents || []).filter((a) => a.result).map((a) => ({ by: a.id, at: 'result', text: a.result }))),
    decM = f.decision && f.decision.source ? String(f.decision.source).match(/^(.*?)(?::(\d+))?$/) : null,
    decSrc = decM ? `<p class="note">From <button class="cite" data-flow="${E(f.id)}" data-p="${E(decM[1])}" data-a="${decM[2] || 1}" data-b="${decM[2] || 1}">${E(f.decision.source)}</button></p>` : '';
  return KK('Needs you', 'mt20') + nHtml(nOf(f)) +
    (f.decision ? KK('Decision', 'vk') + `<div class="dec" style="margin-top:4px">${S_('w', E((f.decision.qualifier ? f.decision.qualifier + ' ' : '') + (f.decision.word || '')))}${TB('data-go="compare"', 'Read the verdicts →')}</div>${decSrc}` : '') +
    KK('Latest', 'vk') + (latest.length ? JN(latest, (x) => { const a = agOf(f, x.by); return D_('post', `<div class="by" style="color:${labC(a && a.lab)}">${E(a ? aLb(a) : x.by)}${S_('t', E(x.at))}</div><div class="md" style="font-size:17px">${md(x.text).html}</div>`); }) : EMP('No posts or results yet.')) +
    D_('acts', ((f.posts || []).length ? TB('data-go="feed"', `All ${f.posts.length} posts →`) : '') + TB('data-page="stats"', 'Stats →'));
}
let TT = 0;
function tTick(a) {
  clearInterval(TT);
  if (!a || a.state !== 'running' || a.durS == null || RM()) return;
  const t0 = Date.now(), base = Number(a.durS) || 0;
  TT = setInterval(() => { const e = q1('.tele [data-live]'); if (!e) return clearInterval(TT); e.textContent = fDur(base + (Date.now() - t0) / 1000); }, 1000);
}
const tLnk = (a) => { const L = (a && a.links) || []; return L.find((l) => l.kind === 'thread' && l.id) || L.find((l) => l.kind === 'session' && l.id) || null; };
function aHtml(f, a) {
  const tok = a.tokens ? `${fTok(a.tokens.in)} in · ${fTok(a.tokens.cache)} cached · ${fTok(a.tokens.out)} out` : '—';
  tTick(a);
  const outs = (a.outputIds || []).map((id) => gOut(f, id)).filter(Boolean), E_ = a.errors || [], T = a.trace || [],
    res = a.result || (a.verdict ? null : a.last), wsObj = a.workstream ? (f.workstreams || []).find((w) => w.id === a.workstream) : null, tid = tLnk(a),
    vk = (t, b) => KK(t, 'vk') + b;
  return KK(`Agent · ${E(a.role)}${wsObj ? ' · ' + E(wsObj.name) : ''}`) +
    `<h2 class="ah2">${E(aTk(a))}</h2>` +
    D_('fx fxw', `${emb(a.lab, a.state)}<span class="labl" style="color:${labC(a.lab)}">${E(labN(a.lab))}</span> <span class="mono mist" style="font-size:15px">${E(a.model || '')}</span>`) +
    `<div class="st" style="color:${isStop(a) ? C.bad : C.ink}">${sIc(a.state, stCol(a))}${E(stWord(a))}${a.verdict ? ` · ✓ ${E(a.verdict)}` : ''}</div>` +
    D_('tele', D_('', KK('Elapsed') + `<b${a.state === 'running' ? ' data-live' : ''}>${a.durS != null ? fDur(a.durS) : '—'}</b>`) + D_('', KK('Tokens') + `<b>${tok}</b>`) + D_('', KK('Est. cost') + `<b>${fUsd(a.costUsd, a.costEstimated)}</b>`)) +
    D_('fr', KK('Result') + (res ? D_('md prose', md(res).html) : a.verdict && a.outputIds && a.outputIds[0] ? TB(`data-out="${E(a.outputIds[0])}"`, 'Read the verdict →') : EMP('No result recorded yet.'))) +
    vk('Chat &amp; session', lBlk(a)) + (outs.length ? vk('Outputs', `<ul class="rows">${JN(outs, (o) => oRow(f, o, false))}</ul>`) : '') +
    (E_.length ? `<details class="lfold vk" ${S.er ? 'open' : ''} id="errs"><summary>${LX}${errN(a)} error${errN(a) > 1 ? 's' : ''} · ${E(String(E_[0].kind || '').replace(/_/g, ' '))}</summary><table class="tbl"><tbody>${JN(E_, (e) => `<tr><td class="mono">${E(e.at || '')}</td><td>${E(e.kind)} ×${e.count}</td></tr>`)}</tbody></table>${D_('md', JN(E_, (e) => `<pre class="err"><code>${E(e.msg)}</code></pre>`))}</details>` : '') +
    (T.length ? `<details class="lfold vk"><summary>${LX}Activity · ${T.reduce((s, t) => s + (typeof t[1] === 'number' ? t[1] : 0), 0)} traced calls</summary>${trc(T)}</details>` : '') +
    `<details class="lfold vk" data-msgs="${E(tid ? tid.id : '')}"><summary>${LX}Last 5 messages</summary>${D_('msgbox', NTE(tid ? 'Expand to load messages.' : 'No thread or conversation linked for this agent.'))}</details>`;
}