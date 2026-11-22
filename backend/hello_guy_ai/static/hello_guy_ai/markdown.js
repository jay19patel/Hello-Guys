/* Minimal Markdown -> DOM renderer for chat answers. No dependencies.
 * Builds nodes with textContent only (never innerHTML), so model output cannot inject HTML.
 * Supports: headings, paragraphs, **bold**, *italic*, `code`, ``` fences, - / 1. lists,
 * > quotes, --- rules, [links](https://...), ![images](...), file links as attachment cards
 * and GFM tables. Tolerates half-streamed input.
 */
(function () {
  "use strict";

  const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|!?\[[^\]]*\]\([^)\s]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const SAFE_URL = /^(https?:|mailto:|\/(?!\/))/i; // no javascript:, data:, protocol-relative
  const FILE_URL = /\.(pdf|docx?|xlsx?|csv|pptx?|zip|txt|md|json)(\?|#|$)/i;

  function attachment(label, href) {
    const a = el("a", "md-file");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.appendChild(el("span", "md-file-ext", (href.match(FILE_URL) || [, "file"])[1].toUpperCase()));
    const meta = a.appendChild(el("span", "md-file-meta"));
    meta.appendChild(el("span", "md-file-name", label));
    meta.appendChild(el("span", "md-file-sub", "Open attachment ↗"));
    return a;
  }

  function inline(parent, text) {
    for (const part of text.split(INLINE)) {
      if (!part) continue;
      let m;
      if ((m = part.match(/^\*\*(.+)\*\*$/) || part.match(/^__(.+)__$/))) inline(parent.appendChild(el("strong")), m[1]);
      else if ((m = part.match(/^`(.+)`$/))) parent.appendChild(el("code", "", m[1]));
      else if ((m = part.match(/^!\[(.*)\]\((\S+)\)$/))) {
        if (SAFE_URL.test(m[2])) {
          const img = el("img", "md-img");
          img.src = m[2];
          img.alt = m[1];
          img.loading = "lazy";
          img.referrerPolicy = "no-referrer";
          parent.appendChild(img);
        } else parent.appendChild(document.createTextNode(m[1]));
      } else if ((m = part.match(/^\[(.+)\]\((\S+)\)$/))) {
        if (SAFE_URL.test(m[2]) && FILE_URL.test(m[2])) parent.appendChild(attachment(m[1], m[2]));
        else if (SAFE_URL.test(m[2])) {
          const a = el("a", "", m[1]);
          a.href = m[2];
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          parent.appendChild(a);
        } else parent.appendChild(document.createTextNode(m[1])); // drop javascript: and friends
      } else if ((m = part.match(/^\*(.+)\*$/) || part.match(/^_(.+)_$/))) inline(parent.appendChild(el("em")), m[1]);
      else parent.appendChild(document.createTextNode(part));
    }
    return parent;
  }

  function codeBlock(lang, code) {
    const wrap = el("div", "md-code");
    const bar = wrap.appendChild(el("div", "md-code-bar"));
    bar.appendChild(el("span", "", lang || "code"));
    const copy = bar.appendChild(el("button", "md-copy", "Copy"));
    copy.type = "button";
    copy.onclick = () => navigator.clipboard?.writeText(code).then(() => {
      copy.textContent = "Copied";
      setTimeout(() => (copy.textContent = "Copy"), 1500);
    });
    wrap.appendChild(el("pre")).appendChild(el("code", "", code));
    return wrap;
  }

  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const isTableSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line || "");

  function render(text) {
    const root = document.createDocumentFragment();
    const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (!line.trim()) { i++; continue; }

      if ((m = line.match(/^\s*```\s*([\w+-]*)/))) {
        const body = [];
        for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) body.push(lines[i]);
        i++; // closing fence (absent while streaming)
        root.appendChild(codeBlock(m[1], body.join("\n")));
      } else if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        root.appendChild(inline(el(`h${Math.min(m[1].length + 2, 6)}`, "md-h"), m[2]));
        i++;
      } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
        root.appendChild(el("hr"));
        i++;
      } else if (line.includes("|") && isTableSep(lines[i + 1])) {
        const wrap = root.appendChild(el("div", "md-table"));
        const table = wrap.appendChild(el("table"));
        const head = table.appendChild(el("thead")).appendChild(el("tr"));
        cells(line).forEach((c) => inline(head.appendChild(el("th")), c));
        const body = table.appendChild(el("tbody"));
        for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) {
          const tr = body.appendChild(el("tr"));
          cells(lines[i]).forEach((c) => inline(tr.appendChild(el("td")), c));
        }
      } else if (/^\s*>/.test(line)) {
        const quote = [];
        for (; i < lines.length && /^\s*>/.test(lines[i]); i++) quote.push(lines[i].replace(/^\s*>\s?/, ""));
        root.appendChild(el("blockquote")).appendChild(render(quote.join("\n")));
      } else if ((m = line.match(/^\s*([-*+]|\d+[.)])\s+/))) {
        const ordered = /\d/.test(m[1]);
        const list = root.appendChild(el(ordered ? "ol" : "ul"));
        const itemRe = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
        for (; i < lines.length; i++) {
          const item = lines[i].match(itemRe);
          if (item) inline(list.appendChild(el("li")), item[1]);
          else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && list.lastChild) inline(list.lastChild, " " + lines[i].trim());
          else break;
        }
      } else {
        const para = [];
        for (; i < lines.length && lines[i].trim() && !/^(\s*```|#{1,6}\s|\s*>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i]); i++) {
          if (lines[i].includes("|") && isTableSep(lines[i + 1])) break;
          para.push(lines[i]);
        }
        const p = root.appendChild(el("p"));
        para.forEach((l, n) => { if (n) p.appendChild(el("br")); inline(p, l); });
      }
    }
    return root;
  }

  window.HelloGuy = { ...(window.HelloGuy || {}), renderMarkdown: render };
})();
