"""Shared capture helpers. Import these instead of calling page.screenshot().

WHY THIS EXISTS. `page.screenshot()` is viewport-only unless `full_page=True`,
and `full_page` was never set in any of 19 capture scripts (findings.md
2026-08-14). Every pixel-delta measurement in the design funnel therefore saw
only the initial static viewport. A blind region produces FALSE NEGATIVES —
motion it cannot see reads as no motion — so four conclusions had to be
reopened, including a universal negative.

Recording the lesson would not have helped: 19 of 19 scripts got it wrong, so
the 20th would too. The default is fixed here instead. Same shape as the
migration-head check and the no_foreign_db_sessions rename — the discovery
needed a human, the guard should not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def shoot(page: Any, path: str | Path, *, full_page: bool = True) -> None:
    """Screenshot, full-page by DEFAULT. Pass full_page=False deliberately."""
    page.screenshot(path=str(path), full_page=full_page)


def shoot_element(page: Any, selector: str, path: str | Path) -> bool:
    """Screenshot one element, scrolled into view first.

    The right instrument when measuring a specific component: it cannot be
    fooled by page position, and it fails loudly (returns False) when the
    target is absent rather than silently measuring blank pixels.
    """
    el = page.query_selector(selector)
    if el is None:
        return False
    el.scroll_into_view_if_needed()
    el.screenshot(path=str(path))
    return True


def assert_in_frame(page: Any, selector: str, *, full_page: bool = True) -> dict[str, Any]:
    """Fail loudly if the thing being measured is outside the captured region.

    This is the guard the audit was missing. A measurement that silently
    reads zero because its subject is below the fold is worse than no
    measurement, because it is recorded as evidence.
    """
    box = page.evaluate(
        """(sel) => { const e = document.querySelector(sel); if (!e) return null;
             const r = e.getBoundingClientRect();
             return {top: r.top + window.scrollY, bottom: r.bottom + window.scrollY,
                     h: r.height, vh: window.innerHeight,
                     pageH: document.documentElement.scrollHeight}; }""",
        selector,
    )
    if box is None:
        raise AssertionError(f"capture target {selector!r} is not in the DOM")
    limit = box["pageH"] if full_page else box["vh"]
    if box["bottom"] > limit or box["h"] == 0:
        raise AssertionError(
            f"capture target {selector!r} is outside the captured region: "
            f"bottom={box['bottom']:.0f} limit={limit:.0f} height={box['h']:.0f}. "
            f"{'Scroll it into view' if not full_page else 'Target has no box'}."
        )
    return box


def montage_crop(image: Any, crop: tuple[int, int, int, int], *, log: list[str]) -> Any:
    """Crop for DISPLAY only, and record the crop next to the output.

    The choreography case lost one survivor to the viewport default and
    another to an unlogged montage crop. A crop applied uniformly across a
    frame series is invisible BECAUSE it is uniform, so it has to be stated
    wherever the series is shown.
    """
    log.append(f"crop={crop} from source size {image.size}")
    return image.crop(crop)
