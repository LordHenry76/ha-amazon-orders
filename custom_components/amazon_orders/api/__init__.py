"""Amazon client used by the integration. No Home Assistant imports in this package."""

from __future__ import annotations

from .auth import AmazonAuth, LoginData
from .client import AmazonOrdersClient
from .exceptions import (
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonConnectionError,
    AmazonError,
    AmazonParseError,
)
from .models import Milestone, Order, OrderItem, Shipment, Tracking, TrackingEvent

__all__ = [
    "AmazonAuth",
    "AmazonAuthError",
    "AmazonCaptchaError",
    "AmazonConnectionError",
    "AmazonError",
    "AmazonOrdersClient",
    "AmazonParseError",
    "LoginData",
    "Milestone",
    "Order",
    "OrderItem",
    "Shipment",
    "Tracking",
    "TrackingEvent",
]
