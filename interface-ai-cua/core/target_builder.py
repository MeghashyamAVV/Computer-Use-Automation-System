from __future__ import annotations

from core.observation import ObservedElement
from core.schema import Locator, LocatorStrategy, ResolvedTarget

_DATA_CELL_ROLES = {"cell", "columnheader", "rowheader", "gridcell"}


def build_target(el: ObservedElement, description: str) -> ResolvedTarget:
    is_data_cell = el.role in _DATA_CELL_ROLES
    fallbacks: list[Locator] = []

    if el.tag in ("input", "select", "textarea") and el.name:
        fallbacks.append(Locator(strategy=LocatorStrategy.LABEL, value=el.name, frame_path=el.frame_path))

    if el.name and not is_data_cell:
        fallbacks.append(Locator(strategy=LocatorStrategy.TEXT, value=el.name, frame_path=el.frame_path, exact=False))

    if is_data_cell:
        primary = Locator(strategy=LocatorStrategy.CSS, value=el.css_path, frame_path=el.frame_path)
    else:
        if el.css_path:
            fallbacks.append(Locator(strategy=LocatorStrategy.CSS, value=el.css_path, frame_path=el.frame_path))
        primary = Locator(
            strategy=LocatorStrategy.ROLE_NAME,
            value=el.name,
            role=el.role,
            frame_path=el.frame_path,
            exact=False,
        )

    return ResolvedTarget(description=description, primary=primary, fallbacks=fallbacks)
