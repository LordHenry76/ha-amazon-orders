"""Exceptions raised by the Amazon client."""

from __future__ import annotations


class AmazonError(Exception):
    """Base class for every error raised by this package."""


class AmazonConnectionError(AmazonError):
    """Amazon could not be reached, or answered with an unexpected HTTP status."""


class AmazonAuthError(AmazonError):
    """Credentials were refused, or the stored session is no longer valid."""


class AmazonCaptchaError(AmazonError):
    """Amazon answered with a CAPTCHA or another anti-bot challenge."""


class AmazonParseError(AmazonError):
    """A page did not have the expected structure (Amazon changed it?)."""
