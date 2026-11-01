"""Meta Model API client via the OpenAI-compatible SDK.

Model: muse-spark-1.3 (default, overridable). Used ONLY for judgment and
language tasks: claim structuring, relevance assessment, summary writing.
Every deterministic check lives in code and cannot be skipped by the model.
"""

from __future__ import annotations

import json
import os

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"

# Prompt-cache prefix shared by both model clients (F2-B accounting).
# Pure routing hint for the provider's cache; requests are otherwise
# byte-identical and behavior is unchanged.
PROMPT_CACHE_KEY = "shipcheck-v0"


def _usage_to_dict(usage) -> dict[str, int | None] | None:
    """Flatten an OpenAI-style usage object to plain ints.

    Live shape (probe 2026-11-01): prompt_tokens, completion_tokens and
    total_tokens are ints; reasoning tokens ride along inside
    completion_tokens_details.reasoning_tokens. Defensive getattr access:
    any missing piece becomes None rather than raising — except
    cached_tokens (F2-B), which defaults to 0 when the provider omits
    prompt_tokens_details, so cache accounting never needs None checks.
    """
    if usage is None:
        return None
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(prompt_details, "cached_tokens", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_tokens": reasoning,
        "total_tokens": getattr(usage, "total_tokens", None),
        "cached_tokens": cached if isinstance(cached, int) else 0,
    }


class MetaClient:
    name = "meta-model-api"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        try:  # honor a local .env without requiring it
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        self.api_key = api_key or os.environ.get("MODEL_API_KEY", "")
        self.base_url = base_url or os.environ.get("MODEL_API_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.environ.get("MODEL_API_MODEL", DEFAULT_MODEL)
        self.timeout_s = timeout_s
        self._client = None
        # Token usage from the most recent complete_json call that got an
        # HTTP response: {"prompt_tokens", "completion_tokens",
        # "reasoning_tokens", "total_tokens"} (values int|None) plus
        # "cached_tokens" (int, 0 when the provider omits it), or None when
        # no call has completed yet / the response carried no usage object.
        # DESIGN (M4): usage is stored here — NOT returned as (data, usage)
        # — because the existing call sites (extract.structure_claim,
        # synthesize.synthesize) take a plain dict return and live in files
        # this change must not touch. The pipeline reads llm.last_usage
        # after each call and persists it into report meta. Transport
        # failures (no response) leave last_usage untouched.
        self.last_usage: dict[str, int | None] | None = None
        # Monotonic counter: incremented every time last_usage is recorded.
        # Lets the pipeline attribute usage to the exact stage that spent
        # it (a stage that made no call sees an unchanged counter).
        self.last_usage_seq: int = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):  # lazy: import openai only when actually used
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_s,
                max_retries=3,
            )
        return self._client

    def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1500,
        reasoning_effort: str | None = None,
        json_schema: dict | None = None,
    ) -> dict:
        if not self.available:
            raise RuntimeError("MODEL_API_KEY not configured; MetaClient unavailable.")
        # LIVE PROBE 2026-11-01 (1 call, value "low"): the API ACCEPTS
        # reasoning_effort, so it is sent when explicitly provided. Default
        # None keeps current behavior: the field is omitted entirely and the
        # request is byte-identical to before this change.
        # LIVE PROBE 2026-11-01 (strict json_schema + null-union types):
        # the API constrains decoding to the schema and accepts
        # "type": ["string", "null"] unions. `json_schema` is
        # {"name": str, "schema": dict}; None keeps json_object mode.
        if json_schema is None:
            response_format: dict = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": str(json_schema.get("name", "output")),
                    "strict": True,
                    "schema": json_schema["schema"],
                },
            }
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": response_format,
            "temperature": 0,
            "max_tokens": max_tokens,
            "prompt_cache_key": PROMPT_CACHE_KEY,  # F2-B: accounting only
        }
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        resp = self._get_client().chat.completions.create(**kwargs)
        # Recorded before the empty-content check: a truncated call still
        # spent tokens (probe: 48 reasoning tokens on an empty reply).
        self.last_usage = _usage_to_dict(getattr(resp, "usage", None))
        self.last_usage_seq += 1
        content = resp.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError(
                "Model returned empty content "
                f"(finish_reason={resp.choices[0].finish_reason}); "
                "likely truncated during reasoning — retry with more max_tokens."
            )
        return json.loads(content)
