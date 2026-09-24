"""``JevClient`` backed by OpenRouter's Decisions API (the same Jev, served as
``typesafe/jev-1.13``).

``POST https://openrouter.ai/api/alpha/decisions`` with ``Authorization: Bearer <key>``. The request
is TypeSafe's shape (``model`` / ``state`` / ``questions``) and so is the response (``answers``
keyed by question id, plus ``usage`` with ``input_tokens`` / ``output_tokens`` / ``cost``). One
difference: the Decisions schema only *requires* ``type`` and ``choice`` on a choice answer, so a
missing ``confidence`` / ``probabilities`` is read as zero certainty, never as a pass.

A thin httpx2 adapter rather than the ``openrouter`` SDK: that SDK covers the whole OpenRouter API
(~1 MB) and pins ``pydantic<2.13``, which conflicts with ``typesafe-sdk``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast

import httpx2
from pydantic import BaseModel
from typesafe_sdk import Question

from jev_web_agent.decide import ChoiceResult, JevAnswers

OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
OPENROUTER_MODEL = "typesafe/jev-1.13"
API_KEY_ENV = "OPENROUTER_API_KEY"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 524, 529})


class OpenRouterError(RuntimeError):
    """A failed or malformed Decisions call. Messages never include request headers."""


@dataclass(frozen=True)
class CallUsage:
    """One Decisions call: what it cost and how long it took."""

    id: str | None
    model: str | None
    latency_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None


def _wire(question: Question) -> dict[str, Any]:
    if isinstance(question, BaseModel):
        return question.model_dump(mode="json")
    return dict(question)


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OpenRouterError(f"malformed Decisions response: {where} is not a number")
    return float(value)


def _choice(name: str, raw: Mapping[str, object]) -> ChoiceResult:
    choice = raw.get("choice")
    if not isinstance(choice, str):
        raise OpenRouterError(f"malformed Decisions response: answers.{name}.choice")
    probabilities_raw: object = raw.get("probabilities") or {}
    if not isinstance(probabilities_raw, dict):
        raise OpenRouterError(f"malformed Decisions response: answers.{name}.probabilities")
    probabilities = {
        str(k): _number(v, f"answers.{name}.probabilities.{k}")
        for k, v in cast(dict[object, object], probabilities_raw).items()
    }
    confidence = raw.get("confidence")
    return ChoiceResult(
        choice=choice,
        probabilities=probabilities,
        confidence=0.0 if confidence is None else _number(confidence, f"answers.{name}.confidence"),
    )


def parse_answers(body: object) -> JevAnswers:
    if not isinstance(body, dict):
        raise OpenRouterError("malformed Decisions response: body is not an object")
    answers = cast(dict[str, object], body).get("answers")
    if not isinstance(answers, dict):
        raise OpenRouterError("malformed Decisions response: no answers")
    choices: dict[str, ChoiceResult] = {}
    nouls: dict[str, float] = {}
    for name, raw in cast(dict[str, object], answers).items():
        if not isinstance(raw, dict):
            raise OpenRouterError(f"malformed Decisions response: answers.{name}")
        answer = cast(dict[str, object], raw)
        kind = answer.get("type")
        if kind == "choice":
            choices[name] = _choice(name, answer)
        elif kind == "noul":
            if "noul" not in answer:
                raise OpenRouterError(f"malformed Decisions response: answers.{name}.noul")
            nouls[name] = _number(answer["noul"], f"answers.{name}.noul")
        # other answer kinds (score, future types) are ignored; we never ask for them
    return JevAnswers(choices=choices, nouls=nouls)


def _usage(body: object, latency: float) -> CallUsage:
    data = cast(dict[str, object], body) if isinstance(body, dict) else {}
    usage_raw = data.get("usage")
    usage = cast(dict[str, object], usage_raw) if isinstance(usage_raw, dict) else {}

    def as_int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    cost = usage.get("cost")
    ident, model = data.get("id"), data.get("model")
    return CallUsage(
        id=ident if isinstance(ident, str) else None,
        model=model if isinstance(model, str) else None,
        latency_seconds=latency,
        input_tokens=as_int(usage.get("input_tokens")),
        output_tokens=as_int(usage.get("output_tokens")),
        cost=float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
    )


def _error_message(response: httpx2.Response) -> str:
    try:
        body = response.json()
        message = body["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:200]
    return f"OpenRouter Decisions HTTP {response.status_code}: {message}"


class OpenRouterJev:
    """Ask Jev through OpenRouter. ``usage`` collects one ``CallUsage`` per call."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = OPENROUTER_MODEL,
        url: str = OPENROUTER_URL,
        transport: httpx2.BaseTransport | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise OpenRouterError(f"{API_KEY_ENV} is empty")
        self.model = model
        self.url = url
        self.usage: list[CallUsage] = []
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._http = httpx2.Client(
            transport=transport,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )

    def __repr__(self) -> str:  # never show the key
        return f"OpenRouterJev(model={self.model!r}, url={self.url!r})"

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def ask(self, state: str, questions: Mapping[str, Question]) -> JevAnswers:
        payload = {
            "model": self.model,
            "state": state,
            "questions": {name: _wire(q) for name, q in questions.items()},
        }
        started = time.monotonic()
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.post(self.url, content=json.dumps(payload))
            except httpx2.TransportError as exc:
                if attempt == self._max_retries:
                    raise OpenRouterError(
                        f"OpenRouter Decisions connection failed: {type(exc).__name__}"
                    ) from None
                time.sleep(self._backoff * 2**attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                time.sleep(self._backoff * 2**attempt)
                continue
            if response.status_code != 200:
                raise OpenRouterError(_error_message(response))
            try:
                body: object = response.json()
            except ValueError:
                raise OpenRouterError("malformed Decisions response: not JSON") from None
            answers = parse_answers(body)
            self.usage.append(_usage(body, time.monotonic() - started))
            return answers
        raise AssertionError("unreachable")  # pragma: no cover

    def write_usage(self, path: Path) -> Path:
        """Per-call latency / tokens / cost (call n = step n) plus totals, as JSON."""

        def total(field: str) -> float | int | None:
            values = [getattr(c, field) for c in self.usage]
            present = [v for v in values if v is not None]
            return sum(present) if present else None

        data = {
            "backend": "openrouter",
            "url": self.url,
            "model_requested": self.model,
            "calls": [asdict(c) for c in self.usage],
            "total": {
                "calls": len(self.usage),
                "latency_seconds": total("latency_seconds"),
                "input_tokens": total("input_tokens"),
                "output_tokens": total("output_tokens"),
                "cost": total("cost"),
            },
        }
        path.write_text(json.dumps(data, indent=2))
        return path
