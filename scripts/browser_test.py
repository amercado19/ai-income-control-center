#!/usr/bin/env python3
"""Drive the built dashboard in a real browser and assert it actually works.

``verify_site`` checks that the required markers are present in the HTML. That is a necessary
gate but a weak one: it cannot tell whether the page renders, whether a nav item leads anywhere,
or whether a JavaScript error empties a section on load. This does, by loading the built file in
headless Chromium, visiting every nav destination at both a desktop and a phone viewport, and
failing on the first console error.

Dev-only. Playwright is not a runtime dependency, and nothing in the shipped package imports it.

    python3 scripts/browser_test.py [--site site] [--shots runs/screens]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}  # iPhone 14 logical size

# Every nav destination, and a string that must appear on it once rendered. The marker is
# deliberately content rather than chrome: a page that renders its heading and nothing else is
# the exact failure this is meant to catch.
PAGES: list[tuple[str, str]] = [
    ("overview", "REVENUE TODAY"),
    ("approvals", ""),
    ("opportunities", ""),
    ("proposals", ""),
    ("jobs", ""),
    ("deliverables", ""),
    ("clients", ""),
    ("revenue", ""),
    ("analytics", ""),
    ("fiverr", "Fiverr launch center"),
    ("portfolio", "Evidence a client can check"),
    ("automation", "Connectors"),
    ("health", ""),
    ("settings", "Operator profile"),
    ("audit", ""),
]

# Google Fonts is the one external request the page makes, and it is expected to fail in a
# sandbox with no network. Chromium reports that as a bare "Failed to load resource: net::ERR_..."
# console line with no URL in it, so the URL has to come from the requestfailed event instead -
# matching the error text alone would mean ignoring every subresource failure, including a real one.
FONT_HOST = re.compile(r"fonts\.(googleapis|gstatic)\.com", re.I)
SUBRESOURCE_FAILURE = re.compile(r"Failed to load resource", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="site")
    ap.add_argument("--shots", default="runs/screens")
    args = ap.parse_args()

    index = Path(args.site) / "index.html"
    if not index.exists():
        print(f"FAIL: {index} does not exist. Run `python -m aicc build` first.")
        return 2
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright is not installed. `pip install playwright --break-system-packages`")
        return 0

    failures: list[str] = []
    url = index.resolve().as_uri()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for label, viewport in (("desktop", DESKTOP), ("phone", PHONE)):
            ctx = browser.new_context(viewport=viewport, device_scale_factor=2)
            page = ctx.new_page()
            errors: list[str] = []
            failed_urls: list[str] = []
            # Bound as defaults rather than closed over: the lists are rebound each viewport,
            # and a late-binding closure would collect the phone run's errors into the desktop run.
            page.on("console", lambda m, acc=errors: acc.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e, acc=errors: acc.append(str(e)))
            page.on("requestfailed", lambda r, acc=failed_urls: acc.append(r.url))
            page.goto(url, wait_until="load")

            nav_count = page.locator(".nav-item").count()
            if nav_count < len(PAGES):
                failures.append(f"{label}: {nav_count} nav items rendered, expected {len(PAGES)}")

            for name, marker in PAGES:
                page.evaluate(f"go({name!r})")
                page.wait_for_timeout(90)
                active = page.locator(f"#page-{name}.active")
                if active.count() != 1:
                    failures.append(f"{label}/{name}: page did not become active")
                    continue
                # inner_text applies CSS text-transform, and section titles are uppercased,
                # so the marker comparison has to be case-insensitive or it tests the stylesheet.
                text = active.inner_text()
                if len(text.strip()) < 40:
                    failures.append(f"{label}/{name}: rendered {len(text.strip())} chars - looks empty")
                if marker and marker.lower() not in text.lower():
                    failures.append(f"{label}/{name}: missing expected content {marker!r}")
                page.screenshot(path=str(shots / f"{label}-{name}.png"), full_page=(label == "phone"))

            # A horizontal scrollbar at phone width is a layout failure, not a style quibble:
            # it means a table or a fixed width is pushing the page sideways on a real device.
            if label == "phone":
                overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
                if overflow:
                    sw = page.evaluate("document.documentElement.scrollWidth")
                    failures.append(f"phone: page scrolls horizontally ({sw}px wide at {viewport['width']}px)")

            non_font_failures = [u for u in failed_urls if not FONT_HOST.search(u)]
            real = []
            for e in errors:
                if FONT_HOST.search(e):
                    continue
                # A bare subresource failure is only excusable when the only requests that
                # failed were the fonts. If anything else failed, it is a genuine error.
                if SUBRESOURCE_FAILURE.search(e) and not non_font_failures:
                    continue
                real.append(e)
            for u in non_font_failures[:5]:
                failures.append(f"{label}: request failed: {u[:160]}")
            for e in real[:5]:
                failures.append(f"{label}: console error: {e[:160]}")
            ctx.close()
        browser.close()

    if failures:
        print(f"BROWSER TEST FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"BROWSER TEST PASSED: {len(PAGES)} pages x 2 viewports. Screenshots in {shots}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
