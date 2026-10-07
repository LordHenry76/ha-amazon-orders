"""Amazon sign-in: login, device registration and session cookie renewal.

Amazon has no public API for customers. This module signs in the way Amazon's
own mobile apps do: it registers a virtual device on the account once, and then
uses the refresh token of that device to obtain website cookies whenever needed.

The sign-in flow and its parameters are adapted from aioamazondevices
(https://github.com/chemelli74/aioamazondevices), Copyright 2024 Simone Chemelli
and contributors, licensed under the Apache License 2.0. See the NOTICE file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup, Tag
from yarl import URL

from .exceptions import AmazonAuthError, AmazonCaptchaError, AmazonConnectionError

# Sign-in is global: every account signs in on amazon.com, whatever its country.
SIGNIN_URL = "https://www.amazon.com"
API_URL = "https://api.amazon.com"

# Identity of the virtual device. The device type is the one of Amazon's Alexa
# app for iOS; APP_NAME is the name the device gets in the user's Amazon account.
APP_NAME = "Home Assistant Amazon Orders"
DEVICE_TYPE = "A2IVLV5VM2W81"
APP_VERSION = "2.2.663733.0"
APP_BUNDLE_ID = "com.amazon.echo"
APP_ID = "MAPiOSLib/6.0/ToHideRetailLink"
DEVICE_SOFTWARE_VERSION = "35602678"
CLIENT_OS = "18.5"
SDK_VERSION = "6.12.4"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

_OTP_INPUT_ID = "auth-mfa-otpcode"
_ERROR_BOX_IDS = ("auth-error-message-box", "auth-warning-message-box")
_CAPTCHA_MARKERS = ("auth-captcha-image", "cvf-aamation-challenge-form")
_AUTH_CODE_PARAM = "openid.oa2.authorization_code"


@dataclass(frozen=True, slots=True)
class LoginData:
    """What is kept after sign-in. The password is never part of it."""

    refresh_token: str
    device_serial: str
    device_info: dict[str, Any] = field(default_factory=dict)
    customer_info: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict, to store in a config entry."""
        return {
            "refresh_token": self.refresh_token,
            "device_serial": self.device_serial,
            "device_info": dict(self.device_info),
            "customer_info": dict(self.customer_info),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoginData:
        """Rebuild the object from what as_dict returned."""
        return cls(
            refresh_token=data["refresh_token"],
            device_serial=data["device_serial"],
            device_info=dict(data.get("device_info") or {}),
            customer_info=dict(data.get("customer_info") or {}),
        )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _client_id(serial: str) -> str:
    return f"{serial}#{DEVICE_TYPE}".encode().hex()


def _auth_code(url: str) -> str | None:
    """Return the authorization code Amazon appends to the landing URL."""
    values = parse_qs(urlsplit(url).query).get(_AUTH_CODE_PARAM)
    return values[0] if values else None


def _signin_form(page: BeautifulSoup, page_url: str) -> tuple[str, str, dict[str, str]]:
    """Return method, target URL and hidden fields of the sign-in form."""
    form = page.find("form", {"name": "signIn"}) or page.find("form")
    if not isinstance(form, Tag):
        raise _login_failure(page)

    method = form.get("method")
    action = form.get("action")
    if not isinstance(method, str) or not isinstance(action, str):
        raise AmazonAuthError("The sign-in form has an unexpected format")

    fields: dict[str, str] = {}
    for element in form.find_all("input"):
        if not isinstance(element, Tag) or element.get("type") != "hidden":
            continue
        name = element.get("name")
        value = element.get("value", "")
        if isinstance(name, str) and isinstance(value, str):
            fields[name] = value
    return method.upper(), urljoin(page_url, action), fields


def _login_failure(page: BeautifulSoup) -> AmazonAuthError | AmazonCaptchaError:
    """Explain why a sign-in page is not the one that was expected."""
    html = str(page)
    if any(marker in html for marker in _CAPTCHA_MARKERS):
        return AmazonCaptchaError("Amazon asked for a CAPTCHA during sign-in")
    for box_id in _ERROR_BOX_IDS:
        box = page.find(id=box_id)
        if isinstance(box, Tag):
            message = " ".join(box.get_text(" ").split())
            if message:
                return AmazonAuthError(message)
    return AmazonAuthError("Amazon showed an unexpected page during sign-in")


class AmazonAuth:
    """Signs in to Amazon and renews the website session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        signin_url: str = SIGNIN_URL,
        api_url: str = API_URL,
    ) -> None:
        """Initialize.

        ``async_login`` needs a session with a real cookie jar, used for nothing
        else: Amazon's sign-in pages rely on cookies between one step and the next.
        The other methods send no cookies and work with any session.
        """
        self._session = session
        self._signin_url = signin_url.rstrip("/")
        self._api_url = api_url.rstrip("/")

    async def async_login(self, email: str, password: str, otp: str) -> LoginData:
        """Sign in and register a device on the account.

        ``otp`` is the current code of the authenticator app. Raise
        AmazonAuthError if Amazon refuses the credentials (its own message is
        the text of the error), AmazonCaptchaError if it asks for a CAPTCHA.
        """
        if isinstance(self._session.cookie_jar, aiohttp.DummyCookieJar):
            raise TypeError("async_login needs a session with a real cookie jar")

        serial = uuid.uuid4().hex.upper()
        client_id = _client_id(serial)
        code_verifier = _b64url(secrets.token_bytes(32))
        code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
        frc = self._set_app_cookies()

        page, url = await self._signin_request(
            "GET",
            f"{self._signin_url}/ap/signin",
            params={
                "openid.return_to": f"{self._signin_url}/ap/maplanding",
                "openid.oa2.code_challenge_method": "S256",
                "openid.assoc_handle": "amzn_dp_project_dee_ios",
                "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
                "pageId": "amzn_dp_project_dee_ios",
                "accountStatusPolicy": "P1",
                "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
                "openid.mode": "checkid_setup",
                "openid.ns.oa2": "http://www.amazon.com/ap/ext/oauth/2",
                "openid.oa2.client_id": f"device:{client_id}",
                "language": "en-US",
                "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
                "openid.oa2.code_challenge": code_challenge,
                "openid.oa2.scope": "device_auth_access",
                "openid.ns": "http://specs.openid.net/auth/2.0",
                "openid.pape.max_auth_age": "0",
                "openid.oa2.response_type": "code",
            },
        )

        method, target, fields = _signin_form(page, url)
        fields["email"] = email
        fields["password"] = password
        page, url = await self._signin_request(method, target, data=fields)

        code = _auth_code(url)
        if code is None:
            if page.find("input", id=_OTP_INPUT_ID) is None:
                raise _login_failure(page)
            method, target, fields = _signin_form(page, url)
            fields["otpCode"] = otp
            fields["mfaSubmit"] = "Submit"
            fields["rememberDevice"] = "false"
            page, url = await self._signin_request(method, target, data=fields)
            code = _auth_code(url)
            if code is None:
                raise _login_failure(page)

        return await self._register_device(serial, client_id, code, code_verifier, frc)

    def _set_app_cookies(self) -> str:
        """Set the cookies Amazon's apps send while signing in. Return ``frc``."""
        frc = base64.b64encode(secrets.token_bytes(313)).decode().rstrip("=")
        map_md = {
            "device_user_dictionary": [],
            "device_registration_data": {"software_version": DEVICE_SOFTWARE_VERSION},
            "app_identifier": {"app_version": APP_VERSION, "bundle_id": APP_BUNDLE_ID},
        }
        self._session.cookie_jar.update_cookies(
            {
                "amzn-app-id": APP_ID,
                "frc": frc,
                "map-md": base64.b64encode(json.dumps(map_md).encode())
                .decode()
                .rstrip("="),
            },
            URL(self._signin_url),
        )
        return frc

    async def _signin_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[BeautifulSoup, str]:
        """Request a sign-in page. Return the parsed page and its final URL."""
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                data=data,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US"},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                final_url = str(response.url)
                # The landing page that carries the authorization code does not
                # exist: Amazon answers 404 there, and that is expected.
                landed = _auth_code(final_url) is not None
                if response.status != 200 and not landed:
                    raise AmazonConnectionError(
                        f"Amazon answered HTTP {response.status} during sign-in"
                    )
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AmazonConnectionError(f"Cannot reach Amazon: {err!r}") from err
        return BeautifulSoup(text, "html.parser"), final_url

    async def _register_device(
        self, serial: str, client_id: str, code: str, code_verifier: str, frc: str
    ) -> LoginData:
        body = {
            "requested_extensions": ["device_info", "customer_info"],
            "cookies": {"website_cookies": [], "domain": ".amazon.com"},
            "registration_data": {
                "domain": "Device",
                "app_version": APP_VERSION,
                "device_type": DEVICE_TYPE,
                "device_name": f"%FIRST_NAME%'s%DUPE_STRATEGY_1ST%{APP_NAME}",
                "os_version": CLIENT_OS,
                "device_serial": serial,
                "device_model": "iPhone",
                "app_name": APP_NAME,
                "software_version": DEVICE_SOFTWARE_VERSION,
            },
            "auth_data": {
                "use_global_authentication": "true",
                "client_id": client_id,
                "authorization_code": code,
                "code_verifier": code_verifier,
                "code_algorithm": "SHA-256",
                "client_domain": "DeviceLegacy",
            },
            "user_context_map": {"frc": frc},
            "requested_token_type": [
                "bearer",
                "mac_dms",
                "website_cookies",
                "store_authentication_cookie",
            ],
        }
        status, payload = await self._api_request("/auth/register", json_body=body)
        if status != 200:
            raise AmazonAuthError(
                f"Amazon refused to register the device: {_api_error(payload, status)}"
            )
        try:
            success = payload["response"]["success"]
            extensions = success.get("extensions") or {}
            return LoginData(
                refresh_token=success["tokens"]["bearer"]["refresh_token"],
                device_serial=serial,
                device_info=dict(extensions.get("device_info") or {}),
                customer_info=dict(extensions.get("customer_info") or {}),
            )
        except (KeyError, TypeError, AttributeError) as err:
            raise AmazonAuthError(
                "The device registration answer has an unexpected format"
            ) from err

    async def async_get_cookies(self, refresh_token: str, site: str) -> dict[str, str]:
        """Exchange the refresh token for website cookies of a site.

        ``site`` is the Amazon domain of the user, e.g. ``amazon.it``. Raise
        AmazonAuthError if the refresh token is no longer accepted.
        """
        payload = await self._token_request(
            refresh_token, "auth_cookies", domain=f"www.{site}"
        )
        try:
            by_domain = payload["response"]["tokens"]["cookies"]
            cookies = {
                cookie["Name"]: str(cookie["Value"]).replace('"', "")
                for domain_cookies in by_domain.values()
                for cookie in domain_cookies
            }
        except (KeyError, TypeError, AttributeError) as err:
            raise AmazonAuthError(
                "The cookie exchange answer has an unexpected format"
            ) from err
        if not cookies:
            raise AmazonAuthError("Amazon returned no cookies for this site")
        return cookies

    async def async_get_access_token(self, refresh_token: str, site: str) -> str:
        """Exchange the refresh token for a short-lived access token."""
        payload = await self._token_request(
            refresh_token, "access_token", domain=f"www.{site}"
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise AmazonAuthError("Amazon returned no access token")
        return token

    async def async_deregister(self, refresh_token: str, site: str) -> None:
        """Remove the registered device from the user's Amazon account.

        Not verified against Amazon yet.
        """
        access_token = await self.async_get_access_token(refresh_token, site)
        status, payload = await self._api_request(
            "/auth/deregister",
            json_body={"deregister_all_existing_accounts": False},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if status != 200:
            raise AmazonAuthError(
                f"Amazon refused to remove the device: {_api_error(payload, status)}"
            )

    async def _token_request(
        self, refresh_token: str, token_type: str, *, domain: str
    ) -> dict[str, Any]:
        status, payload = await self._api_request(
            "/auth/token",
            form={
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "di.sdk.version": SDK_VERSION,
                "source_token": refresh_token,
                "package_name": APP_BUNDLE_ID,
                "di.hw.version": "iPhone",
                "platform": "iOS",
                "requested_token_type": token_type,
                "source_token_type": "refresh_token",
                "di.os.name": "iOS",
                "di.os.version": CLIENT_OS,
                "current_version": SDK_VERSION,
                "previous_version": SDK_VERSION,
                "domain": domain,
            },
        )
        if status in (400, 401, 403):
            raise AmazonAuthError(
                f"Amazon refused the stored session: {_api_error(payload, status)}"
            )
        if status != 200:
            raise AmazonConnectionError(
                f"Amazon answered HTTP {status} to the token request"
            )
        return payload

    async def _api_request(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """POST to Amazon's authentication API. Return status and JSON body."""
        try:
            async with self._session.post(
                f"{self._api_url}{path}",
                json=json_body,
                data=form,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                status = response.status
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AmazonConnectionError(f"Cannot reach Amazon: {err!r}") from err
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}


def _api_error(payload: dict[str, Any], status: int) -> str:
    """Return Amazon's own description of an API error, without identifiers."""
    response = payload.get("response")
    error = response.get("error") if isinstance(response, dict) else None
    if isinstance(error, dict) and (error.get("code") or error.get("message")):
        return f"{error.get('code')}: {error.get('message')} (HTTP {status})"
    for key in ("error_description", "error"):
        if isinstance(payload.get(key), str):
            return f"{payload[key]} (HTTP {status})"
    return f"HTTP {status}"
