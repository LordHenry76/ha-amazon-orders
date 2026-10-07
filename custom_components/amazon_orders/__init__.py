"""The Amazon Orders integration (unofficial delivery tracking)."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .api import AmazonAuth, AmazonError, AmazonOrdersClient, LoginData
from .const import CONF_LOGIN_DATA, CONF_SITE, DOMAIN, STORAGE_VERSION
from .coordinator import AmazonOrdersConfigEntry, AmazonOrdersCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


def create_login_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Create the HTTP session for one sign-in.

    Amazon's sign-in pages need cookies between one step and the next, so this
    session has its own cookie jar and must be used for nothing else. The caller
    must call ``session.detach()`` when done (never ``close()``).
    """
    return async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar()
    )


def create_session(
    hass: HomeAssistant, *, auto_cleanup: bool = True
) -> aiohttp.ClientSession:
    """Create the HTTP session for everything but the sign-in.

    It stores no cookies: the client sends the session cookies explicitly, and
    only to the Amazon site of the account. When created during entry setup,
    Home Assistant detaches it on unload. With auto_cleanup=False the caller
    must call ``session.detach()`` (never ``close()``).
    """
    return async_create_clientsession(
        hass, auto_cleanup=auto_cleanup, cookie_jar=aiohttp.DummyCookieJar()
    )


async def async_deregister_device(
    hass: HomeAssistant, site: str, refresh_token: str, *, quiet: bool = False
) -> bool:
    """Remove the virtual device from the user's Amazon account, best effort.

    Return True if Amazon confirmed. ``quiet`` is for tokens that Amazon has
    probably refused already, where a failure is expected.
    """
    session = create_session(hass, auto_cleanup=False)
    try:
        await AmazonAuth(session).async_deregister(refresh_token, site)
    except AmazonError as err:
        _LOGGER.log(
            logging.DEBUG if quiet else logging.WARNING,
            "Could not remove the device registered on the Amazon account (%s). "
            "You can remove it from your Amazon account, among your devices",
            err,
        )
        return False
    finally:
        session.detach()
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AmazonOrdersConfigEntry
) -> bool:
    """Set up Amazon Orders from a config entry."""
    session = create_session(hass)
    auth = AmazonAuth(session)
    login_data = LoginData.from_dict(entry.data[CONF_LOGIN_DATA])
    client = AmazonOrdersClient(
        session, auth, login_data.refresh_token, entry.data[CONF_SITE]
    )

    coordinator = AmazonOrdersCoordinator(hass, entry, auth, client)
    # Reads the orders once: a refused session starts the re-authentication,
    # an unreachable Amazon makes Home Assistant try again later.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AmazonOrdersConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: AmazonOrdersConfigEntry
) -> None:
    """Forget the packages and remove the device from the Amazon account."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    login_data = LoginData.from_dict(entry.data[CONF_LOGIN_DATA])
    await async_deregister_device(hass, entry.data[CONF_SITE], login_data.refresh_token)
