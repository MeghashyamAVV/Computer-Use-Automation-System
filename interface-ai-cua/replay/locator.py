from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.schema import Locator, LocatorStrategy, ResolvedTarget


class LocatorResolutionError(Exception):
    pass


@dataclass
class ResolvedLocatorResult:
    playwright_locator: any
    strategy_used: str
    frame_path: list[str]


def _frame_context(page, frame_path: list[str]):
    ctx = page
    for label in frame_path:
        found = None
        for fl in ctx.frames if hasattr(ctx, "frames") else page.frames:
            if fl.name == label or fl.url == label or (fl.title() if hasattr(fl, "title") else "") == label:
                found = fl
                break
        if found is None:
            for fl in page.frames:
                if label and label in (fl.name, fl.url):
                    found = fl
                    break
        if found is None:
            raise LocatorResolutionError(f"could not find frame '{label}' in frame_path {frame_path}")
        ctx = found
    return ctx


def _try_one(ctx, loc: Locator):
    if loc.strategy in (LocatorStrategy.ROLE_NAME, "role_name"):
        if not loc.role or not loc.value:
            return None
        return ctx.get_by_role(loc.role, name=loc.value, exact=loc.exact)
    if loc.strategy in (LocatorStrategy.LABEL, "label"):
        return ctx.get_by_label(loc.value, exact=loc.exact)
    if loc.strategy in (LocatorStrategy.TEXT, "text"):
        return ctx.get_by_text(loc.value, exact=loc.exact)
    if loc.strategy in (LocatorStrategy.CSS, "css"):
        return ctx.locator(loc.value)
    if loc.strategy in (LocatorStrategy.XPATH, "xpath"):
        return ctx.locator(f"xpath={loc.value}")
    return None


def resolve_target(page, target: ResolvedTarget, timeout_ms: int = 3000) -> ResolvedLocatorResult:
    candidates: list[Locator] = [target.primary, *target.fallbacks]
    last_error: Optional[Exception] = None

    for loc in candidates:
        try:
            ctx = _frame_context(page, loc.frame_path)
            pw_locator = _try_one(ctx, loc)
            if pw_locator is None:
                continue
            pw_locator.first.wait_for(state="visible", timeout=timeout_ms)
            return ResolvedLocatorResult(
                playwright_locator=pw_locator.first,
                strategy_used=str(loc.strategy),
                frame_path=loc.frame_path,
            )
        except Exception as e:  
            last_error = e
            continue

    raise LocatorResolutionError(
        f"could not resolve target '{target.description}' using any of "
        f"{[str(c.strategy) for c in candidates]}; last error: {last_error}"
    )
