"""Config flow for Amazon Orders: sign-in, re-authentication and options."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import async_deregister_device, create_login_session
from .api import (
    AmazonAuth,
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonError,
    LoginData,
)
from .const import (
    CONF_ACTIVE_INTERVAL,
    CONF_IDLE_INTERVAL,
    CONF_LOGIN_DATA,
    CONF_OTP,
    CONF_SITE,
    DEFAULT_ACTIVE_INTERVAL,
    DEFAULT_COUNTRY,
    DEFAULT_IDLE_INTERVAL,
    DOMAIN,
    MAX_ACTIVE_INTERVAL,
    MAX_IDLE_INTERVAL,
    MIN_ACTIVE_INTERVAL,
    MIN_IDLE_INTERVAL,
    SITES,
)

_LOGGER = logging.getLogger(__name__)

EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
OTP_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="one-time-code")
)
# The error shown when Amazon refuses the sign-in quotes Amazon's own message.
NO_AMAZON_MESSAGE = {"amazon_message": ""}


def _account_id(site: str, email: str, login_data: LoginData) -> str:
    """Return what identifies an account on a site."""
    user_id = login_data.customer_info.get("user_id")
    return (
        f"{site}_{user_id if isinstance(user_id, str) and user_id else email.lower()}"
    )


class AmazonOrdersConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Amazon Orders."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AmazonOrdersOptionsFlow:
        """Return the options flow (polling intervals)."""
        return AmazonOrdersOptionsFlow()

    async def _async_login(
        self, email: str, password: str, otp: str
    ) -> tuple[LoginData | None, dict[str, str], dict[str, str]]:
        """Sign in to Amazon. Return (login data, errors, error placeholders).

        A successful sign-in registers a device on the Amazon account.
        """
        placeholders = dict(NO_AMAZON_MESSAGE)
        session = create_login_session(self.hass)
        try:
            login_data = await AmazonAuth(session).async_login(
                email.strip(), password, "".join(otp.split())
            )
        except AmazonCaptchaError:
            return None, {"base": "captcha"}, placeholders
        except AmazonAuthError as err:
            return None, {"base": "invalid_auth"}, {"amazon_message": str(err)}
        except AmazonError:
            return None, {"base": "cannot_connect"}, placeholders
        except Exception:
            _LOGGER.exception("Unexpected error signing in to Amazon")
            return None, {"base": "unknown"}, placeholders
        finally:
            session.detach()
        return login_data, {}, placeholders

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        placeholders = dict(NO_AMAZON_MESSAGE)
        if user_input is not None:
            site = SITES[user_input[CONF_SITE]]
            email = user_input[CONF_EMAIL].strip()
            login_data, errors, placeholders = await self._async_login(
                email, user_input[CONF_PASSWORD], user_input[CONF_OTP]
            )
            if login_data is not None:
                await self.async_set_unique_id(_account_id(site, email, login_data))
                try:
                    self._abort_if_unique_id_configured()
                except AbortFlow:
                    # The sign-in has just registered one more device: undo it.
                    await async_deregister_device(
                        self.hass, site, login_data.refresh_token
                    )
                    raise
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_SITE: site,
                        CONF_EMAIL: email,
                        CONF_LOGIN_DATA: login_data.as_dict(),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_SITE, default=DEFAULT_COUNTRY): SelectSelector(
                    SelectSelectorConfig(
                        options=list(SITES),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_SITE,
                    )
                ),
                vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                vol.Required(CONF_OTP): OTP_SELECTOR,
            }
        )
        # Never send the password or the code back to the form.
        suggested = {
            key: value
            for key, value in (user_input or {}).items()
            if key in (CONF_SITE, CONF_EMAIL)
        }
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when Amazon no longer accepts the stored session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask to sign in again."""
        entry = self._get_reauth_entry()
        site = entry.data[CONF_SITE]
        email = entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}
        placeholders = dict(NO_AMAZON_MESSAGE)
        if user_input is not None:
            login_data, errors, placeholders = await self._async_login(
                email, user_input[CONF_PASSWORD], user_input[CONF_OTP]
            )
            if login_data is not None:
                await self.async_set_unique_id(_account_id(site, email, login_data))
                try:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                except AbortFlow:
                    await async_deregister_device(
                        self.hass, site, login_data.refresh_token
                    )
                    raise
                # The device of the old session is replaced by the new one.
                old = LoginData.from_dict(entry.data[CONF_LOGIN_DATA])
                await async_deregister_device(
                    self.hass, site, old.refresh_token, quiet=True
                )
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_LOGIN_DATA: login_data.as_dict()}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                    vol.Required(CONF_OTP): OTP_SELECTOR,
                }
            ),
            errors=errors,
            description_placeholders={**placeholders, "email": email},
        )


def _seconds_selector(minimum: int, maximum: int, step: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


class AmazonOrdersOptionsFlow(OptionsFlowWithReload):
    """Let the user tune the polling intervals; the entry reloads on save."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_IDLE_INTERVAL: int(user_input[CONF_IDLE_INTERVAL]),
                    CONF_ACTIVE_INTERVAL: int(user_input[CONF_ACTIVE_INTERVAL]),
                }
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_IDLE_INTERVAL,
                    default=options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL),
                ): _seconds_selector(MIN_IDLE_INTERVAL, MAX_IDLE_INTERVAL, 60),
                vol.Required(
                    CONF_ACTIVE_INTERVAL,
                    default=options.get(CONF_ACTIVE_INTERVAL, DEFAULT_ACTIVE_INTERVAL),
                ): _seconds_selector(MIN_ACTIVE_INTERVAL, MAX_ACTIVE_INTERVAL, 30),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
