# Domoticz PostNL, DHL & DPD Package Tracking plugin

A port of the Toon app [postnl](https://github.com/ToonSoftwareCollective/postnl)
(ToonSoftwareCollective) to a Domoticz Python plugin. It logs in to your PostNL account,
your My DHL account and/or your myDPD account and shows incoming and sent shipments (Track & Trace) as Domoticz
devices.

**Version 1.1.0** - adds DHL (DHL eCommerce NL / DHL Parcel, via your My DHL account at
[my.dhlecommerce.nl](https://my.dhlecommerce.nl)). Feedback and ideas are very welcome, see
[Feedback](#feedback).

## What you get

After installation 5 devices appear (device names are in English, the text inside follows the
selected language). Each text device holds at most 10 lines, one package per line, in the form
`[dd/mm] who: status time(s)`, for example `[06/08] bol: In transit 11:00-13:30`. When more than
one carrier is configured, the carrier is added after the name, for example
`[06/08] bol (PostNL): In transit 11:00-13:30` and `[06/08] Coolblue (DHL): In transit 10:00-12:00`.
PostNL, DHL and DPD packages are combined in the same devices:

- **Packages in transit** - counter with the number of incoming packages that have not yet
  been delivered or returned.
- **Packages Incoming** - only the packages that have not been delivered yet, sorted by
  expected delivery date.
- **Packages Delivered** - packages that have already been delivered, up to a configurable
  number of days back (hardware setting "Show delivered for (days)", default 2), newest first.
  The text shown when nothing was delivered can also be set (hardware setting "Text shown when
  nothing was delivered", default "Niets onderweg").
- **Packages Sent** - same layout (not yet delivered + recently delivered, together also max.
  10 lines), but for packages you send yourself. Here "who" shows the recipient
  (company/name/town) instead of the sender, because for outgoing packages you are the sender.
- **Delivered Today** - switch (On/Off), a read-only indicator that shows whether anything was
  delivered today.

The plugin itself (logs, internal code) is entirely in English. The language of the device
text (status words such as "In transit"/"Onderweg", "Delivered"/"Bezorgd", empty-list texts)
is set separately with the **"Device text language"** setting (English or Dutch).

## Requirements

- Domoticz with Python plugin support and "Extended Plugin Settings" (free parameter fields,
  `type="number"`/`type="boolean"`, `<group>`). At the time of writing this is only available
  in the `development` branch of Domoticz, not yet in a stable release. On an older Domoticz
  version the fields "Poll interval (minutes)", "Show delivered for (days)" and "Debug" are not
  displayed or stored correctly - in that case use a build from before this change (see the git
  history) that works with the classic Mode1/Mode2/Mode6 dropdowns.
- The Python module `requests`, available to the Python that Domoticz uses:
  ```bash
  sudo pip3 install requests
  ```
- A PostNL account (jouw.postnl.nl), a My DHL account (my.dhlecommerce.nl) and/or a myDPD
  account (myDPD app). Fill in at least one; leave the others empty to skip that carrier.

## Installation

1. Clone (or copy) this repository into the Domoticz `plugins` folder, for example:
   ```bash
   cd /home/pi/domoticz/plugins
   git clone https://github.com/MadPatrick/domoticz_postnl.git PostNL
   ```
2. Restart Domoticz (required so the plugin is detected):
   ```bash
   sudo systemctl restart domoticz
   ```
3. In Domoticz go to **Setup -> Hardware** and add new hardware of the type
   **PostNL, DHL & DPD Package Tracking**.
4. Enter your PostNL e-mail address and password, your My DHL e-mail address and
   password and/or your myDPD e-mail address and password, choose a poll interval (default 60 minutes),
   how many days delivered packages should stay visible (default 2), the language for the
   device text, and optionally your own text for "nothing delivered" - then save.
5. The 5 devices appear under **Setup -> Devices** (you may need to use the "add" filter
   first) - put them on your dashboard.

## DHL

- Which parcels: everything that shows up when you log in at
  [my.dhlecommerce.nl](https://my.dhlecommerce.nl) - incoming parcels, returns you send back
  (shown under **Packages Sent**) and parcels you sent yourself. DHL Express shipments are not
  part of that account and are not tracked.
- No tracking numbers or API key needed: just like PostNL the plugin reads the list from your
  account. A parcel only appears once DHL has linked it to your account (usually via your
  e-mail address or postal code).
- Extra statuses for DHL: "Ready for pickup" / "Klaar om op te halen" (waiting at a DHL
  ServicePoint or locker) and "Delayed" / "Vertraagd" (DHL reports a problem or intervention).
- DHL times are converted from UTC to the local time of the Domoticz server.
- If one carrier fails (login error, outage), the other one is still updated and the last known
  packages of the failing carrier stay on the devices.

## DPD

**Version 1.2.0** adds DPD (DPD Netherlands, via your myDPD account - the same account as the
myDPD app).

- Fill in the myDPD e-mail address and password in the hardware settings. An account that only
  signs in with Google, Apple or Facebook has no password and cannot be used; set a password in
  the myDPD app first.
- Which parcels: everything that shows up in the myDPD app - incoming parcels under the
  incoming devices, parcels you sent under **Packages Sent**. A parcel appears once DPD has
  linked it to your account (via your e-mail address or after you added it in the app).
- No tracking numbers or API key needed. The plugin uses the same (unofficial) login chain as
  the myDPD mobile app, as documented by the Home Assistant integration
  [ha-dpd](https://github.com/ha-parcel-integrations/ha-dpd).
- Extra statuses for DPD: "Ready for pickup" / "Klaar om op te halen" (waiting at a DPD
  ParcelShop) and "Delayed" / "Vertraagd" (a missed delivery attempt).
- The delivery time window is shown as soon as DPD knows it (usually on the day of delivery);
  before that only the expected day is shown.

## UPS

UPS is not supported. UPS has no consumer account (My Choice) feed that can be read without an
interactive browser login with bot protection, and its public tracking page only works per
tracking number with a very low request limit. That does not fit a plugin that reads your
parcels from your account automatically.

## Good to know

- **No official API.** The PostNL login uses the same unofficial, reverse-engineered
  Akamai/Janrain hosted-login flow as the original Toon app; the DHL part uses the same
  (unofficial) JSON API as the my.dhlecommerce.nl website and the DPD part the (unofficial) API of
the myDPD app. If PostNL, DHL or DPD change their login
  process, the plugin may break until it is updated.
- **Password storage.** Like with any Domoticz hardware plugin, your PostNL, DHL and DPD passwords are
  stored in the Domoticz database (as password fields in the hardware settings). The plugin only
  sends them to PostNL, DHL and DPD themselves, nowhere else.
- **Do not poll too often.** Do not choose an interval shorter than 15 minutes - logging in too
  often may be seen as suspicious by PostNL.
- **Refresh token.** After the first successful login a refresh token is remembered (stored in
  the Domoticz plugin configuration, not in a separate file), so that not every update needs a
  full login.
- The HTTP calls to PostNL are made synchronously (via `requests`) during the heartbeat tick on
  which the update runs; this usually takes a few seconds.

## Troubleshooting

Enable **Debug** in the hardware settings and check the Domoticz log for detailed error
messages (login steps, HTTP status codes). Errors are prefixed with the carrier
(`PostNL update failed: ...` / `DHL update failed: ...` / `DPD update failed: ...`). On repeated login errors: log in manually at
jouw.postnl.nl to check whether PostNL asks for a CAPTCHA or 2FA, which this non-interactive
flow cannot handle.

## Feedback

This is an early release, so rough edges are to be expected. Feedback is very welcome:
does the login work for you, are packages shown wrong or missing, did you run into errors?
Ideas for extensions are welcome too. Please open an issue on GitHub or reply on the
Domoticz forum.
