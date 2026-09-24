# jev-web-agent

A small, readable web agent driven only by **Jev**, TypeSafe's System One decision model, with
no LLM anywhere. Jev never writes text or actions. On each step the agent turns the page into
text, asks Jev five typed questions in one call (which operation, which element, which quoted
text, is the goal done, is the next step risky), and gets back calibrated probabilities. Code
then decides whether those answers are good enough to act on. When they aren't, a human picks
from Jev's top three, and risky steps are never executed.

![A run report: the step card shows the page Jev saw and its answers, with the chosen link (e57, "Guido van Rossum") at p 1.00](docs/images/report.png)

*A real run's `report.html`: step 4 of "search for "Python", get to the article about the
programming language (not the snake), then open the article about its creator". The report
shows each step's screenshot, the elements sent to Jev, every answer's probabilities and
confidence, and the gate's decision.*

How it works in detail, with diagrams, a real request and response, and live results:
**[docs/design.md](docs/design.md)**.

## Quickstart

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Shai-Koffman/jev-web-agent.git
cd jev-web-agent
uv sync
uv run playwright install chromium
cp .env.example .env
```

Put one key in `.env`:

- `OPENROUTER_API_KEY=...`: Jev on OpenRouter as `typesafe/jev-1.13` (get a key at
  <https://openrouter.ai/settings/keys>), or
- `TYPESAFE_API_KEY=...`: Jev on TypeSafe's own API.

Then run a starter task and watch the browser:

```sh
uv run jev-agent --task wiki     # en.wikipedia.org: search "Alan Turing", open his article
uv run jev-agent --task hn       # news.ycombinator.com: open the top story's comments
uv run jev-agent --task books    # books.toscrape.com: open "A Light in the Attic"
```

Or give it your own goal. Put any text it may type in double quotes:

```sh
uv run jev-agent --goal 'search for "Ada Lovelace" and open her article' --url https://en.wikipedia.org
```

## See it run

The terminal prints one line per step: the operation, the target element with its probability
and confidence, the operation's top three, the two Noul values, and the gate's verdict. This is
the Python run replayed from its `run.json`:

![Terminal output of a five-step run: type, press_enter, click, click, done](docs/images/terminal.svg)

The same run in the browser:

| Step 3: the disambiguation page | Step 4: the programming language | Step 5: goal reached |
| --- | --- | --- |
| ![Wikipedia's "Python" disambiguation page](docs/images/python-disambiguation.png) | ![Wikipedia's "Python (programming language)" article](docs/images/python-language-article.png) | ![Top of Wikipedia's "Guido van Rossum" article](docs/images/guido-article.png) |

On step 3 Jev picked `e37: link "Python (programming language)"` with p 0.97, choosing it over
the snakes. On step 4 it picked `e57: link "Guido van Rossum"` with p 1.00.

## Safety

This is an example, not a product. Read this before pointing it anywhere.

- **It only types what you quoted.** The only text the agent can ever type is a double-quoted
  literal from your goal. Jev chooses among those literals and cannot invent text. Code checks
  it again before every `type`, including one a human picked, and aborts the run if it doesn't
  match.
- **Risky steps are blocked.** Every step asks Jev whether the next step would buy, pay, send
  a message, post, delete, submit personal data or log in. At a probability of 0.3 or more the
  agent does not execute it. When a human overrides Jev's choice, that pick gets its own risk
  check.
- **Unsure means stop.** Low probability or confidence, or the same action three times, stops
  and asks a human. With `--no-ask` it aborts instead.
- **Don't use it where one click can buy or send.** The risk check is a model's probability,
  not a guarantee. Use read-only sites, and never run it logged in to accounts that can spend
  money or send messages.
- Keys are loaded from `.env`, which is gitignored. They are never printed or written to a
  report.

## How it works

```
observe ──► decide (1 Jev call) ──► gate ──► act ──┐
   ▲                                                │
   └────────────────────────────────────────────────┘
```

1. **Observe** (`observe.py`). Playwright tags visible, enabled interactive elements with
   `data-jev-id`. Tagging runs in document order and stops at 60 elements, taking in-viewport
   elements first. Each element becomes one line, such as `e3: searchbox "Search Wikipedia"
   value=""`. The state is the goal, url, title, a text excerpt of up to 3,000 characters, the
   element lines, and the last 5 actions.
2. **Decide** (`decide.py`). One Jev call asks five questions at once:
   - `operation` (Choice): `click`, `type`, `press_enter`, `scroll_down`, `go_back` or `done`
   - `target` (Choice): one option per element id
   - `text` (Choice): the goal's quoted literals, plus `(none)`
   - `goal_done` (Noul)
   - `risky` (Noul)
3. **Gate** (`agent.py`). The checks run in this order:
   - `goal_done ≥ 0.8`, or a confident `done` → finish.
   - An unknown operation → abort.
   - `risky ≥ 0.3` → block.
   - Each answer the chosen operation depends on needs probability ≥ 0.55 and
     confidence ≥ 0.35. Otherwise → ask a human.
   - The same action three times in a row → ask a human.
4. **Act** (`act.py`). The target is re-queried by `data-jev-id` just before acting; if it has
   gone, the agent re-observes. Clicks that time out also re-observe. A new tab opened by a
   link is followed. After a `type`, Enter goes to the box that was typed into.

The full walk-through, with Mermaid diagrams, is in [docs/design.md](docs/design.md). The
original spec is [docs/spec.md](docs/spec.md).

## Backends

| `--backend` | Endpoint | Key | Default model |
| --- | --- | --- | --- |
| `openrouter` | `POST https://openrouter.ai/api/alpha/decisions` | `OPENROUTER_API_KEY` | `typesafe/jev-1.13` |
| `typesafe` | `POST https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` | `jev-latest` |

If you don't pass `--backend`, the agent uses `openrouter` when `OPENROUTER_API_KEY` is set and
`typesafe` otherwise. Both backends sit behind the same `JevClient` protocol. OpenRouter runs
also write `usage.json`, with per-call latency, tokens and cost.

## Options

`--task wiki|hn|books` or `--goal '...' --url ...` · `--backend` · `--headless` (the browser
is headed by default) · `--no-ask` · `--max-steps N` (default 15) · `--min-prob` ·
`--min-conf` · `--done-threshold` · `--risky-threshold` · `--runs-dir` · `--model`.

The exit code is 0 when the goal was reached, 1 for any other ending (aborted, blocked, max
steps, error), and 2 when the API key is missing.

## Reading a report

Every run writes `runs/<YYYY-MM-DD_HH-MM-SS>/`:

- `report.html`: a self-contained page, with no scripts and no external assets. For each step
  it shows the screenshot, the elements sent to Jev, the gate decision and reason, any human
  input, the action and its result, and every option's probability and confidence for each
  question. The exact state text sent to Jev is collapsed under each step.
- `run.json`: the same data, machine-readable.
- `usage.json` (OpenRouter only): latency, tokens and cost for each call.

A flat distribution with low confidence means Jev couldn't tell the options apart, so check the
element lines. A confident wrong answer usually means the goal wording or an element name is
misleading.

## Development

```sh
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

The tests are hermetic. They use a scripted fake `JevClient` and local HTML fixtures in
`tests/fixtures/`, served by a small local HTTP server. The loop tests call `cli.run` exactly
as `jev-agent` does, injecting only the fake Jev (and a scripted human where one is asked).
Both real clients (the TypeSafe SDK and `OpenRouterJev`) are tested with only their HTTP
transport replaced. `tests/test_live.py` makes one real call per backend, and each is skipped
unless its key is set.

## License

[MIT](LICENSE) © Shai Koffman.

The screenshots in `docs/images/` show pages from Wikipedia, whose text is available under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Jev and TypeSafe are
TypeSafe's; this project is an independent example that uses their API.
