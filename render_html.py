#!/usr/bin/env python3
"""Render the JSON output of ``audit_rbac.py`` into a self-contained HTML file.

The resulting file embeds the report data plus a small amount of CSS and
JavaScript so it can be opened directly in a browser without any external
assets. The view groups role assignments per service principal and per
subscription, and offers a fuzzy filter input (press ``/`` to focus).
"""

import argparse
import html
import json
import sys
from pathlib import Path


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Azure RBAC Audit Report</title>
<style>
  :root {
    --bg: #f6f7f9;
    --fg: #1f2933;
    --muted: #62707d;
    --accent: #0b6bcb;
    --row-a: #ffffff;
    --row-b: #eef2f7;
    --sub-bg: #f9fafb;
    --border: #d8dee6;
    --chip-bg: #e3ecf7;
    --chip-fg: #0b3d75;
    --match-bg: #fff2a8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      "Helvetica Neue", Arial, sans-serif;
    background: var(--bg);
    color: var(--fg);
    line-height: 1.45;
  }
  header {
    position: sticky;
    top: 0;
    z-index: 10;
    background: #ffffff;
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }
  header h1 {
    margin: 0 0 8px 0;
    font-size: 18px;
    font-weight: 600;
  }
  .toolbar {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
  }
  #search {
    flex: 1;
    min-width: 260px;
    padding: 8px 12px;
    font-size: 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    outline: none;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  #search:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(11, 107, 203, 0.15);
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
  }
  .hint kbd {
    background: var(--bg);
    border: 1px solid var(--border);
    border-bottom-width: 2px;
    border-radius: 4px;
    padding: 0 6px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
  }
  #count {
    color: var(--muted);
    font-size: 12px;
  }
  main {
    padding: 0 24px 32px 24px;
  }
  .sp {
    border: 1px solid var(--border);
    border-radius: 8px;
    margin: 16px 0;
    overflow: hidden;
  }
  .sp.row-a { background: var(--row-a); }
  .sp.row-b { background: var(--row-b); }
  .sp-header {
    padding: 14px 18px;
    border-bottom: 1px solid var(--border);
  }
  .sp-name {
    font-size: 16px;
    font-weight: 600;
    margin: 0 0 4px 0;
  }
  .sp-ids {
    font-size: 12px;
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-all;
  }
  .sp-ids .label {
    color: var(--fg);
    font-family: inherit;
    font-weight: 500;
    margin-right: 4px;
  }
  .sp-ids .id-row { margin-top: 2px; }
  .no-roles {
    padding: 14px 18px;
    color: var(--muted);
    font-style: italic;
  }
  .sub {
    padding: 12px 18px;
    border-top: 1px solid var(--border);
    background: var(--sub-bg);
  }
  .sp.row-b .sub { background: #e4e9f0; }
  .sub:first-of-type { border-top: none; }
  .sub-header {
    display: flex;
    gap: 8px;
    align-items: baseline;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  .sub-name {
    font-weight: 600;
    font-size: 14px;
  }
  .sub-id {
    font-size: 12px;
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .perms {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .perm {
    padding: 6px 0;
    border-top: 1px dashed var(--border);
    font-size: 13px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 10px;
    align-items: baseline;
  }
  .perm:first-child { border-top: none; }
  .role {
    background: var(--chip-bg);
    color: var(--chip-fg);
    padding: 2px 8px;
    border-radius: 12px;
    font-weight: 600;
    font-size: 12px;
    white-space: nowrap;
  }
  .scope {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    word-break: break-all;
    color: var(--fg);
  }
  .scope-type {
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .hidden { display: none !important; }
  mark.match {
    background: var(--match-bg);
    color: inherit;
    padding: 0 1px;
    border-radius: 2px;
  }
  .empty {
    text-align: center;
    color: var(--muted);
    padding: 48px 0;
    font-style: italic;
  }
</style>
</head>
<body>
<header>
  <h1>Azure RBAC Audit Report</h1>
  <div class="toolbar">
    <input id="search" type="search" placeholder="Filter service principals (space-separated tokens, in order)..." autocomplete="off" spellcheck="false">
    <span id="count"></span>
    <span class="hint">Press <kbd>/</kbd> to focus, <kbd>Esc</kbd> to clear</span>
  </div>
</header>
<main id="report"></main>
<script id="report-data" type="application/json">__REPORT_JSON__</script>
<script>
(function () {
  const data = JSON.parse(document.getElementById('report-data').textContent);
  const report = document.getElementById('report');
  const search = document.getElementById('search');
  const countEl = document.getElementById('count');

  function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function groupBySubscription(assignments) {
    const groups = new Map();
    for (const ra of assignments || []) {
      const key = (ra.subscriptionId || '') + '||' + (ra.subscriptionName || '');
      if (!groups.has(key)) {
        groups.set(key, {
          subscriptionId: ra.subscriptionId,
          subscriptionName: ra.subscriptionName,
          items: [],
        });
      }
      groups.get(key).items.push(ra);
    }
    return Array.from(groups.values()).sort((a, b) => {
      const an = (a.subscriptionName || a.subscriptionId || '').toLowerCase();
      const bn = (b.subscriptionName || b.subscriptionId || '').toLowerCase();
      return an.localeCompare(bn);
    });
  }

  function renderSp(sp, idx) {
    const rowClass = idx % 2 === 0 ? 'row-a' : 'row-b';
    const name = sp.displayName || '(unnamed service principal)';
    const parts = [];
    parts.push('<section class="sp ' + rowClass + '" data-name="' +
      escapeHtml((sp.displayName || '').toLowerCase()) + '">');
    parts.push('<div class="sp-header">');
    parts.push('<div class="sp-name">' + escapeHtml(name) + '</div>');
    parts.push('<div class="sp-ids">');
    if (sp.applicationId) {
      parts.push('<div class="id-row"><span class="label">Client ID:</span>' +
        escapeHtml(sp.applicationId) + '</div>');
    }
    if (sp.objectId) {
      parts.push('<div class="id-row"><span class="label">Object ID:</span>' +
        escapeHtml(sp.objectId) + '</div>');
    }
    parts.push('</div>');
    parts.push('</div>');

    const groups = groupBySubscription(sp.roleAssignments);
    if (groups.length === 0) {
      parts.push('<div class="no-roles">No role assignments found.</div>');
    } else {
      for (const g of groups) {
        parts.push('<div class="sub">');
        parts.push('<div class="sub-header">');
        parts.push('<span class="sub-name">' +
          escapeHtml(g.subscriptionName || '(no subscription name)') + '</span>');
        if (g.subscriptionId) {
          parts.push('<span class="sub-id">' + escapeHtml(g.subscriptionId) + '</span>');
        }
        parts.push('</div>');
        parts.push('<ul class="perms">');
        const items = g.items.slice().sort((a, b) => {
          const ar = (a.roleName || '').toLowerCase();
          const br = (b.roleName || '').toLowerCase();
          if (ar !== br) return ar.localeCompare(br);
          return (a.scope || '').localeCompare(b.scope || '');
        });
        for (const ra of items) {
          parts.push('<li class="perm">');
          parts.push('<span class="role">' +
            escapeHtml(ra.roleName || '(unknown role)') + '</span>');
          if (ra.scopeType) {
            parts.push('<span class="scope-type">' +
              escapeHtml(ra.scopeType) + '</span>');
          }
          parts.push('<span class="scope">' +
            escapeHtml(ra.scope || '') + '</span>');
          parts.push('</li>');
        }
        parts.push('</ul>');
        parts.push('</div>');
      }
    }
    parts.push('</section>');
    return parts.join('');
  }

  function renderAll() {
    if (!data.length) {
      report.innerHTML = '<div class="empty">No service principals in this report.</div>';
      countEl.textContent = '0 service principals';
      return;
    }
    const sorted = data.slice().sort((a, b) => {
      const an = (a.displayName || '').toLowerCase();
      const bn = (b.displayName || '').toLowerCase();
      return an.localeCompare(bn);
    });
    report.innerHTML = sorted.map(renderSp).join('');
    updateCount();
  }

  // Case-insensitive multi-token match: the query is split on whitespace and
  // each token must appear as a substring of the candidate, in the order
  // given. E.g. "sp app-" matches "sp-terraform-app-gateway".
  function nameMatches(query, candidate) {
    const tokens = (query || '').toLowerCase().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return true;
    const c = (candidate || '').toLowerCase();
    let pos = 0;
    for (const t of tokens) {
      const found = c.indexOf(t, pos);
      if (found === -1) return false;
      pos = found + t.length;
    }
    return true;
  }

  function updateCount() {
    const total = report.querySelectorAll('.sp').length;
    const visible = report.querySelectorAll('.sp:not(.hidden)').length;
    if (visible === total) {
      countEl.textContent = total + ' service principal' + (total === 1 ? '' : 's');
    } else {
      countEl.textContent = visible + ' / ' + total + ' shown';
    }
  }

  function reassignAlternating() {
    let idx = 0;
    for (const el of report.querySelectorAll('.sp')) {
      if (el.classList.contains('hidden')) continue;
      el.classList.remove('row-a', 'row-b');
      el.classList.add(idx % 2 === 0 ? 'row-a' : 'row-b');
      idx++;
    }
  }

  function applyFilter() {
    const q = search.value.trim();
    for (const el of report.querySelectorAll('.sp')) {
      const name = el.getAttribute('data-name') || '';
      const visible = nameMatches(q, name);
      el.classList.toggle('hidden', !visible);
    }
    reassignAlternating();
    updateCount();
  }

  search.addEventListener('input', applyFilter);
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      search.value = '';
      applyFilter();
      search.blur();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== search) {
      const tag = (document.activeElement && document.activeElement.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      e.preventDefault();
      search.focus();
      search.select();
    }
  });

  renderAll();
})();
</script>
</body>
</html>
"""


def render(report: list[dict], title: str | None = None) -> str:
    """Returns a self-contained HTML document for the given report data."""
    # json.dumps with ensure_ascii avoids issues with non-ASCII characters in
    # service principal names. We also guard against a literal "</script>"
    # appearing in any string field by escaping the forward slash.
    payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    document = HTML_TEMPLATE.replace("__REPORT_JSON__", payload)
    if title:
        document = document.replace(
            "<title>Azure RBAC Audit Report</title>",
            f"<title>{html.escape(title)}</title>",
        )
    return document


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Render the JSON output of audit_rbac.py into a self-contained "
            "HTML file with a search filter."
        )
    )
    parser.add_argument(
        "input",
        help="Path to the JSON report produced by audit_rbac.py.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="audit-results.html",
        help="Output HTML file path (default: audit-results.html).",
    )
    parser.add_argument(
        "--title",
        help="Optional document title to use in the <title> tag.",
    )
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        print(f"Error reading {args.input}: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: {args.input} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print(
            f"Error: expected a JSON array of report entries in {args.input}, "
            f"got {type(data).__name__}.",
            file=sys.stderr,
        )
        sys.exit(1)

    document = render(data, title=args.title)
    out_path = Path(args.output)
    out_path.write_text(document, encoding="utf-8")
    print(f"Wrote HTML report to {out_path}")


if __name__ == "__main__":
    main()
