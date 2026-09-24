import pytest
from playwright.sync_api import Page

import jev_web_agent.act as act_module
from jev_web_agent.act import act
from jev_web_agent.models import Action
from jev_web_agent.observe import observe


def _id_of(page: Page, name: str) -> str:
    return next(e.id for e in observe(page).elements if e.name == name)


def test_click_follows_a_link_and_waits_for_the_page(page: Page, site: str) -> None:
    page.goto(f"{site}/results.html")

    result = act(page, Action("click", target_id=_id_of(page, "Alan Turing")))

    assert result.executed
    assert page.url == f"{site}/article.html"
    assert page.title() == "Alan Turing - Encyclopedia"


def test_type_fills_the_tagged_input_and_press_enter_submits(page: Page, site: str) -> None:
    page.goto(f"{site}/search.html")
    box = _id_of(page, "Search encyclopedia")

    typed = act(page, Action("type", target_id=box, text="Alan Turing"))
    assert typed.executed
    assert page.input_value("input[name=q]") == "Alan Turing"

    entered = act(page, Action("press_enter"))
    assert entered.executed
    assert page.url == f"{site}/results.html?q=Alan+Turing"


def test_scroll_down_moves_the_viewport(page: Page, site: str) -> None:
    page.goto(f"{site}/many.html")

    act(page, Action("scroll_down"))

    assert page.evaluate("window.scrollY") > 0


def test_go_back_returns_to_the_previous_page(page: Page, site: str) -> None:
    page.goto(f"{site}/search.html")
    page.goto(f"{site}/article.html")

    act(page, Action("go_back"))

    assert page.url == f"{site}/search.html"


def test_element_gone_is_not_acted_on(page: Page, site: str) -> None:
    page.goto(f"{site}/vanish.html")
    ghost = _id_of(page, "Ghost button")
    page.evaluate("document.getElementById('ghost').remove()")

    result = act(page, Action("click", target_id=ghost))

    assert not result.executed
    assert result.gone
    assert page.title() == "Vanishing button"


def test_element_hidden_since_observation_counts_as_gone(page: Page, site: str) -> None:
    page.goto(f"{site}/vanish.html")
    ghost = _id_of(page, "Ghost button")
    page.evaluate("document.getElementById('ghost').style.display = 'none'")

    result = act(page, Action("click", target_id=ghost))

    assert result.gone
    assert page.title() == "Vanishing button"


def test_element_still_there_is_clicked(page: Page, site: str) -> None:
    page.goto(f"{site}/vanish.html")

    result = act(page, Action("click", target_id=_id_of(page, "Ghost button")))

    assert result.executed
    assert page.title() == "CLICKED"


def test_click_blocked_by_an_overlay_times_out_without_raising(
    page: Page, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(act_module, "ACTION_TIMEOUT_MS", 500)
    page.goto(f"{site}/overlay.html")

    result = act(page, Action("click", target_id=_id_of(page, "Covered button")))

    assert not result.executed
    assert "timed out" in result.note
    assert page.title() == "Covered button"
