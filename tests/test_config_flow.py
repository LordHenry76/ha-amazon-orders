"""Tests for the config flow, the re-authentication and the options."""

from __future__ import annotations

import json

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.api.auth import LoginData
from custom_components.amazon_orders.api.exceptions import (
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonConnectionError,
)
from custom_components.amazon_orders.const import (
    CONF_ACTIVE_INTERVAL,
    CONF_IDLE_INTERVAL,
    CONF_LOGIN_DATA,
    CONF_OTP,
    CONF_SITE,
    DEFAULT_ACTIVE_INTERVAL,
    DEFAULT_IDLE_INTERVAL,
    DOMAIN,
)

from .conftest import EMAIL, LOGIN_DATA, USER_ID

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SITE = "amazon.it"
PASSWORD = "correct-password"
USER_INPUT = {CONF_SITE: "it", "email": EMAIL, "password": PASSWORD, CONF_OTP: "123456"}
OLD_LOGIN_DATA = LoginData(
    refresh_token="Atnr|old-refresh-token",
    device_serial="OLDSERIAL",
    customer_info={"user_id": USER_ID},
)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=f"{SITE}_{USER_ID}",
        data={
            CONF_SITE: SITE,
            "email": EMAIL,
            CONF_LOGIN_DATA: OLD_LOGIN_DATA.as_dict(),
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_user_flow(hass: HomeAssistant, mock_amazon) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "email": f"  {EMAIL} ", CONF_OTP: "135 792"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {
        CONF_SITE: SITE,
        "email": EMAIL,
        CONF_LOGIN_DATA: LOGIN_DATA.as_dict(),
    }
    assert result["result"].unique_id == f"{SITE}_{USER_ID}"
    # Neither the password nor the code are kept.
    stored = json.dumps(result["data"])
    assert PASSWORD not in stored
    assert "135792" not in stored

    auth, email, password, otp = mock_amazon.login.call_args.args
    assert (email, password, otp) == (EMAIL, PASSWORD, "135792")
    # The sign-in runs on a session with its own cookie jar.
    assert isinstance(auth._session.cookie_jar, aiohttp.CookieJar)

    assert result["result"].state is ConfigEntryState.LOADED
    assert mock_amazon.deregister.call_count == 0


@pytest.mark.parametrize(
    ("exception", "error", "message"),
    [
        (
            AmazonAuthError("Your password is incorrect"),
            "invalid_auth",
            "Your password is incorrect",
        ),
        (AmazonCaptchaError("captcha"), "captcha", ""),
        (AmazonConnectionError("down"), "cannot_connect", ""),
        (RuntimeError("boom"), "unknown", ""),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_amazon, exception: Exception, error: str, message: str
) -> None:
    mock_amazon.login.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert result["description_placeholders"]["amazon_message"] == message
    # The form comes back with site and email, never with password or code.
    suggested = {
        str(key): (key.description or {}).get("suggested_value")
        for key in result["data_schema"].schema
    }
    assert suggested == {
        CONF_SITE: "it",
        "email": EMAIL,
        "password": None,
        CONF_OTP: None,
    }

    mock_amazon.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(hass: HomeAssistant, mock_amazon) -> None:
    _entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # The device registered by this second sign-in is removed again.
    assert mock_amazon.deregister.call_count == 1
    assert mock_amazon.deregister.call_args.args[1:] == (LOGIN_DATA.refresh_token, SITE)


async def test_reauth(hass: HomeAssistant, mock_amazon) -> None:
    entry = _entry(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == EMAIL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": PASSWORD, CONF_OTP: "123456"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_LOGIN_DATA] == LOGIN_DATA.as_dict()
    assert entry.data["email"] == EMAIL
    assert mock_amazon.login.call_args.args[1:] == (EMAIL, PASSWORD, "123456")
    # The device of the old session is removed from the account.
    assert mock_amazon.deregister.call_count == 1
    assert mock_amazon.deregister.call_args.args[1:] == (
        OLD_LOGIN_DATA.refresh_token,
        SITE,
    )
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_error(hass: HomeAssistant, mock_amazon) -> None:
    entry = _entry(hass)
    mock_amazon.login.side_effect = AmazonAuthError("The code you entered is not valid")
    result = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": PASSWORD, CONF_OTP: "000000"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    placeholders = result["description_placeholders"]
    assert placeholders["amazon_message"] == "The code you entered is not valid"
    assert placeholders["email"] == EMAIL
    assert entry.data[CONF_LOGIN_DATA] == OLD_LOGIN_DATA.as_dict()


async def test_reauth_with_another_account(hass: HomeAssistant, mock_amazon) -> None:
    entry = _entry(hass)
    other = LoginData(
        refresh_token="Atnr|other-account",
        device_serial="OTHER",
        customer_info={"user_id": "amzn1.account.OTHER"},
    )
    mock_amazon.login.return_value = other
    result = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": PASSWORD, CONF_OTP: "123456"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_LOGIN_DATA] == OLD_LOGIN_DATA.as_dict()
    # Only the device just registered on the other account is removed.
    assert mock_amazon.deregister.call_count == 1
    assert mock_amazon.deregister.call_args.args[1:] == (other.refresh_token, SITE)


async def test_options_flow(hass: HomeAssistant, mock_amazon) -> None:
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {
        CONF_IDLE_INTERVAL: DEFAULT_IDLE_INTERVAL,
        CONF_ACTIVE_INTERVAL: DEFAULT_ACTIVE_INTERVAL,
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_IDLE_INTERVAL: 600.0, CONF_ACTIVE_INTERVAL: 90.0}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_IDLE_INTERVAL: 600, CONF_ACTIVE_INTERVAL: 90}
    assert entry.state is ConfigEntryState.LOADED
