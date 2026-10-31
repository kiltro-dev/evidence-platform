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

    def complete_json(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        if not self.available:
            raise RuntimeError("MODEL_API_KEY not configured; MetaClient unavailable.")
        resp = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError(
                "Model returned empty content "
                f"(finish_reason={resp.choices[0].finish_reason}); "
                "likely truncated during reasoning — retry with more max_tokens."
            )
        return json.loads(content)
