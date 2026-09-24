"""Observe: turn the live page into text-only data Jev can read.

``observe`` tags every visible, enabled interactive element with ``data-jev-id`` and returns an
``Observation``. ``render_state`` is a separate, pure function that formats an observation (plus
the goal and recent actions) as the ``state`` string sent to Jev.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict, cast

from playwright.sync_api import Page

from jev_web_agent.models import ActionRecord, Element, Observation

MAX_ELEMENTS = 60
MAX_TEXT_CHARS = 3000
MAX_NAME_CHARS = 80
HISTORY_IN_STATE = 5

INTERACTIVE_SELECTOR = ", ".join(
    [
        "a[href]",
        "button",
        "input",
        "textarea",
        "select",
        *(
            f'[role="{role}"]'
            for role in ("button", "link", "textbox", "searchbox", "combobox", "tab", "menuitem")
        ),
    ]
)

# Runs in the page. Clears old tags, collects candidates in document order, keeps at most
# `maxElements` (in-viewport ones first, the rest from the top of the document), re-sorts the
# kept ones into document order and tags them e1..eN.
_TAG_ELEMENTS_JS = r"""
([selector, maxElements, maxName]) => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const BUTTON_INPUTS = ["submit", "button", "reset", "image"];

  const roleOf = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName;
    if (tag === "A") return "link";
    if (tag === "BUTTON") return "button";
    if (tag === "SELECT") return "combobox";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (BUTTON_INPUTS.includes(type)) return "button";
      if (type === "checkbox" || type === "radio") return type;
      if (type === "search") return "searchbox";
      if (type === "range") return "slider";
      return "textbox";
    }
    return tag.toLowerCase();
  };

  const nameOf = (el) => {
    let name = clean(el.getAttribute("aria-label"));
    if (!name && el.getAttribute("aria-labelledby")) {
      name = clean(el.getAttribute("aria-labelledby").split(/\s+/)
        .map((id) => document.getElementById(id)?.innerText || "").join(" "));
    }
    if (!name && el.labels && el.labels.length) {
      name = clean(Array.from(el.labels).map((l) => l.innerText).join(" "));
    }
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (!name && el.tagName === "INPUT" && BUTTON_INPUTS.includes(type)) name = clean(el.value);
    if (!name && !["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) name = clean(el.innerText);
    if (!name) name = clean(el.getAttribute("placeholder"));
    if (!name) name = clean(el.getAttribute("title"));
    if (!name) name = clean(el.querySelector("img[alt]")?.getAttribute("alt"));
    if (!name) name = clean(el.getAttribute("name"));
    name = name.replace(/"/g, "'");
    return name.length > maxName ? name.slice(0, maxName - 1) + "…" : name;
  };

  const valueOf = (el) => {
    if (el.tagName === "SELECT") return clean(el.selectedOptions[0]?.text || "");
    if (el.tagName === "TEXTAREA") return el.value;
    if (el.tagName === "INPUT") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (BUTTON_INPUTS.includes(type) || type === "checkbox" || type === "radio") return null;
      if (type === "password") return el.value ? "••••" : "";
      return el.value;
    }
    return null;
  };

  document.querySelectorAll("[data-jev-id]").forEach((el) => el.removeAttribute("data-jev-id"));

  const vw = window.innerWidth, vh = window.innerHeight;
  const candidates = [];
  for (const el of document.querySelectorAll(selector)) {
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (el.tagName === "INPUT" && type === "hidden") continue;
    if (el.disabled || el.getAttribute("aria-disabled") === "true") continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    if (!el.checkVisibility({ visibilityProperty: true })) continue;
    const inView = rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw;
    candidates.push({ el, inView });
  }

  let chosen = candidates;
  if (candidates.length > maxElements) {
    const inView = candidates.filter((c) => c.inView).slice(0, maxElements);
    const rest = candidates.filter((c) => !c.inView).slice(0, maxElements - inView.length);
    const keep = new Set([...inView, ...rest]);
    chosen = candidates.filter((c) => keep.has(c));
  }

  return chosen.map((c, i) => {
    const id = "e" + (i + 1);
    c.el.setAttribute("data-jev-id", id);
    return { id, role: roleOf(c.el), name: nameOf(c.el), value: valueOf(c.el) };
  });
}
"""

_TEXT_JS = r"""
(maxChars) => ((document.body && document.body.innerText) || "")
  .replace(/\s+/g, " ").trim().slice(0, maxChars)
"""


class _RawElement(TypedDict):
    id: str
    role: str
    name: str
    value: str | None


def observe(page: Page) -> Observation:
    """Tag the page's interactive elements and capture url, title, text excerpt and elements."""
    raw = cast(
        list[_RawElement],
        page.evaluate(_TAG_ELEMENTS_JS, [INTERACTIVE_SELECTOR, MAX_ELEMENTS, MAX_NAME_CHARS]),
    )
    text = cast(str, page.evaluate(_TEXT_JS, MAX_TEXT_CHARS))
    return Observation(
        url=page.url,
        title=page.title(),
        text=text,
        elements=tuple(
            Element(id=r["id"], role=r["role"], name=r["name"], value=r["value"]) for r in raw
        ),
    )


def render_state(goal: str, obs: Observation, history: Sequence[ActionRecord]) -> str:
    """Format everything Jev needs for one decision as a single text ``state``."""
    elements = "\n".join(e.line() for e in obs.elements) or "(no interactive elements)"
    recent = list(history)[-HISTORY_IN_STATE:]
    actions = (
        "\n".join(f"{i}. {a.describe()}" for i, a in enumerate(recent, start=1)) or "(none yet)"
    )
    return (
        f"GOAL: {goal}\n"
        f"URL: {obs.url}\n"
        f"TITLE: {obs.title}\n"
        f"\nPAGE TEXT (excerpt):\n{obs.text or '(empty)'}\n"
        f'\nINTERACTIVE ELEMENTS (id: role "name"):\n{elements}\n'
        f"\nLAST ACTIONS (oldest first):\n{actions}\n"
    )
