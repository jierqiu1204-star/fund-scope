from __future__ import annotations

import json

from app.core.config import Settings


def test_settings_default_to_localhost_3000_when_cors_origins_are_unset() -> None:
    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://localhost:3000"]


def test_settings_parse_single_cors_origin_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3100")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://localhost:3100"]


def test_settings_parse_comma_separated_cors_origins_from_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "http://localhost:3000, http://localhost:3100",
    )

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:3100"]


def test_settings_parse_json_array_cors_origins_from_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        json.dumps(["http://localhost:3000", "http://localhost:3100"]),
    )

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:3100"]
