// ===== mapcards.js: map hover cards =====
function mapCard(el) {
  const f = flow();
  if (!f) return;
  const svg = el.closest('svg'), d = el.dataset, cl = el.classList, row = (h) => `<div class="fx">${h}</div>`;
  const seatCard = (a) => {
    if (!a) return;
    showPop(el, `${row(emb(a.lab, a.state) + `<span class="nm" style="color:${LB(a.lab).txt}">${esc(labN(a.lab))}</span>`)}<div class="mono mist">${esc(a.model || '')}</div><div class="mist">${a.weight != null ? `w ${a.weight.toFixed(2)} · ` : ''}${esc(fmtDur(a.durS) || '—')} · ${fmtCost(a.costUsd, a.costEstimated)}</div>${a.verdict ? `<div>✓ ${esc(a.verdict)}</div>` : ''}<div class="ft">Click to open Compare</div>`, LB(a.lab).txt);
    hl(svg, [a.id]);
  };
  if (cl.contains('nd')) { const a = agentOf(f, d.agent); if (!a) return; if (f.kind === 'committee') return seatCard(a); nodeCard(el); hl(svg, witness(f, a.id).concat(a.id === S.unfold ? kids(f, a.id).map((w) => w.id) : [])); }
  else if (cl.contains('sr')) seatCard(agentOf(f, d.to));
  else if (cl.contains('bd')) {
    const p = (f.posts || []).find((x) => x.id === +d.post), a = p && agentOf(f, p.author);
    if (!p) return;
    const t = String(p.text || '').replace(/[`*_#>[\]]/g, '').replace(/\s+/g, ' ').trim();
    showPop(el, `<div class="nm">#${p.id} · ${esc(a ? aName(a) : p.author)} → ${esc((p.mentions || []).join(', '))}${p.at ? ' · ' + esc(p.at) : ''}</div><div class="mist">${esc(t.slice(0, 140))}${t.length > 140 ? '…' : ''}</div><div class="ft">Click to read the post</div>`, LB(a && a.lab).txt);
    hl(svg, witness(f, d.to), d.post);
  } else if (cl.contains('pod')) {
    const lead = agentOf(f, d.lead), ws = kids(f, d.lead), run = ws.filter((w) => w.state === 'running'), dn = ws.filter((w) => w.state === 'done').length;
    showPop(el, `<div class="nm">${esc(lead ? aName(lead) : d.lead)}</div><div class="mist">${ws.length} workers · ${run.length} running · ${dn} done</div>${run.length ? `<div>${run.map((w) => esc(aName(w))).join(', ')} running</div>` : ''}<div class="ft">Click to unfold · Esc folds</div>`, LB(lead && lead.lab).txt);
    hl(svg, witness(f, d.lead).concat(ws.map((w) => w.id)));
  } else if (cl.contains('spk')) {
    const a = agentOf(f, d.agent);
    if (!a) return;
    showPop(el, `<div class="nm" style="color:${C.bad}">${esc(aName(a))} · ${errN(a)} error${errN(a) > 1 ? 's' : ''}</div>${(a.errors || []).map((e) => `<div class="mono mist">${esc(e.kind)} × ${e.count}${e.at ? ' · ' + esc(e.at) : ''}</div><div>${esc(e.msg)}</div>`).join('')}<div class="ft">Click to read the errors</div>`, C.bad);
    hl(svg, witness(f, a.id));
  } else if (cl.contains('seal')) {
    const x = f.decision || {};
    showPop(el, `<div class="nm">Decision ${esc((x.qualifier || '') + ' ' + (x.word || ''))}</div><div class="mist">weighted · ${f.agents.length} seats</div><div class="ft">Click to open Compare</div>`, C.ink);
    hl(svg, f.agents.map((a) => a.id));
  }
}
