from playwright.sync_api import Page

from jev_web_agent.models import ActionRecord, Element, Observation
from jev_web_agent.observe import MAX_ELEMENTS, MAX_TEXT_CHARS, observe, render_state


def test_extracts_visible_enabled_interactive_elements_in_document_order(
    page: Page, site: str
) -> None:
    page.goto(f"{site}/search.html")

    obs = observe(page)

    assert obs.url == f"{site}/search.html"
    assert obs.title == "Encyclopedia"
    assert [e.line() for e in obs.elements] == [
        'e1: searchbox "Search encyclopedia" value=""',
        'e2: button "Search"',
        'e3: link "About"',
        'e4: button "Random article"',
        'e5: combobox "Language" value="English"',
        'e6: textbox "Feedback" value=""',
    ]


def test_tags_each_element_with_its_data_jev_id(page: Page, site: str) -> None:
    page.goto(f"{site}/search.html")

    obs = observe(page)

    for element in obs.elements:
        tagged = page.locator(f'[data-jev-id="{element.id}"]')
        assert tagged.count() == 1
    assert page.locator('[data-jev-id="e2"]').inner_text() == "Search"


def test_hidden_and_disabled_elements_are_not_tagged(page: Page, site: str) -> None:
    page.goto(f"{site}/search.html")

    obs = observe(page)

    names = [e.name for e in obs.elements]
    assert "Hidden thing" not in names
    assert "Disabled thing" not in names
    assert page.locator("[data-jev-id]").count() == len(obs.elements)


def test_reobserving_retags_from_scratch(page: Page, site: str) -> None:
    page.goto(f"{site}/search.html")
    observe(page)
    page.evaluate("document.querySelector('a').remove()")

    obs = observe(page)

    assert [e.name for e in obs.elements][:3] == ["Search encyclopedia", "Search", "Random article"]
    assert page.locator("[data-jev-id]").count() == len(obs.elements)
    assert page.locator('[data-jev-id="e6"]').count() == 0


def test_typed_value_is_reported(page: Page, site: str) -> None:
    page.goto(f"{site}/search.html")
    page.fill("input[name=q]", "Alan Turing")

    obs = observe(page)

    assert obs.elements[0].line() == 'e1: searchbox "Search encyclopedia" value="Alan Turing"'


def test_caps_elements_and_prefers_the_viewport(page: Page, site: str) -> None:
    page.goto(f"{site}/many.html")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    obs = observe(page)

    names = [e.name for e in obs.elements]
    assert len(obs.elements) == MAX_ELEMENTS == 60
    assert "Link 100" in names  # in the viewport after scrolling, so it made the cut
    assert "Link 1" in names  # the remaining slots fill from the top of the document
    ids = [int(e.id[1:]) for e in obs.elements]
    assert ids == list(range(1, 61))  # numbered in document order
    link_numbers = [int(n.split()[1]) for n in names]
    assert link_numbers == sorted(link_numbers)


def test_visible_text_excerpt_is_capped_and_collapsed(page: Page, site: str) -> None:
    page.goto(f"{site}/many.html")

    obs = observe(page)

    assert obs.text.startswith("A long list Link 1 Link 2")
    assert "\n" not in obs.text

    page.evaluate("document.body.innerText = 'word '.repeat(5000)")
    assert len(observe(page).text) <= MAX_TEXT_CHARS


def _obs() -> Observation:
    return Observation(
        url="https://example.test/",
        title="Example",
        text="Hello world",
        elements=(
            Element(id="e1", role="textbox", name="Search", value=""),
            Element(id="e2", role="button", name="Go", value=None),
        ),
    )


def test_render_state_contains_every_part_jev_needs() -> None:
    history = [
        ActionRecord(
            operation="click", target_line=f'e{i}: link "L{i}"', text=None, url="https://x/"
        )
        for i in range(1, 8)
    ]

    state = render_state('find "cats"', _obs(), history)

    assert 'GOAL: find "cats"' in state
    assert "URL: https://example.test/" in state
    assert "TITLE: Example" in state
    assert "Hello world" in state
    assert 'e1: textbox "Search" value=""' in state
    assert 'e2: button "Go"' in state
    # only the last five actions, oldest first
    assert 'e2: link "L2"' not in state
    assert state.index('e3: link "L3"') < state.index('e7: link "L7"')


def test_render_state_with_no_history_or_elements() -> None:
    obs = Observation(url="about:blank", title="", text="", elements=())

    state = render_state("anything", obs, [])

    assert "(none yet)" in state
    assert "(no interactive elements)" in state
