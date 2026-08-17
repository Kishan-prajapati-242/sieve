"""Contrast audit across every route and both themes.

The theming equivalent of the scrollWidth assertion. I shipped light mode on
the reasoning that a position-semantic ink ramp made it a free value swap,
and did not look — which is the same error as trusting a screenshot crop:
a property was assumed rather than measured, and it was wrong.

What this measures, in the page, for every visible text node:

  * the element's computed colour
  * its EFFECTIVE background — walking up the tree past transparent
    ancestors, because almost nothing sets its own background and a naive
    read returns rgba(0,0,0,0) everywhere
  * the WCAG 2.1 contrast ratio between them

Thresholds are the AA ones: 4.5:1 for body text, 3:1 for large text
(>=24px, or >=18.66px bold). Anything below is reported with its selector,
theme, route and computed colours, so a failure names the fix.

    docker compose run --rm test python -m bench.theme_audit   # needs the dev server
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROUTES = ["/", "/search", "/login", "/signup", "/collections"]
THEMES = ["dark", "light"]

# The resting state was the only one checked, which is why a white-on-white
# button hover shipped past a passing audit. Hover, focus and active are
# separate colour pairs and each can fail on its own — CSS lets you change
# background without changing text, and nothing warns you.
STATES = ["rest", "hover", "focus", "active"]

AUDIT_JS = r"""
() => {
  const srgb = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
  const lum = ([r,g,b]) => 0.2126*srgb(r) + 0.7152*srgb(g) + 0.0722*srgb(b);
  const parse = (s) => {
    const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return { rgb: [p[0],p[1],p[2]], a: p.length > 3 ? p[3] : 1 };
  };
  // Composite a colour over what is behind it, so semi-transparent text and
  // tinted panels are judged as they actually appear.
  const over = (fg, bg) => fg.rgb.map((c,i) => c*fg.a + bg[i]*(1-fg.a));
  // An element may DECLARE its effective background when the real one is a
  // sibling (an animated pill, a layered highlight) that tree-walking cannot
  // find. Declaring beats exempting: the value is still checked.
  const declared = (el) => {
    const v = el.getAttribute && el.getAttribute('data-contrast-bg');
    if (!v) return null;
    const probe = document.createElement('div');
    probe.style.color = v; document.body.appendChild(probe);
    const c = parse(getComputedStyle(probe).color); probe.remove();
    return c ? c.rgb : null;
  };
  const effectiveBg = (el) => {
    const d = declared(el); if (d) return d;
    let node = el, acc = null;
    while (node && node !== document.documentElement) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c && c.a > 0) {
        acc = acc ? over({rgb: acc, a: 1}, c.rgb) : (c.a === 1 ? c.rgb : null);
        if (c.a === 1) return acc || c.rgb;
      }
      node = node.parentElement;
    }
    const html = parse(getComputedStyle(document.documentElement).backgroundColor);
    return html ? html.rgb : [0,0,0];
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    return (hi + 0.05) / (lo + 0.05);
  };

  const out = [];
  document.querySelectorAll('*').forEach(el => {
    const text = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
    if (!text) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return;
    if (parseFloat(cs.opacity) < 0.15) return;
    // Gradient-clipped text sets color:transparent on purpose; its
    // legibility is a property of the gradient, not of `color`.
    if (cs.webkitBackgroundClip === 'text' || cs.backgroundClip === 'text') return;
    const fg = parse(cs.color); if (!fg || fg.a === 0) return;
    const bg = effectiveBg(el);
    const fgc = over(fg, bg);
    const size = parseFloat(cs.fontSize);
    const bold = parseInt(cs.fontWeight, 10) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3.0 : 4.5;
    const got = ratio(fgc, bg);
    if (got < need) {
      out.push({
        text: text.slice(0, 42),
        tag: el.tagName.toLowerCase(),
        cls: (el.className.baseVal ?? el.className ?? '').toString().slice(0, 70),
        color: cs.color, bg: `rgb(${bg.map(Math.round).join(',')})`,
        size: Math.round(size), ratio: Math.round(got * 100) / 100, need,
      });
    }
  });
  return out;
}
"""


# Same maths as AUDIT_JS, applied to one element handed in from Python.
_ALL = "  const out = [];\n  document.querySelectorAll('*').forEach(el => {"
_ONE = "  const out = [];\n  [el].forEach(el => {"
AUDIT_ONE_JS = AUDIT_JS.replace("() => {", "(el) => {").replace(_ALL, _ONE)


def main() -> int:
    base = "http://localhost:5173"
    out_dir = Path(".design-review/theme")
    out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, object]] = []
    skipped: list[str] = []
    checked = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in THEMES:
            for route in ROUTES:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.add_init_script(f"localStorage.setItem('sieve-theme','{theme}')")
                page.goto(base + route, wait_until="load", timeout=45000)
                page.wait_for_timeout(1800)
                for row in page.evaluate(AUDIT_JS):
                    row["theme"], row["route"], row["state"] = theme, route, "rest"
                    failures.append(row)

                # Hover / focus / active are SEPARATE colour pairs, and each can
                # fail alone: CSS lets a rule change background without touching
                # colour, and nothing warns you. A white-on-white button hover
                # shipped past this audit because only the resting state was
                # ever measured.
                #
                # Playwright's hover()/focus() fire the real pseudo-classes, so
                # what is measured is what the stylesheet actually applies —
                # a class-based imitation would miss anything written against
                # :hover directly.
                controls = page.locator(
                    "button:not(:disabled), a[href], [role='button'], summary"
                )
                for i in range(min(controls.count(), 30)):
                    el = controls.nth(i)
                    for state in ("hover", "focus", "active"):
                        try:
                            if not el.is_visible():
                                break
                            el.hover(timeout=1200)
                            if state == "focus":
                                el.focus(timeout=1200)
                            if state == "active":
                                # Held down, so :active is live while measured.
                                page.mouse.down()
                            handle = el.element_handle()
                            if handle is not None:
                                checked += 1
                                bad = page.evaluate(AUDIT_ONE_JS, handle)
                                for row in bad:
                                    row["theme"], row["route"] = theme, route
                                    row["state"] = state
                                    failures.append(row)
                        except Exception as exc:
                            skipped.append(f"{route}/{theme}/{state}: {type(exc).__name__}")
                            continue
                        finally:
                            if state == "active":
                                with contextlib.suppress(Exception):
                                    page.mouse.up()

                slug = route.strip("/").replace("/", "_") or "landing"
                page.screenshot(path=str(out_dir / f"{theme}_{slug}.png"), full_page=True)
                ctx.close()
        browser.close()

    print(f"checked {checked} interaction-state samples; {len(skipped)} skipped")
    if skipped:
        # Loudly. A skip is not a pass, and treating it as one is how a
        # white-on-white hover survived a green audit.
        print("SKIPPED (these were NOT checked):")
        for line in sorted(set(skipped))[:12]:
            print("   ", line)
    if not failures:
        print("PASS: no contrast failures in either theme")
        return 1 if skipped else 0

    print(f"FAIL: {len(failures)} contrast failures\n")
    by_theme: dict[str, list[dict[str, object]]] = {}
    for f in failures:
        by_theme.setdefault(str(f["theme"]), []).append(f)
    for theme, rows in by_theme.items():
        print(f"--- {theme} ({len(rows)}) ---")
        seen = set()
        for r in rows:
            key = (r["cls"], r["color"], r["bg"])
            if key in seen:
                continue
            seen.add(key)
            print(
                f"  {r['ratio']:>5}:1 (need {r['need']})  {r['route']:<13} "
                f"{r['color']} on {r['bg']}  <{r['tag']}> {r['text']!r}"
            )
            print(f"        class: {r['cls']}")
    (out_dir / "contrast.json").write_text(json.dumps(failures, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
