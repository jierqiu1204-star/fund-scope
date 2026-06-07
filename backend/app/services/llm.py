from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from openai import AsyncOpenAI

from app.core.config import Settings


def _extract_json_object_text(content: str) -> str:
    stripped = content.strip()
    if not stripped:
        raise ValueError("LLM returned empty content")

    candidates = [stripped]
    if "```" in stripped:
        parts = stripped.split("```")
        for index, part in enumerate(parts):
            if index % 2 == 1:
                block = part.strip()
                if block.lower().startswith("json"):
                    block = block[4:].strip()
                candidates.append(block)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False)
    raise ValueError("LLM returned content without a valid JSON object")


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
        messages = [
            {"role": "system", "content": self._short_research_advisor_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            },
        ]
        response = await self._client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=cast(Any, messages),
            response_format=cast(Any, {
                "type": "json_schema",
                "json_schema": {
                    "name": "short_research_advisor_report",
                    "schema": response_schema,
                },
            }),
            max_tokens=1200,
            timeout=timeout_seconds,
        )
        content = response.choices[0].message.content
        if content:
            try:
                return _extract_json_object_text(content)
            except ValueError:
                pass

        fallback_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "上一次输出不是可解析 JSON。请只返回一个 JSON 对象，不要 Markdown、"
                    "不要解释文字，字段必须符合给定 schema。"
                ),
            },
        ]
        fallback_response = await self._client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=cast(Any, fallback_messages),
            max_tokens=1200,
            timeout=timeout_seconds,
        )
        fallback_content = fallback_response.choices[0].message.content
        if not fallback_content:
            raise ValueError("LLM returned an empty advisor report")
        return _extract_json_object_text(fallback_content)
