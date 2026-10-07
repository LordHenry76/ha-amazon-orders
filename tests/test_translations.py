"""Tests keeping the translations in step with the code."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from custom_components.amazon_orders.const import (
    CONF_ACTIVE_INTERVAL,
    CONF_IDLE_INTERVAL,
    CONF_OTP,
    CONF_SITE,
    SITES,
)

TRANSLATIONS = (
    Path(__file__).parent.parent
    / "custom_components"
    / "amazon_orders"
    / "translations"
)
LANGUAGES = sorted(path.stem for path in TRANSLATIONS.glob("*.json"))


def _load(language: str) -> dict[str, Any]:
    return json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))


def _paths(value: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        result: dict[str, str] = {}
        for key, child in value.items():
            result.update(_paths(child, f"{prefix}/{key}"))
        return result
    return {prefix: value}


def test_languages() -> None:
    assert LANGUAGES == ["en", "it"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_same_strings_and_placeholders_as_english(language: str) -> None:
    english = _paths(_load("en"))
    other = _paths(_load(language))

    assert set(other) == set(english)
    for path, text in english.items():
        assert set(re.findall(r"{(\w+)}", other[path])) == set(
            re.findall(r"{(\w+)}", text)
        ), path


def test_strings_used_by_the_flows() -> None:
    config = _load("en")["config"]

    assert set(config["step"]["user"]["data"]) == {
        CONF_SITE,
        "email",
        "password",
        CONF_OTP,
    }
    assert set(config["step"]["reauth_confirm"]["data"]) == {"password", CONF_OTP}
    assert set(config["error"]) == {
        "invalid_auth",
        "captcha",
        "cannot_connect",
        "unknown",
    }
    assert set(config["abort"]) == {
        "already_configured",
        "reauth_successful",
        "wrong_account",
    }
    assert set(_load("en")["options"]["step"]["init"]["data"]) == {
        CONF_IDLE_INTERVAL,
        CONF_ACTIVE_INTERVAL,
    }
    assert set(_load("en")["selector"][CONF_SITE]["options"]) == set(SITES)


def test_placeholders_are_the_ones_the_flows_provide() -> None:
    config = _paths(_load("en")["config"])
    used = {path: set(re.findall(r"{(\w+)}", text)) for path, text in config.items()}

    assert {path: names for path, names in used.items() if names} == {
        "/step/reauth_confirm/description": {"email"},
        "/error/invalid_auth": {"amazon_message"},
    }
