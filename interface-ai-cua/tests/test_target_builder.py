from core.observation import ObservedElement
from core.schema import LocatorStrategy
from core.target_builder import build_target


def test_build_target_prefers_role_name_primary():
    el = ObservedElement(
        index=0, role="button", name="Search", tag="button", value=None,
        visible=True, css_path="form > button", href=None, frame_path=[],
    )
    target = build_target(el, description="Search button")
    assert target.primary.strategy == LocatorStrategy.ROLE_NAME
    assert target.primary.role == "button"
    assert target.primary.value == "Search"


def test_build_target_includes_fallback_chain_with_css_last():
    el = ObservedElement(
        index=0, role="textbox", name="Member ID", tag="input", value=None,
        visible=True, css_path="form > input:nth-of-type(1)", href=None, frame_path=[],
    )
    target = build_target(el, description="Member ID field")
    strategies = [f.strategy for f in target.fallbacks]
    assert LocatorStrategy.LABEL in strategies
    assert LocatorStrategy.CSS in strategies
    assert strategies[-1] == LocatorStrategy.CSS  # structural locator is always the last resort


def test_build_target_carries_frame_path_for_iframe_elements():
    el = ObservedElement(
        index=0, role="cell", name="Savings", tag="td", value=None,
        visible=True, css_path="table > tr > td", href=None, frame_path=["Account balances"],
    )
    target = build_target(el, description="Savings balance cell")
    assert target.primary.frame_path == ["Account balances"]
    assert all(f.frame_path == ["Account balances"] for f in target.fallbacks)


def test_build_target_uses_structural_locator_as_primary_for_data_cells():
    # A VALUE cell's accessible name IS the data (e.g. "$4,210.55"). Using
    # role+name as primary would only ever match this one recorded value.
    el = ObservedElement(
        index=0, role="cell", name="$4,210.55", tag="td", value=None,
        visible=True, css_path="table > tbody > tr:nth-of-type(2) > td:nth-of-type(2)",
        href=None, frame_path=["Account balances"],
    )
    target = build_target(el, description="Savings balance value cell")
    assert target.primary.strategy == LocatorStrategy.CSS
    assert target.primary.value == el.css_path
    # role_name must NOT appear anywhere in the chain for a data cell --
    # it would only ever match this exact recorded balance again.
    assert not any(f.strategy == LocatorStrategy.ROLE_NAME for f in target.fallbacks)


def test_build_target_still_prefers_role_name_for_non_cell_elements_near_cells():
    # Sanity check the exception is scoped to cell/columnheader/etc roles only.
    el = ObservedElement(
        index=0, role="link", name="View Member", tag="a", value=None,
        visible=True, css_path="table > tr > td > a", href="/member/12345", frame_path=[],
    )
    target = build_target(el, description="View Member link")
    assert target.primary.strategy == LocatorStrategy.ROLE_NAME
