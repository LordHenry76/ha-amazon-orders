# Amazon Orders for Home Assistant (unofficial)

Track the deliveries of your Amazon orders in Home Assistant: status, expected delivery and
progress of each package, so you can build automations on them.

> **Work in progress.** Signing in, reading the orders and the entities work. So far they have
> been tried on an order just placed and on packages already delivered: what Amazon reports for
> a package that has been shipped or is out for delivery has not been observed yet.

> **Unofficial.** Amazon has no public API for customers' orders. This integration reads the
> same pages the Amazon website shows you when you are signed in. It can break at any time if
> Amazon changes its website, and automated access may be against Amazon's conditions of use.
> Use at your own risk. Not affiliated with or endorsed by Amazon.

## Status

| Country | Status |
|---|---|
| Italy (`amazon.it`) | In development, tested |

Other Amazon sites are not offered in the setup form until someone has tested them.

## What you should know before using it

- **Two-step verification with an authenticator app is required** on your Amazon account.
  Codes sent by SMS do not work.
- **A device is registered on your Amazon account.** The integration signs in the way Amazon's
  own mobile apps do: it registers a virtual device named "Home Assistant Amazon Orders" and
  uses its token to renew the session, so you sign in only once.
- **Your password is not stored.** It is used once, during setup. What is stored, in the Home
  Assistant configuration like for every other integration, is the token of that device: who
  can read your Home Assistant configuration can read your Amazon orders.

## Installation

### HACS (custom repository)
1. HACS → ⋮ → **Custom repositories** → add `https://github.com/LordHenry76/ha-amazon-orders`, category **Integration**.
2. Install **Amazon Orders** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Amazon Orders**.

### Manual
Copy `custom_components/amazon_orders` into your `config/custom_components/` folder and restart.

## Configuration

The setup form asks for:

- the Amazon site you place your orders on;
- the email and the password of your Amazon account;
- the 6-digit code shown at that moment by your authenticator app.

If Amazon refuses the sign-in, the form shows Amazon's own message (wrong password, code not
valid, …). If Amazon asks for a CAPTCHA the integration cannot go on: try again later.

When Amazon stops accepting the stored session, Home Assistant asks you to sign in again. The
device of the old session is then replaced by a new one.

## Entities

All entities belong to one device, "Amazon". The "next package" is, among the packages not
delivered yet, the one that has gone through the most steps.

| Entity | State |
|---|---|
| Packages in transit | How many packages are on their way. The attribute `packages` lists them. |
| Package in transit (binary sensor) | On while at least one package is on its way. |
| Next package status | Amazon's state code in lower case, `idle` when nothing is on its way. |
| Next package step | The current step of the delivery, in the language of the website (on amazon.it: "Ordinato", "Spedito", "In consegna", "Consegnato"). The attributes `step_index`, `step_count` and `steps` give its position and the whole list. |
| Next package progress | Progress of the delivery, in percent. |
| Next package delivery | Amazon's own message about the delivery, in the language of the website (e.g. "In arrivo domani"). |
| Next package last event | The latest entry of the tracking history; date, time and place are attributes. |
| Last delivered package | Amazon's message for the most recent delivery (e.g. "Consegnato 4 ottobre"). |

The status sensor and the last delivered package carry the details as attributes: `order_id`,
`order_placed`, `items`, `status`, `milestone`, `step`, `step_label`, `progress`, `expected`,
`carrier`, `status_text`, `status_detail`, `last_event`, `tracking_url`.

Limits:

- Only the shipments Amazon offers a "Track package" page for are followed. Orders fulfilled by
  third-party sellers often have none, and are ignored.
- Only the orders of the last three months are read.
- The state codes seen so far are `order_placed` and `delivered`; the others are shown as Amazon
  sends them.

## Event

`amazon_orders_package_update` fires when a package appears (`type: new`), when its state, step,
progress or delivery message changes (`type: update`) and when it is delivered
(`type: delivered`):

```yaml
triggers:
  - trigger: event
    event_type: amazon_orders_package_update
    event_data:
      type: delivered
actions:
  - action: notify.mobile_app_iphone
    data:
      title: "📦 Amazon"
      message: "{{ trigger.event.data.items | join(', ') }}: {{ trigger.event.data.expected }}"
```

Event data: `type`, `order_id`, `status`, `previous_status`, `step`, `step_label`, `progress`, `expected`,
`carrier`, `items`, `last_event`, `is_delivered`, `config_entry_id`.

Nothing fires for what is found when Home Assistant starts.

## Dashboard card

[`examples/package-card.yaml`](examples/package-card.yaml) is a card for the next package
(Italian entity ids and texts: [`examples/package-card.it.yaml`](examples/package-card.it.yaml)).
It stays on one line while nothing is on its way and expands when a package is: progress bar,
expected delivery, the four-step timeline, current step and last tracking event. Once the
package is delivered the card goes back to idle.

It needs three frontend cards from HACS: [Mushroom](https://github.com/piitaya/lovelace-mushroom),
[card-mod](https://github.com/thomasloven/lovelace-card-mod) and
[Vertical Stack In Card](https://github.com/ofekashery/vertical-stack-in-card).

Entity ids depend on your Home Assistant language, so check yours in **Settings → Entities**
and find/replace them in the file before pasting.

## Options

**Configure** on the integration card sets how often Amazon is checked, in seconds:

- the idle interval (default 900) is used normally;
- the active interval (default 120) is used while a package has reached its third step, out for
  delivery.

At each check the orders list is read once, plus one tracking page for each package not
delivered yet. A delivered package is read once and then remembered, also across restarts.
If Amazon answers with a CAPTCHA the integration waits one hour before trying again. Very short
intervals may make Amazon block the requests.

## Removing the integration

Deleting the integration also tries to remove its device from your Amazon account. If that fails (the
Home Assistant log says so), remove "Home Assistant Amazon Orders" yourself from the devices
listed in your Amazon account.

## How it works

1. During setup the integration signs in on Amazon with your credentials and registers a
   virtual device, as Amazon's mobile apps do. Amazon answers with a long-lived token.
2. With that token it asks Amazon for the session cookies of your Amazon site, whenever needed.
3. With those cookies it reads the orders list and the "Track package" page of each shipment.
   The state of a package comes from the data that page embeds for its own scripts, which does
   not depend on the language of the website.

The session cookies are sent only to the Amazon site of your account.

## Diagnostics

**Download diagnostics** on the integration card produces a file meant to be attached to issues.
It contains the state of each package as the integration last read it and the names of the
fields Amazon sent, and leaves out or redacts your email, the tokens, order numbers, products,
amounts, links and the places of the tracking events. Have a look at it before posting it anyway.

## Brand images

The integration ships its icon in `custom_components/amazon_orders/brand/`, picked up
automatically by Home Assistant 2026.3 or newer (older versions just show no icon).
The Amazon name and logo are trademarks of Amazon.com, Inc. or its affiliates, used for
identification purposes only.

## Development

```
pip install pytest-homeassistant-custom-component beautifulsoup4
pytest -q
```

The tests never contact Amazon: they run against synthetic pages in `tests/fixtures/`, which
reproduce the structure of the real ones with invented data, and against a small fake of the
Amazon endpoints (`tests/fake_amazon.py`).

## Credits

The sign-in flow is adapted from [aioamazondevices](https://github.com/chemelli74/aioamazondevices)
by Simone Chemelli and contributors (Apache License 2.0), the library behind Home Assistant's
Alexa Devices integration. See [NOTICE](NOTICE).

## License

MIT. Third-party notices are in [NOTICE](NOTICE).
