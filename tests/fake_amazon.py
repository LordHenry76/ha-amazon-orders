"""A small fake of the Amazon endpoints used by the integration, for the tests."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from aiohttp import web

FIXTURES = Path(__file__).parent / "fixtures"

EMAIL = "user@example.com"
PASSWORD = "correct-password"
OTP = "123456"
AUTH_CODE = "TESTAUTHCODE"
REFRESH_TOKEN = "Atnr|test-refresh-token"
ACCESS_TOKEN = "Atna|test-access-token"

_LOGIN_FORM = """
<form name="signIn" method="post" action="/ap/signin">
  <input type="hidden" name="appActionToken" value="token-1">
  <input type="hidden" name="workflowState" value="state-1">
  <input type="email" id="ap_email" name="email">
  <input type="password" id="ap_password" name="password">
  {extra}
</form>
"""
_OTP_FORM = """
<form id="auth-mfa-form" method="post" action="/ap/signin">
  <input type="hidden" name="appActionToken" value="token-2">
  <input type="tel" id="auth-mfa-otpcode" name="otpCode">
  {extra}
</form>
"""
_ERROR_BOX = (
    '<div id="auth-error-message-box"><h4>There was a problem</h4>'
    "<span>{message}</span></div>"
)


def _page(body: str, status: int = 200) -> web.Response:
    return web.Response(
        text=f"<html><body>{body}</body></html>",
        content_type="text/html",
        status=status,
    )


class FakeAmazon:
    """Fake Amazon: sign-in pages, authentication API and order pages.

    The attributes steer its answers and record what the client sent.
    """

    def __init__(self) -> None:
        self.captcha_on_login = False
        self.require_otp = True
        self.register_status = 200
        self.token_status = 200
        self.deregister_status = 200
        self.orders_status = 200
        self.orders_page = "orders.html"
        self.orders_redirect: str | None = None
        self.always_ask_signin = False

        self.code_challenge: str | None = None
        self.frc_cookie: str | None = None
        self.register_body: dict[str, Any] | None = None
        self.token_requests: list[dict[str, str]] = []
        self.deregister_auth: str | None = None
        self.issued_sessions = 0
        self.page_requests: list[web.Request] = []

        self.app = web.Application()
        self.app.add_routes(
            [
                web.get("/ap/signin", self._signin_page),
                web.post("/ap/signin", self._signin_post),
                web.get("/ap/mfa", self._mfa_page),
                web.get("/ap/maplanding", self._landing),
                web.post("/auth/register", self._register),
                web.post("/auth/token", self._token),
                web.post("/auth/deregister", self._deregister),
                web.get("/your-orders/orders", self._orders),
                web.get("/progress-tracker/package", self._tracking),
                web.get("/elsewhere", self._elsewhere),
            ]
        )

    @property
    def session_cookie(self) -> str:
        """Value of the session cookie currently accepted."""
        return f"session-{self.issued_sessions}"

    # --- sign-in pages -------------------------------------------------

    async def _signin_page(self, request: web.Request) -> web.Response:
        self.code_challenge = request.query.get("openid.oa2.code_challenge")
        self.frc_cookie = request.cookies.get("frc")
        if request.cookies.get("amzn-app-id") is None:
            return _page("missing app cookies", status=400)
        return _page(_LOGIN_FORM.format(extra=""))

    async def _signin_post(self, request: web.Request) -> web.StreamResponse:
        form = await request.post()
        if "otpCode" in form:
            if form.get("appActionToken") != "token-2" or form["otpCode"] != OTP:
                error = _ERROR_BOX.format(message="The code you entered is not valid.")
                return _page(_OTP_FORM.format(extra=error))
            raise web.HTTPFound(
                f"/ap/maplanding?openid.oa2.authorization_code={AUTH_CODE}"
            )

        if self.captcha_on_login:
            return _page(_LOGIN_FORM.format(extra='<img id="auth-captcha-image">'))
        if (
            form.get("appActionToken") != "token-1"
            or form.get("email") != EMAIL
            or form.get("password") != PASSWORD
        ):
            error = _ERROR_BOX.format(message="Your password is incorrect")
            return _page(_LOGIN_FORM.format(extra=error))
        if not self.require_otp:
            raise web.HTTPFound(
                f"/ap/maplanding?openid.oa2.authorization_code={AUTH_CODE}"
            )
        raise web.HTTPFound("/ap/mfa")

    async def _mfa_page(self, request: web.Request) -> web.Response:
        return _page(_OTP_FORM.format(extra=""))

    async def _landing(self, request: web.Request) -> web.Response:
        return _page("Page not found", status=404)

    # --- authentication API -------------------------------------------

    async def _register(self, request: web.Request) -> web.Response:
        self.register_body = body = await request.json()
        if self.register_status != 200:
            return web.json_response(
                {
                    "response": {
                        "error": {
                            "code": "InvalidValue",
                            "index": "secret-index",
                            "message": "One or more provided values are invalid.",
                        }
                    }
                },
                status=self.register_status,
            )
        auth = body["auth_data"]
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(auth["code_verifier"].encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        if auth["authorization_code"] != AUTH_CODE or challenge != self.code_challenge:
            return web.json_response(
                {"response": {"error": {"code": "BadCode"}}}, status=401
            )
        return web.json_response(
            {
                "response": {
                    "success": {
                        "tokens": {
                            "bearer": {
                                "access_token": ACCESS_TOKEN,
                                "refresh_token": REFRESH_TOKEN,
                                "expires_in": "3600",
                            },
                            "mac_dms": {
                                "adp_token": "adp",
                                "device_private_key": "key",
                            },
                            "website_cookies": [],
                            "store_authentication_cookie": {"cookie": "sac"},
                        },
                        "extensions": {
                            "device_info": {
                                "device_serial_number": body["registration_data"][
                                    "device_serial"
                                ],
                                "device_type": body["registration_data"]["device_type"],
                                "device_name": "Test's Home Assistant Amazon Orders",
                            },
                            "customer_info": {
                                "user_id": "amzn1.account.TEST",
                                "name": "Test",
                            },
                        },
                    }
                }
            }
        )

    async def _token(self, request: web.Request) -> web.Response:
        form = {key: str(value) for key, value in (await request.post()).items()}
        self.token_requests.append(form)
        if self.token_status != 200 or form.get("source_token") != REFRESH_TOKEN:
            return web.json_response(
                {
                    "error": "invalid_grant",
                    "error_description": "The token is not valid",
                },
                status=self.token_status if self.token_status != 200 else 400,
            )
        if form["requested_token_type"] == "access_token":
            return web.json_response({"access_token": ACCESS_TOKEN, "expires_in": 3600})
        self.issued_sessions += 1
        return web.json_response(
            {
                "response": {
                    "tokens": {
                        "cookies": {
                            ".amazon.it": [
                                {
                                    "Name": "session-token",
                                    "Value": f'"{self.session_cookie}"',
                                },
                                {
                                    "Name": "at-acbit",
                                    "Value": "Atza|test/with+symbols=",
                                },
                            ]
                        }
                    }
                }
            }
        )

    async def _deregister(self, request: web.Request) -> web.Response:
        self.deregister_auth = request.headers.get("Authorization")
        if self.deregister_status != 200:
            return web.json_response({"error": "nope"}, status=self.deregister_status)
        return web.json_response({"response": {"success": {}}})

    # --- pages for signed-in users ------------------------------------

    def _signed_in(self, request: web.Request) -> bool:
        return (
            not self.always_ask_signin
            and request.cookies.get("session-token") == self.session_cookie
            and request.cookies.get("at-acbit") == "Atza|test/with+symbols="
        )

    def _fixture(self, name: str) -> web.Response:
        return web.Response(
            text=(FIXTURES / name).read_text(encoding="utf-8"), content_type="text/html"
        )

    async def _orders(self, request: web.Request) -> web.StreamResponse:
        self.page_requests.append(request)
        if not self._signed_in(request):
            raise web.HTTPFound("/ap/signin?openid.return_to=orders")
        if self.orders_redirect is not None:
            raise web.HTTPFound(self.orders_redirect)
        if self.orders_status != 200:
            return _page("Service unavailable", status=self.orders_status)
        return self._fixture(self.orders_page)

    async def _tracking(self, request: web.Request) -> web.StreamResponse:
        self.page_requests.append(request)
        if not self._signed_in(request):
            raise web.HTTPFound("/ap/signin?openid.return_to=tracking")
        return self._fixture("tracking_delivered.html")

    async def _elsewhere(self, request: web.Request) -> web.Response:
        self.page_requests.append(request)
        return _page("elsewhere")
