from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.core.config import Settings


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key or "test-key",
        )
        self.model_name = settings.model_name
        prompt_dir = Path(__file__).resolve().parent / "prompts"
        self._prompt = (
            Path(__file__).resolve().parent / "prompts" / "news_summary.txt"
        ).read_text(encoding="utf-8")
        self._recommendation_prompt = (prompt_dir / "recommendation_explanation.txt").read_text(
            encoding="utf-8"
        )
        self._short_research_advisor_prompt = (prompt_dir / "short_research_advisor.txt").read_text(
            encoding="utf-8"
        )

    async def summarize_news(self, title: str, raw_content: str) -> str:
        response = await self._client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": self._prompt},
                {
                    "role": "user",
                    "content": f"Title:\n{title}\n\nContent:\n{raw_content}",
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM returned an empty summary")
        return content.strip()

    async def explain_recommendation(self, payload: dict[str, object]) -> str:
        response = await self._client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": self._recommendation_prompt},
                {"role": "user", "content": str(payload)},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM returned an empty explanation")
        return content.strip()[:280]

    async def generate_short_research_report(
        self,
        payload: dict[str, Any],
        *,
        response_schema: dict[str, Any],
        timeout_seconds: float,
    ) -> str:
        response = await self._client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": self._short_research_advisor_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "short_research_advisor_report",
                    "schema": response_schema,
                },
            },
            max_tokens=1200,
            timeout=timeout_seconds,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM returned an empty advisor report")
        return content.strip()
