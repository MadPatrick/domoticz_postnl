# Domoticz PostNL Package Tracking plugin

A port of the Toon app [postnl](https://github.com/ToonSoftwareCollective/postnl)
(ToonSoftwareCollective) to a Domoticz Python plugin. It logs in to your PostNL account
and shows incoming and sent shipments (Track & Trace) as Domoticz devices.

**Version 0.2.2** - early release. Feedback and ideas are very welcome, see
[Feedback](#feedback).

## What you get

After installation 5 devices appear (device names are in English, the text inside follows the
selected language). Each text device holds at most 10 lines, one package per line, in the form
`[dd/mm] who: status time(s)`, for example `[06/08] bol: In transit 11:00-13:30`:

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
- A PostNL account (jouw.postnl.nl) with at least one tracked package.

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
   **PostNL Package Tracking**.
4. Enter your PostNL e-mail address and password, choose a poll interval (default 60 minutes),
   how many days delivered packages should stay visible (default 2), the language for the
   device text, and optionally your own text for "nothing delivered" - then save.
5. The 5 devices appear under **Setup -> Devices** (you may need to use the "add" filter
   first) - put them on your dashboard.

## Good to know

- **No official API.** The login uses the same unofficial, reverse-engineered Akamai/Janrain
  hosted-login flow as the original Toon app. If PostNL changes their login process, the
  plugin may break until it is updated.
- **Password storage.** Like with any Domoticz hardware plugin, your PostNL password is stored
  in the Domoticz database (as an encrypted field in the hardware settings). The plugin only
  sends it to PostNL itself, nowhere else.
- **Do not poll too often.** Do not choose an interval shorter than 15 minutes - logging in too
  often may be seen as suspicious by PostNL.
- **Refresh token.** After the first successful login a refresh token is remembered (stored in
  the Domoticz plugin configuration, not in a separate file), so that not every update needs a
  full login.
- The HTTP calls to PostNL are made synchronously (via `requests`) during the heartbeat tick on
  which the update runs; this usually takes a few seconds.

## Troubleshooting

Enable **Debug** in the hardware settings and check the Domoticz log for detailed error
messages (login steps, HTTP status codes). On repeated login errors: log in manually at
jouw.postnl.nl to check whether PostNL asks for a CAPTCHA or 2FA, which this non-interactive
flow cannot handle.

## Feedback

This is an early release, so rough edges are to be expected. Feedback is very welcome:
does the login work for you, are packages shown wrong or missing, did you run into errors?
Ideas for extensions are welcome too. Please open an issue on GitHub or reply on the
Domoticz forum.
