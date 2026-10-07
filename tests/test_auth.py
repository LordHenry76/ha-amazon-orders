"""Tests for sign-in, device registration and session renewal."""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.amazon_orders.api.auth import (
    APP_NAME,
    DEVICE_TYPE,
    AmazonAuth,
    LoginData,
)
from custom_components.amazon_orders.api.exceptions import (
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonConnectionError,
)

from .fake_amazon import ACCESS_TOKEN, EMAIL, OTP, PASSWORD, REFRESH_TOKEN, FakeAmazon

SITE = "amazon.it"


def _auth(session: aiohttp.ClientSession, fake: FakeAmazon) -> AmazonAuth:
    return AmazonAuth(session, signin_url=fake.url, api_url=fake.url)


async def test_login(fake_amazon: FakeAmazon, login_session) -> None:
    data = await _auth(login_session, fake_amazon).async_login(EMAIL, PASSWORD, OTP)

    assert data.refresh_token == REFRESH_TOKEN
    assert len(data.device_serial) == 32
    assert data.device_info["device_serial_number"] == data.device_serial
    assert data.customer_info["user_id"] == "amzn1.account.TEST"

    body = fake_amazon.register_body
    assert body is not None
    registration = body["registration_data"]
    assert registration["device_serial"] == data.device_serial
    assert registration["device_type"] == DEVICE_TYPE
    assert registration["app_name"] == APP_NAME
    assert registration["device_name"].endswith(APP_NAME)
    expected_client_id = f"{data.device_serial}#{DEVICE_TYPE}".encode().hex()
    assert body["auth_data"]["client_id"] == expected_client_id
    # The same "frc" value goes in the sign-in cookies and in the registration.
    assert fake_amazon.frc_cookie
    assert body["user_context_map"]["frc"] == fake_amazon.frc_cookie


async def test_login_without_otp_step(fake_amazon: FakeAmazon, login_session) -> None:
    fake_amazon.require_otp = False

    data = await _auth(login_session, fake_amazon).async_login(EMAIL, PASSWORD, "")

    assert data.refresh_token == REFRESH_TOKEN


async def test_login_wrong_password(fake_amazon: FakeAmazon, login_session) -> None:
    with pytest.raises(AmazonAuthError, match="Your password is incorrect"):
        await _auth(login_session, fake_amazon).async_login(EMAIL, "wrong", OTP)

    assert fake_amazon.register_body is None


async def test_login_wrong_otp(fake_amazon: FakeAmazon, login_session) -> None:
    with pytest.raises(AmazonAuthError, match="The code you entered is not valid"):
        await _auth(login_session, fake_amazon).async_login(EMAIL, PASSWORD, "000000")

    assert fake_amazon.register_body is None


async def test_login_captcha(fake_amazon: FakeAmazon, login_session) -> None:
    fake_amazon.captcha_on_login = True

    with pytest.raises(AmazonCaptchaError):
        await _auth(login_session, fake_amazon).async_login(EMAIL, PASSWORD, OTP)


async def test_login_registration_refused(
    fake_amazon: FakeAmazon, login_session
) -> None:
    fake_amazon.register_status = 401

    with pytest.raises(AmazonAuthError, match="InvalidValue") as err:
        await _auth(login_session, fake_amazon).async_login(EMAIL, PASSWORD, OTP)

    assert "secret-index" not in str(err.value)


async def test_login_needs_a_cookie_jar(fake_amazon: FakeAmazon, plain_session) -> None:
    with pytest.raises(TypeError):
        await _auth(plain_session, fake_amazon).async_login(EMAIL, PASSWORD, OTP)


async def test_login_amazon_unreachable(login_session, local_sockets) -> None:
    auth = AmazonAuth(
        login_session, signin_url="http://127.0.0.1:9", api_url="http://127.0.0.1:9"
    )

    with pytest.raises(AmazonConnectionError):
        await auth.async_login(EMAIL, PASSWORD, OTP)


async def test_get_cookies(fake_amazon: FakeAmazon, plain_session) -> None:
    cookies = await _auth(plain_session, fake_amazon).async_get_cookies(
        REFRESH_TOKEN, SITE
    )

    # Amazon wraps some values in quotes: they are removed.
    assert cookies == {
        "session-token": "session-1",
        "at-acbit": "Atza|test/with+symbols=",
    }
    request = fake_amazon.token_requests[0]
    assert request["requested_token_type"] == "auth_cookies"
    assert request["source_token_type"] == "refresh_token"
    assert request["domain"] == "www.amazon.it"


async def test_get_cookies_refresh_token_refused(
    fake_amazon: FakeAmazon, plain_session
) -> None:
    with pytest.raises(AmazonAuthError, match="The token is not valid"):
        await _auth(plain_session, fake_amazon).async_get_cookies("Atnr|revoked", SITE)


async def test_get_cookies_amazon_error(fake_amazon: FakeAmazon, plain_session) -> None:
    fake_amazon.token_status = 503

    with pytest.raises(AmazonConnectionError):
        await _auth(plain_session, fake_amazon).async_get_cookies(REFRESH_TOKEN, SITE)


async def test_deregister(fake_amazon: FakeAmazon, plain_session) -> None:
    await _auth(plain_session, fake_amazon).async_deregister(REFRESH_TOKEN, SITE)

    assert fake_amazon.deregister_auth == f"Bearer {ACCESS_TOKEN}"
    assert fake_amazon.token_requests[0]["requested_token_type"] == "access_token"


async def test_deregister_refused(fake_amazon: FakeAmazon, plain_session) -> None:
    fake_amazon.deregister_status = 400

    with pytest.raises(AmazonAuthError):
        await _auth(plain_session, fake_amazon).async_deregister(REFRESH_TOKEN, SITE)


def test_login_data_round_trip() -> None:
    data = LoginData(
        refresh_token="token",
        device_serial="SERIAL",
        device_info={"device_name": "name"},
        customer_info={"user_id": "id"},
    )

    assert LoginData.from_dict(data.as_dict()) == data
    assert "password" not in data.as_dict()
