# jev-web-agent

A small, readable example of driving a web browser with **Jev**, TypeSafe's System One model,
and nothing else. No LLM anywhere. When Jev isn't confident, a human picks.

## What Jev is

Jev does not generate text or actions. You send it a text `state` plus typed questions, and it
returns typed answers:

| Question | Answer | Used here for |
| --- | --- | --- |
| `Choice` | the chosen option, a probability for every option, and a `confidence` | which operation, which element, which text |
| `Noul` | one number: the probability the statement is true (a Noul has no separate confidence) | "is the goal done?", "is the next step risky?" |
| `Score` | ordered levels; not used here | |

It is text only, so the page is turned into text first. Docs: <https://docs.typesafe.ai/llms.txt>.

### Two backends, same model

| `--backend` | Endpoint | Key | Default model | Client |
| --- | --- | --- | --- | --- |
| `openrouter` | `POST https://openrouter.ai/api/alpha/decisions` | `OPENROUTER_API_KEY` | `typesafe/jev-1.13` | `openrouter.py`, a thin httpx2 adapter |
| `typesafe` | `POST https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` | `jev-latest` | the `typesafe-sdk` `TypeSafeClient` |

If you don't pass `--backend`, the agent uses `openrouter` when `OPENROUTER_API_KEY` is set and
`typesafe` otherwise. OpenRouter's Decisions API takes the same `model` / `state` / `questions`
request and returns the same `answers`, with two differences:

- `usage` also includes `cost` in USD.
- A choice answer only guarantees `choice`, so a missing `confidence` or `probabilities` is read
  as zero certainty, which fails the gate.

On OpenRouter the context limit is 32k tokens. A run on OpenRouter also writes `usage.json`,
with per-call latency, tokens and cost (call *n* is step *n*).

## How the loop works

Each step runs four stages:

```
observe ──► decide (1 Jev call) ──► gate ──► act ──┐
   ▲                                                │
   └────────────────────────────────────────────────┘
```

1. **Observe** (`observe.py`). Playwright tags each visible, enabled interactive element (links,
   buttons, inputs, textareas, selects, and ARIA buttons/links/textboxes/tabs/menu items) with
   `data-jev-id`. Tagging runs in document order and stops at 60 elements, taking in-viewport
   elements first. Each element becomes one line:

   ```
   e1: searchbox "Search encyclopedia" value=""
   e2: button "Search"
   e3: link "About"
   ```

   An `Observation` holds the url, the title, a text excerpt of up to 3,000 characters, and the
   elements. `render_state` turns it into the state text: goal, url, title, excerpt, element
   lines, and the last 5 actions.

2. **Decide** (`decide.py`). One Jev call asks all five questions together (speculative
   fan-out), and the code then uses only the answers it needs:
   - `operation` (Choice): `click`, `type`, `press_enter`, `scroll_down`, `go_back` or `done`
   - `target` (Choice): one option per element id, described by its element line
   - `text` (Choice): the goal's double-quoted literals plus `(none)`. **The agent can only
     ever type text you wrote in quotes in the goal.**
   - `goal_done` (Noul): "The goal has been achieved on the current page."
   - `risky` (Noul): "Taking this next step would buy, pay, send a message, post, delete,
     submit personal data, or log in."

   All Jev access goes through the `JevClient` protocol. `TypeSafeJev` is the real client, and
   the tests use a scripted fake.

3. **Gate** (`agent.py`). Code, not Jev, decides whether to act. The checks run in this order:
   - `goal_done ≥ 0.8`, or operation `done` passing the gate → **finish**.
   - `risky ≥ 0.3` → **blocked**. The step is never executed. You're asked whether you'll do
     it yourself in the browser (the agent then re-observes) or abort.
   - Every answer the chosen operation depends on (`operation`; plus `target` for click/type;
     plus `text` for type) needs probability ≥ 0.55 and confidence ≥ 0.35. If any answer falls
     short, the terminal shows Jev's **top 3** for that question and you pick one or abort.
     If what you pick differs from Jev's proposal, a second, narrow Jev call asks `risky`
     about your pick, described in the state as the next step. If that answer is ≥ 0.3, the
     pick is **blocked** too.
   - The same (operation, target, text, url) three times in a row → **ask** you.
   - If Jev returns an operation that isn't in the list, the run **aborts** and the report
     says why.
   - `--no-ask` aborts wherever it would have asked (for tests and CI). `--max-steps` defaults
     to 15.

4. **Act** (`act.py`). The target is re-queried by `data-jev-id` just before acting. If it has
   disappeared or been hidden, nothing is executed and the loop re-observes. Otherwise
   Playwright performs the action and waits for the page to load. Three cases get special
   handling:
   - A click or type that times out (for example, an overlay covers the target) re-observes
     instead of ending the run.
   - A click that opens a new tab is followed, and the agent observes the new tab from the
     next step.
   - After a `type`, `press_enter` presses Enter on the box that was typed into, not on
     whatever currently has focus.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run playwright install chromium
cp .env.example .env        # then fill in OPENROUTER_API_KEY=... (or TYPESAFE_API_KEY=...)
```

`.env` is loaded from the directory you run in, if it exists. Keys are never printed or written
to a report.

## Running

```sh
uv run jev-agent --task wiki     # en.wikipedia.org: search "Alan Turing", open his article
uv run jev-agent --task hn       # news.ycombinator.com: open the top story's comments
uv run jev-agent --task books    # books.toscrape.com: open "A Light in the Attic"

uv run jev-agent --goal 'Search for "rust borrow checker"' --url https://duckduckgo.com
```

The browser runs headed by default so you can watch it. Other useful flags: `--backend`,
`--headless`, `--no-ask`, `--max-steps N`, `--min-prob`, `--min-conf`, `--done-threshold`,
`--risky-threshold`, `--runs-dir`, `--model`.

The terminal prints one line per step:

```
step 3 │ click │ e2: link "Alan Turing" (p 0.72, conf 0.58) │ click 0.72  type 0.06  press_enter 0.06 │ conf 0.66 │ done 0.02 risky 0.01 │ PASS all relevant answers passed
```

The fields are: step number, chosen operation, target element with its probability and
confidence, the operation's top 3, the operation's confidence, the two Noul values, and the
gate result.

The exit code is 0 when the goal was reached, 1 for any other ending (aborted, blocked,
max steps, error), and 2 when the API key is missing.

## Reading a report

Every run writes `runs/<YYYY-MM-DD_HH-MM-SS>/`:

- `report.html`: a self-contained page, with no scripts and no external assets. It opens with
  the goal, the final status and why, the start URL, and the gate thresholds. Each step then
  shows:
  - the **screenshot** Jev's state was built from (`step-NN.png`, in the same folder)
  - the **elements sent**: the exact element lines Jev chose among
  - the **gate decision** (PASS / DONE / BLOCK / ASK) and its reason, any human input, the
    action executed, and what happened
  - **Jev's full answers**: every option's probability for `operation`, `target` and `text`,
    with the chosen option in bold and its confidence, plus both Noul values
  - the exact state text sent to Jev (collapsed)
- `run.json`: the same data, machine-readable.
- `usage.json` (OpenRouter runs only): latency, input and output tokens, and cost for each call,
  plus totals.

To diagnose a bad step, look at the answers. A flat distribution with low confidence means Jev
couldn't tell the options apart, so check the element lines. A confident wrong answer usually
means the goal wording or an element name is misleading.

## Development

```sh
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

Unit tests are hermetic. They use a scripted fake `JevClient` and local HTML fixtures from
`tests/fixtures/`, served by a small local HTTP server. The loop tests call `cli.run` exactly as
`jev-agent` does and inject only the fake Jev (and a scripted human where one is asked). The
real SDK adapter is tested against the real `typesafe-sdk` client with only its HTTP transport
replaced: the TypeSafe SDK and `OpenRouterJev` both run for real over a mock transport, and one
test runs the whole CLI on the OpenRouter backend that way. Every test runs with provider keys
removed from the environment and a working directory that has no `.env`. `tests/test_live.py`
has one real call per backend. Each is skipped unless its key is set; if `.env` has
`OPENROUTER_API_KEY`, the OpenRouter one runs as part of `pytest`.
