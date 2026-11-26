// ===== views.js: outputs list, side router views, agent telemetry =====
const trc = (T) => { const m = Math.max(1, ...T.map((t) => t[1])); return `<div class="trace">${T.map((t) => `<span class="mono">${esc(t[0])}</span><i style="width:${(100 * t[1] / m).toFixed(0)}%"></i><span class="num">${t[1]}</span>`).join('')}</div>`; };
function postOut(f, p) { return { id: 'post-' + p.id, kind: 'post', title: `Post #${p.id}`, by: p.author, at: p.at, post: p }; }
function getOut(f, id, view) {
  if (!f || !id) return null;
  if (id.startsWith('post-')) { const p = (f.posts || []).find((x) => 'post-' + x.id === id); return p && postOut(f, p); }
  if (view === 'file' || id.startsWith('file:')) {
    const raw = id.startsWith('file:') ? id.slice(5) : id;
    const m = raw.match(/^(.*):(\d+(?:-\d+)?)$/);
    const rel = m ? m[1] : raw, ln = m ? m[2] : '1';
    const [a, b] = ln.split('-').map(Number), cf = f.cited && f.cited.files && f.cited.files[rel];
    return { id: 'file:' + rel + ':' + ln, arg: id, kind: 'cfile', title: rel.split('/').pop() || rel, path: rel, line: a || 1, cf, hit: [a || 1, b || a || 1], by: null };
  }
  return outOf(f, id);
}
const RANK = { verdict: 0, markdown: 1, result: 1, brief: 1, table: 2, json: 2, code: 3, file: 3, video: 4, audio: 4, transcript: 5 };
function outRow(f, o, open) {
  const a = agentOf(f, o.by), dir = o.path && o.path.includes('/') ? o.path.replace(/\/[^/]*$/, '').replace(/^.*\/(?=[^/]+\/[^/]+$)/, '') : '';
  let n = fmtKB(o.size);
  if (o.kind === 'table' && typeof o.body === 'string') {
    try { const j = JSON.parse(o.body), tk = o.tableKey || Object.keys(j).find((k) => Array.isArray(j[k])); if (tk && Array.isArray(j[tk])) n = j[tk].length + ' rows'; } catch (e) { /* ignore */ }
  }
  const picking = S.pick != null, on = picking && S.pick.includes(o.id);
  const mt = [esc(dir), n, a ? `<span style="color:${labC(a.lab)}">${esc(aLabel(a))}</span>` : '', esc(o.at)].filter(Boolean).join(' · ');
  return `<li class="bar${open ? ' on' : ''}"><button class="row1" data-out="${esc(o.id)}" ${picking ? `aria-pressed="${on}"` : ''}><span class="ic">${picking ? `<span class="lbox${on ? ' on' : ''}"></span>` : ICON[o.kind] || '▤'}</span><span><span class="nm">${esc(o.title)}</span><br><span class="mt">${mt}</span></span></button></li>`;
}
function renderOuts() {
  const f = flow();
  if (!f) { $('outs').innerHTML = ''; return; }
  const outs = (f.outputs || []).slice().sort((a, b) => (RANK[a.kind] || 5) - (RANK[b.kind] || 5)), P = f.posts || [];
  const cur = S.view === 'output' ? S.arg : null;
  let h = `<div class="shd"><span class="kick">Outputs</span><span class="sum">${outs.length + P.length}</span>${outs.length > 1 ? (S.pick ? `<button class="tbtn" data-act="cmpgo" ${S.pick.length < 2 ? 'disabled' : ''}>Compare ${S.pick.length} →</button><button class="tbtn" data-act="cmpx">Cancel</button>` : `<button class="tbtn" data-act="cmppick">Compare</button>`) : ''}</div><ul class="rows">`;
  if (f.decision) h += `<li class="bar${S.view === 'compare' ? ' on' : ''}"><button class="row1" data-go="compare"><span class="ic">⚖</span><span><span class="nm">Verdicts, side by side</span><br><span class="mt">${f.agents.length} seats · ${esc((f.decision.qualifier ? f.decision.qualifier + ' ' : '') + (f.decision.word || ''))}</span></span></button></li>`;
  h += outs.map((o) => outRow(f, o, o.id === cur)).join('') + '</ul>';
  if (P.length) h += `<details class="lfold" data-fold="posts" ${S.posts ? 'open' : ''}><summary>${LX}${P.length} board posts</summary><ul class="rows">${P.map((p) => outRow(f, postOut(f, p), 'post-' + p.id === cur)).join('')}</ul></details>`;
  if (!outs.length && !P.length) h += EMP('No outputs recorded yet.');
  $('outs').innerHTML = h;
}
function needsHTML(items, all) {
  if (!items.length) return EMP(`Nothing is waiting on you${all ? '' : ' in this flow'}.`);
  return items.map((it) => {
    const nm = it.ag.slice(0, 3).map((a) => `<button class="ment" data-agent="${esc(a.id)}" data-flowid="${esc(it.flow.id)}" style="color:${labC(a.lab)}">${esc(trunc(aLabel(a), 26))}</button>`).join(', ');
    const r = rootOf(it.flow);
    const acts = it.acts.map((k) => k === 'errors' && it.ag[0] ? `<button class="tbtn" data-agent="${esc(it.ag[0].id)}" data-flowid="${esc(it.flow.id)}">Read errors</button>`
      : k === 'chat' ? it.ag.map((a) => { const t = (a.links || []).find((l) => l.kind === 'thread') || ((r && r.links) || []).find((l) => l.kind === 'thread'); return t && t.href ? `<a class="tbtn" href="${esc(t.href)}">Open chat ↗</a>` : ''; }).find(Boolean) || ''
      : k === 'compare' ? `<button class="tbtn" data-go="compare" data-flowid="${esc(it.flow.id)}">Read side by side</button>` : '').join('');
    return `<div class="nitem">${sIcon(it.st, it.st === 'killed' ? C.bad : it.st === 'waiting' || it.st === 'cond' ? C.acc : C.mist)}<div><p>${all ? `<span class="mist">${esc(it.flow.title)} · </span>` : ''}${esc(it.text)}${nm ? ' — ' + nm : ''}</p><div class="acts">${acts}<button class="tbtn" data-done="${esc(it.key)}">Done</button></div></div></div>`;
  }).join('');
}
function overviewHTML(f) {
  const latest = (f.posts || []).slice(-5).reverse().map((p) => ({ by: p.author, at: '#' + p.id + (p.at ? ' · ' + p.at : ''), text: p.text, id: 'post-' + p.id }))
    .concat(f.posts && f.posts.length ? [] : (f.agents || []).filter((a) => a.result).map((a) => ({ by: a.id, at: 'result', text: a.result })));
  const decSrc = f.decision && f.decision.source ? `<p class="note">From <button class="cite" data-flow="${esc(f.id)}" data-p="${esc(f.decision.source.split(':')[0])}" data-a="6" data-b="6">${esc(f.decision.source)}</button></p>` : '';
  return `<div class="kick mt20">Needs you</div>${needsHTML(needsOf(f))}
    ${f.decision ? `<div class="vk kick">Decision</div><div class="dec" style="margin-top:4px"><span class="w">${esc((f.decision.qualifier ? f.decision.qualifier + ' ' : '') + (f.decision.word || ''))}</span><button class="tbtn" data-go="compare">Read the verdicts →</button></div>${decSrc}` : ''}
    <div class="vk kick">Latest</div>${latest.length ? latest.map((x) => { const a = agentOf(f, x.by); return `<div class="post"><div class="by" style="color:${labC(a && a.lab)}">${esc(a ? aLabel(a) : x.by)}<span class="t">${esc(x.at)}</span></div><div class="md" style="font-size:17px">${md(x.text).html}</div></div>`; }).join('') : EMP('No posts or results yet.')}
    <div class="acts">${(f.posts || []).length ? `<button class="tbtn" data-go="feed">All ${f.posts.length} posts →</button>` : ''}<button class="tbtn" data-page="stats">Stats →</button></div>`;
}
let TT = 0;
function teleTick(a) {
  clearInterval(TT);
  if (!a || a.state !== 'running' || a.durS == null || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const t0 = Date.now(), base = Number(a.durS) || 0;
  TT = setInterval(() => { const e = q1('.tele [data-live]'); if (!e) return clearInterval(TT); e.textContent = fmtDur(base + (Date.now() - t0) / 1000); }, 1000);
}
const threadLinkOf = (a) => { const L = (a && a.links) || []; return L.find((l) => l.kind === 'thread' && l.id) || L.find((l) => l.kind === 'session' && l.id) || null; };
function agentHTML(f, a) {
  const tok = a.tokens ? `${fmtTok(a.tokens.in)} in · ${fmtTok(a.tokens.cache)} cached · ${fmtTok(a.tokens.out)} out` : '—';
  teleTick(a);
  const outs = (a.outputIds || []).map((id) => getOut(f, id)).filter(Boolean), E = a.errors || [], T = a.trace || [];
  const res = a.result || (a.verdict ? null : a.last), wsObj = a.workstream ? (f.workstreams || []).find((w) => w.id === a.workstream) : null, tid = threadLinkOf(a);
  const vk = (t, b) => `<div class="vk kick">${t}</div>${b}`;
  return `<div class="kick">Agent · ${esc(a.role)}${wsObj ? ' · ' + esc(wsObj.name) : ''}</div>
    <h2 class="ah2">${esc(aTask(a))}</h2>
    <div class="fx fxw">${emb(a.lab, a.state)}<span class="labl" style="color:${labC(a.lab)}">${esc(labN(a.lab))}</span> <span class="mono mist" style="font-size:15px">${esc(a.model || '')}</span></div>
    <div class="st" style="color:${isStop(a) ? C.bad : C.ink}">${sIcon(a.state, stateCol(a))}${esc(stateWord(a))}${a.verdict ? ` · ✓ ${esc(a.verdict)}` : ''}</div>
    <div class="tele"><div><span class="kick">Elapsed</span><b${a.state === 'running' ? ' data-live' : ''}>${a.durS != null ? fmtDur(a.durS) : '—'}</b></div><div><span class="kick">Tokens</span><b>${tok}</b></div><div><span class="kick">Est. cost</span><b>${fmtCost(a.costUsd, a.costEstimated)}</b></div></div>
    <div class="fr"><div class="kick">Result</div>${res ? `<div class="md prose">${md(res).html}</div>` : a.verdict && a.outputIds && a.outputIds[0] ? `<button class="tbtn" data-out="${esc(a.outputIds[0])}">Read the verdict →</button>` : EMP('No result recorded yet.')}</div>
    ${vk('Chat &amp; session', linksBlock(a))}${outs.length ? vk('Outputs', `<ul class="rows">${outs.map((o) => outRow(f, o, false)).join('')}</ul>`) : ''}
    ${E.length ? `<details class="lfold vk" ${S.errs ? 'open' : ''} id="errs"><summary>${LX}${errN(a)} error${errN(a) > 1 ? 's' : ''} · ${esc(String(E[0].kind || '').replace(/_/g, ' '))}</summary>
      <table class="tbl"><tbody>${E.map((e) => `<tr><td class="mono">${esc(e.at || '')}</td><td>${esc(e.kind)} ×${e.count}</td></tr>`).join('')}</tbody></table><div class="md">${E.map((e) => `<pre class="err"><code>${esc(e.msg)}</code></pre>`).join('')}</div></details>` : ''}
    ${T.length ? `<details class="lfold vk"><summary>${LX}Activity · ${T.reduce((s, t) => s + t[1], 0)} traced calls</summary>${trc(T)}</details>` : ''}
    <details class="lfold vk" data-msgs="${esc(tid ? tid.id : '')}"><summary>${LX}Last 5 messages</summary><div class="msgbox">${NTE(tid ? 'Expand to load messages.' : 'No thread or conversation linked for this agent.')}</div></details>`;
}
