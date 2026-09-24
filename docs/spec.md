# jev-web-agent — design spec

Approved 24/09/2026. A small, readable example of driving a web browser with
**Jev** (TypeSafe's System One model) and nothing else.

## What Jev is (constraints that shape everything)

- Jev does **not** generate text or actions. It takes a text `state` plus typed
  questions and returns typed answers: `choice` (one of N options, with
  per-option probabilities), `score` (ordered levels), `noul` (probability a
  statement is true). Every answer carries a `confidence`.
- Text only — no images. The page must be turned into text.
- Endpoint `POST https://api.typesafe.ai/v1/systemone`, Python SDK
  `typesafe-sdk` (`TypeSafeClient` / `AsyncTypeSafeClient`, `Choice`, `Score`,
  `Noul`), env `TYPESAFE_API_KEY`, model `jev-latest` (= `jev-1.13.0`).
- Context: 64k tokens per request; 32k for `state` + the longest question.
- Docs index: https://docs.typesafe.ai/llms.txt — read `api.md`,
  `sdk/python/api/types/responses.md`, `primitives/*.md`, `confidence.md`
  before coding against the response shape. Do not guess field names.

## Scope

Pure Jev. No LLM anywhere. Where Jev isn't confident, **a human** picks.

## Loop (one step)

1. **Observe** (`observe.py`) — Playwright injects `data-jev-id` on visible,
   enabled interactive elements (links, buttons, inputs, textareas, selects,
   `[role=button|link|textbox|searchbox|combobox|tab|menuitem]`), in document
   order, capped at 60 (prefer in-viewport first). Each becomes one line:
   `e12: button "Search"`, `e7: textbox "Search Wikipedia" value=""`.
   `Observation` = url, title, visible-text excerpt (capped ~3k chars),
   elements list. Pure data; rendering to Jev state text is a separate function.
2. **Decide** (`decide.py`) — ONE Jev call per step (speculative fan-out):
   - `operation` Choice: `click`, `type`, `press_enter`, `scroll_down`,
     `go_back`, `done` — each with a descriptive criterion.
   - `target` Choice over element ids, criterion = the element line.
   - `text` Choice over the goal's double-quoted literals (`search for "Alan
     Turing"`) plus `(none)`. The agent can only ever type text the user wrote.
   - `goal_done` Noul: "The goal has been achieved on the current page."
   - `risky` Noul: "Taking this next step would buy, pay, send a message,
     post, delete, submit personal data, or log in."
   State = goal, url, title, page excerpt, element lines, last 5 actions.
   Behind a `JevClient` Protocol so tests use a fake.
3. **Gate** (`agent.py`) — thresholds configurable, defaults: probability
   ≥ 0.55 and confidence ≥ 0.35 on each answer the chosen action depends on
   (`operation`; plus `target` for click/type; plus `text` for type).
   - `goal_done` ≥ 0.8 (or operation `done` passing the gate) → finish.
   - `risky` ≥ 0.3 → blocked, never executed; ask the human.
   - Below threshold → terminal shows Jev's top-3 for each relevant question;
     human picks one or aborts. `--no-ask` aborts instead (for tests/CI).
   - Same (operation, target, url) 3× in a row → ask the human.
   - `--max-steps` default 15.
4. **Act** (`act.py`) — re-query the element by `data-jev-id`; if gone,
   re-observe instead of acting. Execute via Playwright; wait for load state.

## Watching

- Headed Chromium by default (`--headless` flag available).
- `rich` terminal line per step: step #, operation, target line, top-3
  probabilities, confidence, gate result.
- `runs/<YYYY-MM-DD_HH-MM-SS>/report.html` — self-contained (screenshots
  embedded as base64 or relative PNGs in the same dir), per step: screenshot,
  the element list sent, Jev's full answers, gate decision. Also `run.json`.

## CLI

`uv run jev-agent --task wiki|hn|books` or
`uv run jev-agent --goal '...' --url https://...`. Starter tasks (read-only
public sites):
- `wiki`: start https://en.wikipedia.org — goal: search for "Alan Turing",
  open his article, done when the article is showing.
- `hn`: start https://news.ycombinator.com — open the comments page of the top
  story.
- `books`: start https://books.toscrape.com — open the book page for
  "A Light in the Attic".

## Engineering

- Python 3.12, uv, `pytest`, `ruff`, `pyright` (basic or strict). Types at
  seams (dataclasses / TypedDict).
- TDD. Unit tests are hermetic: fake `JevClient`, local HTML fixtures served
  from `tests/fixtures/` (file:// or a tiny local http server). Cover: element
  extraction + id tagging, state rendering, quoted-literal extraction, every
  gate branch (pass, low confidence → ask, risky → block, loop → ask, done),
  act re-validation, report written.
- One live smoke test, skipped unless `TYPESAFE_API_KEY` is set.
- `.env` loaded if present (python-dotenv); never log or print the key.
- README: what Jev is, how the loop works, setup, running, reading a report.
