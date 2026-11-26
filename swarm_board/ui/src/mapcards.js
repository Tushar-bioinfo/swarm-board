// ===== mapcards.js =====
function mCard(el) {
  const f = flow();
  if (!f) return;
  const svg = el.closest('svg'), d = el.dataset, cl = CL(el),
    seatCard = (a) => {
      if (!a) return;
      sPop(el, D_('fx', emb(a.lab, a.state) + `<span class="nm" style="color:${LB(a.lab).txt}">${E(labN(a.lab))}</span>`) +
        D_('mono mist', E(a.model || '')) +
        D_('mist', `${a.weight != null && isF(+a.weight) ? `w ${Number(a.weight).toFixed(2)} · ` : ''}${E(fDur(a.durS) || '—')} · ${fUsd(a.costUsd, a.costEstimated)}`) +
        (a.verdict ? D_('', `✓ ${E(a.verdict)}`) : '') + D_('ft', 'Click to open Compare'), LB(a.lab).txt);
      hl(svg, [a.id]);
    };
  if (cl.contains('nd')) { const a = agOf(f, d.agent); if (!a) return; if (f.kind === 'committee') return seatCard(a); nCard(el); hl(svg, wit_(f, a.id).concat(a.id === S.uf ? kids(f, a.id).map((w) => w.id) : [])); }
  else if (cl.contains('sr')) seatCard(agOf(f, d.to));
  else if (cl.contains('bd')) {
    const p = (f.posts || []).find((x) => String(x.id) === String(d.post)), a = p && agOf(f, p.author);
    if (!p) return;
    const t = String(p.text || '').replace(/[`*_#>[\]]/g, '').replace(/\s+/g, ' ').trim();
    sPop(el, D_('nm', `#${p.id} · ${E(a ? aLb(a) : p.author)} → ${E((p.mentions || []).join(', '))}${p.at ? ' · ' + E(p.at) : ''}`) +
      D_('mist', E(t.slice(0, 140)) + (t.length > 140 ? '…' : '')) + D_('ft', 'Click to read the post'), LB(a && a.lab).txt);
    hl(svg, wit_(f, d.to), d.post);
  } else if (cl.contains('pod')) {
    const lead = agOf(f, d.lead), ws = kids(f, d.lead), run = ws.filter((w) => w.state === 'running'), dn = ws.filter((w) => w.state === 'done').length;
    sPop(el, D_('nm', E(lead ? aLb(lead) : d.lead)) + D_('mist', `${ws.length} workers · ${run.length} running · ${dn} done`) +
      (run.length ? D_('', `${JN(run, (w) => E(aLb(w)), ', ')} running`) : '') + D_('ft', 'Click to unfold · Esc folds'), LB(lead && lead.lab).txt);
    hl(svg, wit_(f, d.lead).concat(ws.map((w) => w.id)));
  } else if (cl.contains('spk')) {
    const a = agOf(f, d.agent);
    if (!a) return;
    sPop(el, `<div class="nm" style="color:${C.bad}">${E(aLb(a))} · ${errN(a)} error${errN(a) > 1 ? 's' : ''}</div>` +
      JN(a.errors || [], (e) => D_('mono mist', `${E(e.kind)} × ${e.count}${e.at ? ' · ' + E(e.at) : ''}`) + D_('', E(e.msg))) +
      D_('ft', 'Click to read the errors'), C.bad);
    hl(svg, wit_(f, a.id));
  } else if (cl.contains('seal')) {
    const x = f.decision || {}, ag = f.agents || [];
    sPop(el, D_('nm', `Decision ${E((x.qualifier || '') + ' ' + (x.word || ''))}`) + D_('mist', `weighted · ${ag.length} seats`) + D_('ft', 'Click to open Compare'), C.ink);
    hl(svg, ag.map((a) => a.id));
  }
}