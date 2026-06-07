from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import llm as llm_module
from app.services.news_summarizer import parse_summary_output


@pytest.mark.asyncio
async def test_summarize_news_calls_configured_openai_endpoint(monkeypatch, settings) -> None:
    created_clients: list[Any] = []

    class FakeCompletions:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> Any:
            self.requests.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="manager_change|基金经理发生变更")
                    )
                ]
            )

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            self.base_url = base_url
            self.api_key = api_key
            self.completions = FakeCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            created_clients.append(self)

    monkeypatch.setattr(llm_module, "AsyncOpenAI", FakeAsyncOpenAI)

    client = llm_module.LLMClient(settings)
    result = await client.summarize_news("Fund manager changed", "The manager changed today.")

    fake_client = created_clients[0]
    assert fake_client.base_url == "https://example.com/v1"
    assert fake_client.api_key == "test-key"
    assert result == "manager_change|基金经理发生变更"
    request = fake_client.completions.requests[0]
    assert request["model"] == "test-model"
    assert request["temperature"] == 0
    assert "event_type|summary" in request["messages"][0]["content"]
    assert "Fund manager changed" in request["messages"][1]["content"]
    assert "The manager changed today." in request["messages"][1]["content"]


@pytest.mark.asyncio
async def test_generate_short_research_report_uses_structured_output(monkeypatch, settings) -> None:
    created_clients: list[Any] = []

    class FakeCompletions:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> Any:
            self.requests.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"action_label":"谨慎"}')
                    )
                ]
            )

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            self.completions = FakeCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            created_clients.append(self)

    monkeypatch.setattr(llm_module, "AsyncOpenAI", FakeAsyncOpenAI)

    client = llm_module.LLMClient(settings)
    result = await client.generate_short_research_report(
        {"code": "270042"},
        response_schema={"type": "object", "properties": {"action_label": {"type": "string"}}},
        timeout_seconds=12,
    )

    request = created_clients[0].completions.requests[0]
    assert json.loads(result) == {"action_label": "谨慎"}
    assert request["model"] == "test-model"
    assert request["temperature"] == 0
    assert request["timeout"] == 12
    assert request["response_format"]["type"] == "json_schema"
    assert "只做解释" not in request["messages"][1]["content"]


@pytest.mark.asyncio
async def test_generate_short_research_report_falls_back_to_extract_json(
    monkeypatch, settings
) -> None:
    created_clients: list[Any] = []

    class FakeCompletions:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> Any:
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                content = ""
            else:
                content = '说明如下：```json\n{"action_label":"谨慎","plain_summary":"只做观察"}\n```'
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            self.completions = FakeCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            created_clients.append(self)

    monkeypatch.setattr(llm_module, "AsyncOpenAI", FakeAsyncOpenAI)

    client = llm_module.LLMClient(settings)
    result = await client.generate_short_research_report(
        {"code": "270042"},
        response_schema={"type": "object", "properties": {"action_label": {"type": "string"}}},
        timeout_seconds=12,
    )

    requests = created_clients[0].completions.requests
    assert len(requests) == 2
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert "response_format" not in requests[1]
    assert "不要 Markdown" in requests[1]["messages"][-1]["content"]
    assert json.loads(result) == {"action_label": "谨慎", "plain_summary": "只做观察"}


def test_parse_summary_output_restricts_event_type_and_summary_length() -> None:
    parsed = parse_summary_output(f"unsupported|{'x' * 100}")

    assert parsed.event_type == "other"
    assert parsed.summary == "x" * 80
