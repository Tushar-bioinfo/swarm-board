// ===== find.js =====
let FND = { i: 0, ms: [] };
function oFind() {
  const r = $('findrow');
  if (!r) return;
  r.innerHTML = D_('findrow', `<input id="fq" type="search" aria-label="Find in text" placeholder="Find" value="${E(S.fq || '')}"><span class="cnt" id="fcnt"></span>${TB('data-act="fprev" aria-label="Previous match"', '↑')}${TB('data-act="fnext" aria-label="Next match"', '↓')}${TB('data-act="fclose"', 'Esc')}`);
  const q = $('fq');
  A_(q, 'input', () => { S.fq = q.value; rFind(); });
  A_(q, 'keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); sFind(e.shiftKey ? -1 : 1); } if (e.key === 'Escape') { e.stopPropagation(); cFind(); } });
  q.focus(); rFind();
}
function cFind() { S.fn = false; S.fq = ''; cMrk(); IH('findrow', ''); const b = q1('[data-act=find]'); if (b) SA(b, 'aria-pressed', 'false'); }
function cMrk() { qa('#rmain mark.find').forEach((k) => k.replaceWith(k.textContent)); const m = $('rmain'); if (m) m.normalize(); }
function rFind() {
  cMrk(); FND = { i: 0, ms: [] };
  const q = (S.fq || '').trim(), m = $('rmain');
  if (q && m) {
    const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    wTxt(m, re, (x) => { const k = CE('mark'); k.className = 'find'; k.textContent = x[0]; return k; }, 'mark,button.tbtn,.endnav');
    FND.ms = qa('mark.find', m);
  }
  sFind(0);
}
function sFind(d) {
  const ms = FND.ms, c = $('fcnt');
  if (!ms.length) { if (c) c.textContent = S.fq ? '0 of 0' : ''; return; }
  ms.forEach((x) => CL(x).remove('cur'));
  FND.i = (FND.i + d + ms.length) % ms.length;
  CL(ms[FND.i]).add('cur');
  const det = ms[FND.i].closest('details'); if (det) det.open = true;
  if (c) c.textContent = `${FND.i + 1} of ${ms.length}`;
  scrTo(ms[FND.i]);
}
function scrTo(el) {
  const sc = CL($('app')).contains('appm') ? el.closest('.side,.left') : null;
  if (sc) sc.scrollTop += rect(el).top - rect(sc).top - 120;
  else if (window.self === window.top || QS.has('app')) el.scrollIntoView({ block: 'center' });
  else window.scrollTo(0, rect(el).top + scrollY - 120);
}
function prog() {
  const i = $('progi'), b = $('rbody');
  if (!i || !b) return;
  const r = rect(b), sc = CL($('app')).contains('appm') ? $('side') : null, sr = sc ? rect(sc) : null,
    vh = sr ? sr.bottom : innerHeight, p = MX(0, MN(1, (vh - r.top) / MX(1, r.height)));
  i.style.width = (100 * p).toFixed(1) + '%';
  return p;
}