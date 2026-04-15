"""OpenAI-compatible LLM client.

Points at whatever ``LLM_BASE_URL`` is configured. Works with local vLLM /
llama.cpp / Ollama ``/v1`` endpoints, or any OpenAI-compatible service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings


@dataclass
class LLMResponse:
    content: str
    model: str


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
    )
    def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Ask the LLM for JSON output.

        Uses ``response_format={"type": "json_object"}`` which is supported by
        most OpenAI-compatible servers. Falls back to best-effort parsing if
        the server ignores the hint.
        """
        model = model or self.model
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception:
            # Some servers reject response_format — retry without it.
            resp = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        text = (resp.choices[0].message.content or "").strip()
        return _parse_json_loose(text)


def _parse_json_loose(text: str) -> dict[str, Any]:
    """Parse JSON tolerantly: strip markdown fences, find the first object."""
    t = text.strip()
    if t.startswith("```"):
        # Strip code fences.
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # Fall back: find the first balanced {...} block.
        start = t.find("{")
        if start < 0:
            raise
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(t[start : i + 1])
        raise
