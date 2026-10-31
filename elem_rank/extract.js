// In-page candidate extractor. Returns interactive + labelled-text elements with the same role/name view
// the Playwright MCP snapshot gives, plus attributes and nearby context the snapshot drops.
(maxEls) => {
  const IMPLICIT = {A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox', H1: 'heading', H2: 'heading',
    H3: 'heading', H4: 'heading', H5: 'heading', H6: 'heading', IMG: 'img', LABEL: 'label', TH: 'columnheader',
    LEGEND: 'legend', SUMMARY: 'button', OPTION: 'option', NAV: 'navigation', P: 'paragraph', LI: 'listitem', TD: 'cell'};
  const INPUT_ROLE = {checkbox: 'checkbox', radio: 'radio', search: 'searchbox', range: 'slider', button: 'button',
    submit: 'button', reset: 'button', image: 'button', number: 'spinbutton'};
  const INTERACTIVE = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[contenteditable=""],[contenteditable=true],' +
    '[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=switch],[role=combobox],' +
    '[role=textbox],[role=searchbox],[role=option],[role=slider],[onclick],[tabindex]:not([tabindex="-1"]),' +
    'a:not([href]):has(svg,i,img),[class~=btn]:not(div)';  // icon-only anchors / buttons styled by class
  const LABELLED = 'h1,h2,h3,h4,h5,h6,[role=heading],label,legend,th,img[alt]';
  const clean = (s, n = 160) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.05;
  };
  const role = (el) => {
    const r = el.getAttribute('role');
    if (r) return r.split(' ')[0];
    if (el.tagName === 'INPUT') return INPUT_ROLE[(el.type || 'text').toLowerCase()] || 'textbox';
    if (el.tagName === 'A' && !el.hasAttribute('href')) return 'generic';
    return IMPLICIT[el.tagName] || 'generic';
  };
  const byIds = (ids) => clean(ids.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText || e.textContent).join(' '));
  const labelFor = (el) => {
    if (el.labels && el.labels.length) return clean([...el.labels].map(l => l.innerText).join(' '));
    return '';
  };
  const accName = (el) => {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = byIds(lb); if (t) return t; }
    const al = el.getAttribute('aria-label'); if (al && al.trim()) return clean(al);
    const lf = labelFor(el); if (lf) return lf;
    if (el.tagName === 'IMG') return clean(el.getAttribute('alt'));
    if (el.tagName === 'INPUT') {
      const t = (el.type || '').toLowerCase();
      if (['submit', 'button', 'reset'].includes(t)) return clean(el.value || t);
      if (t === 'image') return clean(el.alt || el.value);
      return clean(el.getAttribute('title') || el.getAttribute('placeholder'));
    }
    if (['SELECT', 'TEXTAREA'].includes(el.tagName)) return clean(el.getAttribute('title') || el.getAttribute('placeholder'));
    let t = clean(el.innerText);
    if (!t) { const img = el.querySelector('img[alt],svg[aria-label],[aria-label]'); if (img) t = clean(img.getAttribute('alt') || img.getAttribute('aria-label')); }
    return t || clean(el.getAttribute('title'));
  };
  // icon-only controls (✕, 🗑, +): the icon's name from icon-font classes or SVG attributes, e.g. "xmark", "trash"
  const iconOf = (el) => {
    const svg = el.querySelector('svg[data-icon]');
    if (svg) return clean(svg.getAttribute('data-icon'), 30);
    for (const i of el.querySelectorAll('[class*="fa-"],[class*="bi-"],[class*="icon-"],[class*="material-icons"]')) {
      const m = (i.getAttribute('class') || '').match(/(?:^|\s)(?:fa|bi|icon)-(?!icon\b)([a-z][a-z0-9-]*)/);
      if (m) return m[1];
      if (/material-icons/.test(i.getAttribute('class') || '')) return clean(i.textContent, 30);
    }
    return '';
  };
  // table rows: the row's first text cell ("Claw Hammer"), so "delete Claw Hammer" finds that row's button
  const rowOf = (el) => {
    const tr = el.closest('tr');
    if (!tr || el.tagName === 'TR') return '';
    const c = [...tr.children].find(td => clean(td.innerText));
    return c && !c.contains(el) ? clean(c.innerText, 60) : '';
  };
  const ownText = (el) => clean([...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' '));
  const prevText = (el) => {
    // label-like text just before the element (sibling or parent's sibling), e.g. <p>Username</p><input>
    let n = el, hops = 0;
    while (n && hops < 3) {
      let s = n.previousElementSibling;
      while (s) { const t = clean(s.innerText, 80); if (t) return t; s = s.previousElementSibling; }
      n = n.parentElement; hops++;
    }
    return '';
  };
  const headingCache = new Map();
  const sectionHeading = (el) => {
    let n = el;
    for (let d = 0; n && d < 8; d++, n = n.parentElement) {
      if (headingCache.has(n)) return headingCache.get(n);
      const h = n.querySelector && n.querySelector('h1,h2,h3,h4,[role=heading],legend');
      if (h && h !== el && !el.contains(h)) { const t = clean(h.innerText, 80); headingCache.set(n, t); return t; }
    }
    return '';
  };
  const landmark = (el) => {
    const l = el.closest('nav,header,footer,main,aside,form,dialog,[role=navigation],[role=banner],[role=contentinfo],[role=main],[role=dialog],[role=search]');
    if (!l) return '';
    const name = clean(l.getAttribute('aria-label') || '', 40);
    return (l.getAttribute('role') || l.tagName.toLowerCase()) + (name ? ':' + name : '');
  };
  const cssPath = (el) => {
    if (el.id && /^[A-Za-z][\w-]*$/.test(el.id) && document.querySelectorAll('#' + el.id).length === 1) return '#' + el.id;
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 6; n = n.parentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id && /^[A-Za-z][\w-]*$/.test(n.id)) { parts.unshift('#' + n.id); break; }
      const sib = n.parentElement ? [...n.parentElement.children].filter(c => c.tagName === n.tagName) : [];
      if (sib.length > 1) p += `:nth-of-type(${sib.indexOf(n) + 1})`;
      parts.unshift(p);
    }
    return parts.join(' > ');
  };
  document.querySelectorAll('[data-er-idx]').forEach(e => e.removeAttribute('data-er-idx'));
  const seen = new Set();
  const out = [], nodes = [];
  const add = (el, kind) => {
    if (seen.has(el) || out.length >= maxEls || !visible(el)) return;
    seen.add(el);
    el.setAttribute('data-er-idx', String(out.length));  // lets callers verify a locator resolves to this element
    nodes.push(el);
    const r = el.getBoundingClientRect();
    const a = (k) => clean(el.getAttribute(k), 100);
    let href = el.getAttribute('href') || '';
    try { if (href) { const u = new URL(href, location.href); href = u.pathname + (u.search ? '?' : ''); } } catch (e) {}
    out.push({
      idx: out.length, kind, tag: el.tagName.toLowerCase(), role: role(el), name: accName(el),
      text: clean(el.innerText || el.textContent, 200),
      attrs: {id: a('id'), name: a('name'), type: a('type'), placeholder: a('placeholder'), aria_label: a('aria-label'),
              title: a('title'), alt: a('alt'), value: ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase()) ? a('value') : '',
              href, testid: a('data-testid') || a('data-test') || a('data-qa'), testid_attr: ['data-testid', 'data-test', 'data-qa'].find(k => a(k)) || '', cls: clean((el.getAttribute('class') || '').split(/\s+/).slice(0, 4).join(' '), 80)},
      icon: (el.innerText || '').trim() ? '' : iconOf(el), row: rowOf(el),
      near: prevText(el), heading: sectionHeading(el), landmark: landmark(el),
      box: [Math.round(r.x), Math.round(r.y + scrollY), Math.round(r.width), Math.round(r.height)],
      in_viewport: r.top < innerHeight && r.bottom > 0, css: cssPath(el),
    });
  };
  document.querySelectorAll(INTERACTIVE).forEach(el => add(el, 'interactive'));
  document.querySelectorAll(LABELLED).forEach(el => add(el, 'label'));
  // labelled text blocks: leaf-ish elements with their own short text
  document.querySelectorAll('p,span,li,td,dt,dd,div,strong,em,b,small').forEach(el => {
    const t = ownText(el);
    if (t.length >= 2 && t.length <= 120 && !el.closest('a,button,label,[role=button],[role=link]')) add(el, 'text');
  });
  // Card context for repeated labels (generic, any site): when several elements share a role + name ("Buy Now" x6,
  // "Add to cart", "Details"), each gets the title of its own card: the largest ancestor that still contains only
  // that one copy. Title = its first heading, else a title/name-like class, else its first meaningful text.
  const groups = new Map();
  out.forEach((e, i) => { const k = e.role + '|' + (e.name || '').toLowerCase(); if (e.name) (groups.get(k) || groups.set(k, []).get(k)).push(i); });
  const cardTitle = (box, el) => {
    const own = (el.innerText || '').trim();
    const pick = (q) => [...box.querySelectorAll(q)].filter(n => !el.contains(n) && !n.contains(el))
      .map(n => clean(n.innerText, 90)).find(t => t.length >= 3 && t !== own);
    return pick('h1,h2,h3,h4,h5,h6,[role=heading]') || pick('[class*=title i],[class*=name i],[class*=heading i]') ||
      [...box.querySelectorAll('*')].filter(n => !n.children.length && !el.contains(n)).map(n => clean(n.innerText, 90))
        .find(t => t.length >= 6 && t !== own && !/^[\d\s¥$€£₹.,:/-]+$/.test(t)) || '';
  };
  for (const idxs of groups.values()) {
    if (idxs.length < 2) continue;
    const members = idxs.map(i => nodes[i]);
    idxs.forEach(i => {
      let box = nodes[i];
      while (box.parentElement && members.filter(m => box.parentElement.contains(m)).length === 1) box = box.parentElement;
      if (box === nodes[i]) return;
      const t = cardTitle(box, nodes[i]);
      if (!t) return;
      const cls = (box.getAttribute('class') || '').trim().split(/\s+/)[0];
      out[i].card = t;
      out[i].card_sel = box.tagName.toLowerCase() + (cls && /^[A-Za-z_-][\w-]*$/.test(cls) ? '.' + cls : '');
    });
  }
  return {url: location.href, title: document.title, n: out.length, elements: out,
          page_h: document.documentElement.scrollHeight, vw: innerWidth, vh: innerHeight};
}
