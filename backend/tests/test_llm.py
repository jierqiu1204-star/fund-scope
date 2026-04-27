from __future__ import annotations

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


def test_parse_summary_output_restricts_event_type_and_summary_length() -> None:
    parsed = parse_summary_output(f"unsupported|{'x' * 100}")

    assert parsed.event_type == "other"
    assert parsed.summary == "x" * 80
