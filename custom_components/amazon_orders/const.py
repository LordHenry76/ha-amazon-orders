"""Constants for the Amazon Orders integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "amazon_orders"

# Config entry data. Email, password and OTP are asked once by the config flow;
# only the tokens returned by the device registration are stored, never the password.
CONF_LOGIN_DATA: Final = "login_data"
CONF_SITE: Final = "site"
CONF_OTP: Final = "otp"

# Amazon sites the integration can be set up for, by the country code shown in
# the setup form. Only tested ones are listed. The config entry stores the site.
SITES: Final = {"it": "amazon.it"}
DEFAULT_COUNTRY: Final = "it"

# Polling: slow while nothing is on its way, fast while a package is out for
# delivery. Both are user-configurable (options flow), in seconds.
# Provisional values: Amazon's tolerance to polling has not been measured yet.
CONF_IDLE_INTERVAL: Final = "idle_interval"
CONF_ACTIVE_INTERVAL: Final = "active_interval"
DEFAULT_IDLE_INTERVAL: Final = 900
DEFAULT_ACTIVE_INTERVAL: Final = 120
MIN_IDLE_INTERVAL: Final = 300
MAX_IDLE_INTERVAL: Final = 3600
MIN_ACTIVE_INTERVAL: Final = 60
MAX_ACTIVE_INTERVAL: Final = 600

# A package is polled at the active interval from this many reached steps on.
# Amazon shows four steps: ordered, shipped, out for delivery, delivered (on
# amazon.it "Ordinato", "Spedito", "In consegna", "Consegnato").
OUT_FOR_DELIVERY_MIN_STEPS: Final = 3

# Tracking pages of shipments never seen before, read at most per update. It
# spreads the requests of the first run, when every recent order is new.
MAX_NEW_TRACKING_PER_UPDATE: Final = 5

# After a CAPTCHA, leave Amazon alone for a while.
CAPTCHA_BACKOFF: Final = timedelta(hours=1)

# Fired on the event bus when a package appears, changes state or is delivered.
EVENT_PACKAGE_UPDATE: Final = f"{DOMAIN}_package_update"

STORAGE_VERSION: Final = 1
