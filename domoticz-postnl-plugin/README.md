# Domoticz PostNL Pakket Tracking plugin

Portering van de Toon-app [postnl](https://github.com/ToonSoftwareCollective/postnl)
(ToonSoftwareCollective) naar een Domoticz Python-plugin. Logt in op je PostNL-account
en toont inkomende/verzonden zendingen (Track & Trace) als Domoticz-devices.

## Wat je krijgt

Na installatie verschijnen 4 devices:

- **Aantal onderweg** — teller met het aantal inkomende pakketten dat nog niet bezorgd/retour is.
- **Inkomende pakketten** — tekstregel per pakket: afzender, status, tijdvenster.
- **Verzonden pakketten** — zelfde, maar voor pakketten die jij verstuurt.
- **Vandaag bezorgd** — schakelaar (On/Off), alleen-lezen indicator of er vandaag iets is bezorgd.

## Vereisten

- Domoticz met Python-plugin support (standaard aanwezig in moderne Domoticz).
- Python-module `requests` beschikbaar voor de Python die Domoticz gebruikt:
  ```bash
  sudo pip3 install requests
  ```
- Een PostNL-account (jouw.postnl.nl) met minstens één gevolgd pakket.

## Installatie

1. Kopieer deze map naar de Domoticz `plugins`-map, bijvoorbeeld:
   ```bash
   cp -r domoticz-postnl-plugin /home/pi/domoticz/plugins/PostNL
   ```
2. Herstart Domoticz (nodig zodat de plugin wordt gedetecteerd):
   ```bash
   sudo systemctl restart domoticz
   ```
3. Ga in Domoticz naar **Instellingen → Hardware** en voeg nieuwe hardware toe van het
   type **PostNL Pakket Tracking**.
4. Vul je PostNL e-mailadres en wachtwoord in, kies een interval (standaard 60 minuten)
   en sla op.
5. De 4 devices verschijnen onder **Instellingen → Apparaten** (evt. eerst "toegevoegd"
   filter gebruiken) — zet ze op je dashboard.

## Belangrijk om te weten

- **Geen officiële API.** Het inloggen gebruikt dezelfde (niet-officiële, reverse-engineered)
  Akamai/Janrain hosted-login flow als de originele Toon-app. Als PostNL hun inlogproces
  wijzigt, kan de plugin stuk gaan totdat hij wordt bijgewerkt.
- **Wachtwoord opslag.** Je PostNL-wachtwoord staat, net als bij elke Domoticz hardware-plugin,
  in de Domoticz-database (versleuteld veld in de hardware-instellingen). De plugin stuurt het
  alleen naar PostNL zelf, nergens anders heen.
- **Niet te vaak opvragen.** Kies geen interval korter dan 15 minuten — te vaak inloggen kan
  door PostNL als verdacht worden gezien.
- **Refresh token.** Na de eerste succesvolle login wordt een refresh-token onthouden
  (opgeslagen in de Domoticz plugin-configuratie, niet in een los bestand), zodat niet
  elke ronde een volledige login nodig is.
- De HTTP-calls naar PostNL gebeuren synchroon (via `requests`) tijdens de heartbeat-tick
  waarop wordt bijgewerkt; dit duurt doorgaans enkele seconden.

## Troubleshooting

Zet **Debug** aan bij de hardware-instellingen en bekijk de Domoticz-log voor gedetailleerde
foutmeldingen (login-stappen, HTTP-statuscodes). Bij herhaalde login-fouten: log handmatig in
op jouw.postnl.nl om te controleren of PostNL bijvoorbeeld een CAPTCHA of 2FA vraagt — dat kan
deze niet-interactieve flow niet afhandelen.
