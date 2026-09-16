"""
<plugin key="PostNL" name="PostNL Pakket Tracking" author="patrick" version="1.0.0" externallink="https://github.com/ToonSoftwareCollective/postnl">
    <description>
        <h2>PostNL Pakket Tracking</h2>
        <p>Toont inkomende en verzonden PostNL-zendingen (Track &amp; Trace) uit je PostNL-account.</p>
        <p>Portering van de Toon-app <i>postnl</i> (ToonSoftwareCollective) naar een Domoticz-plugin.</p>
        <h3>Vereisten</h3>
        <ul style="list-style-type:square">
            <li>Een PostNL-account (jouw.postnl.nl)</li>
            <li>Python-module 'requests' (pip3 install requests)</li>
        </ul>
        <h3>Let op</h3>
        <ul style="list-style-type:square">
            <li>Deze plugin gebruikt een niet-officiele, "reverse-engineered" inlogflow. Als PostNL het inlogproces wijzigt kan de plugin stoppen met werken.</li>
            <li>Vraag niet te vaak op (minimaal 15 minuten interval) om je account niet te blokkeren.</li>
        </ul>
    </description>
    <params>
        <param field="Username" label="PostNL e-mailadres" width="300px" required="true"/>
        <param field="Password" label="PostNL wachtwoord" width="300px" required="true" password="true"/>
        <param field="Mode1" label="Interval (minuten)" width="100px">
            <options>
                <option label="15" value="15"/>
                <option label="30" value="30"/>
                <option label="60" value="60" default="true"/>
                <option label="120" value="120"/>
            </options>
        </param>
        <param field="Mode2" label="Bezorgd tonen (dagen)" width="100px">
            <options>
                <option label="1" value="1"/>
                <option label="2" value="2" default="true"/>
                <option label="3" value="3"/>
                <option label="5" value="5"/>
                <option label="7" value="7"/>
            </options>
        </param>
        <param field="Mode6" label="Debug" width="100px">
            <options>
                <option label="Normaal" value="Normal" default="true"/>
                <option label="Debug" value="Debug"/>
            </options>
        </param>
    </params>
</plugin>
"""
import base64
import hashlib
import re
import secrets
import time
import urllib.parse

import Domoticz

try:
    import requests
except ImportError:
    requests = None

# ---- Fixed PostNL / Akamai identity constants (mirrors the account web app) ----
TENANT = "https://login.postnl.nl/101112a0-4a0f-4bbb-8176-2f1b2d370d7c"
OIDC_CLIENT = "deb0a372-6d72-4e09-83fe-997beacbd137"
REDIRECT_URI = "postnl://login"
SCOPE = "openid profile email poa-profiles-api"
CAPTURE_SERVER = "https://login.postnl.nl"
GRAPHQL_URL = "https://jouw.postnl.nl/account/api/graphql"
TT_URL = "https://jouw.postnl.nl/track-and-trace/api/trackAndTrace"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

GRAPHQL_QUERY = (
    "{ trackedShipments { "
    "receiverShipments { key creationDateTime title barcode delivered deliveredTimeStamp "
    "deliveryWindowFrom deliveryWindowTo shipmentType deliveryAddressType sourceDisplayName } "
    "senderShipments { key creationDateTime title barcode delivered deliveredTimeStamp "
    "deliveryWindowFrom deliveryWindowTo shipmentType deliveryAddressType sourceDisplayName } "
    "} }"
)

STATUS_NL = {
    "Open": "Onderweg",
    "InTransit": "Onderweg",
    "Delivered": "Bezorgd",
    "ReturnToSender": "Retour afzender",
}

UNIT_COUNT = 1
UNIT_INBOX = 2
UNIT_SENT = 3
UNIT_DELIVERED_TODAY = 4
UNIT_DELIVERED_LIST = 5

HEARTBEAT_SECONDS = 30
DEFAULT_DELIVERED_DAYS = 2
MAX_TEXT_LINES = 10


class PostNLError(Exception):
    pass


class PostNLAuthError(PostNLError):
    pass


class BasePlugin:
    def __init__(self):
        self.username = None
        self.password = None
        self.refresh_token = None
        self.debug_enabled = False
        self.available = True
        self.poll_interval_minutes = 60
        self.delivered_days = DEFAULT_DELIVERED_DAYS
        self.ticks_needed = 1
        self.tick_count = 0

    # ------------------------------------------------------------------ utils
    def debug(self, msg):
        if self.debug_enabled:
            Domoticz.Debug(str(msg))

    def load_refresh_token(self):
        try:
            cfg = Domoticz.Configuration()
        except Exception:
            cfg = {}
        self.refresh_token = (cfg or {}).get("refresh_token")

    def save_refresh_token(self):
        try:
            cfg = Domoticz.Configuration() or {}
        except Exception:
            cfg = {}
        if self.refresh_token:
            cfg["refresh_token"] = self.refresh_token
        else:
            cfg.pop("refresh_token", None)
        try:
            Domoticz.Configuration(cfg)
        except Exception as e:
            Domoticz.Error("Kon refresh token niet opslaan: {}".format(e))

    @staticmethod
    def _first_match(pattern, text, exclude=None):
        for m in re.findall(pattern, text or ""):
            if exclude is not None and m == exclude:
                continue
            return m
        return None

    @staticmethod
    def _extract_code(location):
        if not location:
            return None
        qs = urllib.parse.urlsplit(location).query
        params = urllib.parse.parse_qs(qs)
        values = params.get("code")
        return values[0] if values else None

    @staticmethod
    def _fmt_time(iso_ts):
        m = re.search(r"T(\d{2}:\d{2})", iso_ts or "")
        return m.group(1) if m else ""

    @staticmethod
    def _fmt_date(iso_ts):
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", iso_ts or "")
        return "{}-{}-{}".format(m.group(3), m.group(2), m.group(1)) if m else ""

    # ------------------------------------------------------------- login flow
    def full_login(self):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        code_verifier = secrets.token_hex(32)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")
        state = secrets.token_hex(16)

        auth_url = TENANT + "/login/authorize"
        auth_params = {
            "client_id": OIDC_CLIENT,
            "response_type": "code",
            "scope": SCOPE,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        # Step 1: authorize -> redirect to hosted login page
        r1 = session.get(auth_url, params=auth_params, allow_redirects=False, timeout=20)
        authui = r1.headers.get("Location")
        if not authui or "auth-ui" not in authui:
            raise PostNLError("Geen hosted-login redirect ontvangen van authorize.")

        # Step 2: load hosted login page (sets _csrf_token cookie, exposes capture settings)
        r2 = session.get(authui, allow_redirects=False, timeout=20)
        body = r2.text
        app_id = self._first_match(r"appId:\s*'([^']*)'", body, exclude="None")
        client_id = self._first_match(r"clientId:\s*'([^']*)'", body)
        flow_name = self._first_match(r"flowName:\s*'([^']*)'", body) or "standard"
        csrf = None
        for c in session.cookies:
            if c.name == "_csrf_token":
                csrf = c.value
        if not app_id or not client_id:
            raise PostNLError("Kon login-widget instellingen niet lezen.")
        self.debug("capture appId={} client={} flow={}".format(app_id, client_id, flow_name))

        # Step 2b: flow file -> current flow version
        flow_js_url = "https://ssl-static.janraincapture.com/widget_data/flow.js:{}:nl-NL:HEAD:{}".format(
            app_id, flow_name
        )
        r_flow = session.get(flow_js_url, timeout=20)
        flow_version = self._first_match(r'"version":\s*"([^"]*)"', r_flow.text) or "HEAD"

        # Step 3: submit credentials to the capture widget
        capture_redirect = authui + "&socialRedirect=True"
        trans_id = secrets.token_hex(20)
        session.post(
            CAPTURE_SERVER + "/widget/traditional_signin.jsonp",
            data={
                "utf8": "✓",
                "js_version": "d445bf4",
                "capture_screen": "signIn",
                "capture_transactionId": trans_id,
                "flow": flow_name,
                "client_id": client_id,
                "redirect_uri": capture_redirect,
                "response_type": "token",
                "flow_version": flow_version,
                "settings_version": "",
                "locale": "nl-NL",
                "recaptcha_version": "2",
                "form": "signInForm",
                "signInEmailAddress": self.username,
                "currentPassword": self.password,
            },
            headers={"Referer": authui, "Origin": "https://login.postnl.nl"},
            timeout=20,
        )

        # Step 3b: fetch the sign-in result (holds the short-lived capture access token)
        r3b = session.get(
            CAPTURE_SERVER + "/widget/get_result.jsonp",
            params={"transactionId": trans_id, "cache": int(time.time() * 1000)},
            headers={"Referer": authui},
            timeout=20,
        )
        if "invalidCredentials" in r3b.text or "invalidPassword" in r3b.text:
            raise PostNLError("PostNL heeft gebruikersnaam/wachtwoord geweigerd.")
        cap_token = self._first_match(r'"accessToken":"([^"]*)"', r3b.text)
        if not cap_token:
            raise PostNLError("Geen capture access token ontvangen (login mislukt).")

        # Step 4: hand the capture token to auth-ui token-url, establishing the SSO session cookie
        authui_query = urllib.parse.parse_qs(urllib.parse.urlsplit(authui).query)
        session.post(
            TENANT + "/auth-ui/token-url",
            params=authui_query,
            data={
                "authenticated": "True",
                "registering": "False",
                "accessToken": cap_token,
                "_csrf_token": csrf or "",
            },
            headers={"Referer": authui},
            timeout=20,
        )

        # Step 4b: re-request authorize; with the SSO cookie it redirects with ?code=
        r4b = session.get(auth_url, params=auth_params, allow_redirects=False, timeout=20)
        loc = r4b.headers.get("Location")
        code = self._extract_code(loc)
        hops = 0
        while not code and loc and loc.startswith("https://") and hops < 6:
            hops += 1
            rn = session.get(loc, allow_redirects=False, timeout=20)
            loc = rn.headers.get("Location")
            code = self._extract_code(loc)
        if not code:
            raise PostNLError("Geen authorization code ontvangen na login.")

        # Step 5: exchange the code for tokens
        r5 = session.post(
            TENANT + "/login/token",
            data={
                "grant_type": "authorization_code",
                "client_id": OIDC_CLIENT,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": code_verifier,
            },
            timeout=20,
        )
        if r5.status_code != 200:
            raise PostNLError("Token exchange mislukt (status {}).".format(r5.status_code))
        tokens = r5.json()
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token:
            raise PostNLError("Geen access token ontvangen van token endpoint.")
        if refresh_token:
            self.refresh_token = refresh_token
            self.save_refresh_token()
        return access_token

    def refresh_access_token(self):
        if not self.refresh_token:
            return None
        r = requests.post(
            TENANT + "/login/token",
            data={
                "grant_type": "refresh_token",
                "client_id": OIDC_CLIENT,
                "refresh_token": self.refresh_token,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        if r.status_code != 200:
            self.debug("Refresh token geweigerd (status {}).".format(r.status_code))
            return None
        tokens = r.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return None
        new_rt = tokens.get("refresh_token")
        if new_rt:
            self.refresh_token = new_rt
            self.save_refresh_token()
        return access_token

    def get_access_token(self, force_full=False):
        if not force_full:
            token = self.refresh_access_token()
            if token:
                self.debug("Hergebruikt login via refresh token.")
                return token
        return self.full_login()

    # ---------------------------------------------------------------- data
    def track_and_trace(self, key, barcode, access_token):
        try:
            r = requests.get(
                "{}/{}".format(TT_URL, key),
                params={"language": "nl"},
                headers={"Authorization": "Bearer " + access_token, "User-Agent": USER_AGENT},
                timeout=20,
            )
            if r.status_code != 200:
                return None
            return r.json().get(barcode)
        except Exception as e:
            self.debug("Track & trace opvragen mislukt voor {}: {}".format(barcode, e))
            return None

    def build_entry(self, item, role, access_token):
        barcode = item.get("barcode") or ""
        key = item.get("key")
        title = item.get("title") or ""
        stype = item.get("shipmentType") or ""
        delivered = item.get("delivered")
        dts = item.get("deliveredTimeStamp") or ""
        dwf = item.get("deliveryWindowFrom") or ""
        dwt = item.get("deliveryWindowTo") or ""

        sender_company = title
        sender_last = ""
        sender_town = ""
        pickup = ""
        tf_from = ""
        tf_to = ""
        deldate = ""
        rcpt_street = ""
        rcpt_house = ""
        rcpt_town = ""
        tt_delivered = None
        tt_return = None
        tt_atretail = None
        tt_deldate = ""

        colli = self.track_and_trace(key, barcode, access_token) if key and barcode else None
        if colli:
            tt_delivered = colli.get("isDelivered")
            tt_return = colli.get("isReturnShipment")
            tt_atretail = colli.get("isAtRetailLocation")
            tt_deldate = colli.get("deliveryDate") or ""
            eta = colli.get("eta") or {}
            tf_from = eta.get("start") or ""
            tf_to = eta.get("end") or ""
            sender_block = colli.get("sender") or {}
            s_company = sender_block.get("companyName") or ""
            s_person = sender_block.get("personName") or ""
            sender_town = (sender_block.get("address") or {}).get("town") or ""
            recipient_addr = (colli.get("recipient") or {}).get("address") or {}
            rcpt_street = recipient_addr.get("street") or ""
            rcpt_house = recipient_addr.get("houseNumber") or ""
            rcpt_town = recipient_addr.get("town") or ""
            pickup = (colli.get("retailDeliveryLocation") or {}).get("name") or ""
            if not sender_company:
                sender_company = s_company or ""
                if not s_company:
                    sender_last = s_person

        if delivered is True or tt_delivered is True:
            status = "Delivered"
            deldate = dts or tt_deldate
        elif tt_return is True:
            status = "ReturnToSender"
        elif pickup or tt_atretail is True:
            status = "InTransit"
        else:
            f = tf_from or dwf
            t = tf_to or dwt
            status = "Open" if f else "InTransit"
            tf_from, tf_to = f, t

        if not tf_from:
            tf_from = dwf
        if not tf_to:
            tf_to = dwt

        return {
            "role": role,
            "barcode": barcode,
            "shipmentType": stype,
            "title": title,
            "status": status,
            "deliveryDate": deldate,
            "from": tf_from,
            "to": tf_to,
            "senderCompany": sender_company,
            "senderLast": sender_last,
            "senderTown": sender_town,
            "recipientStreet": rcpt_street,
            "recipientHouse": rcpt_house,
            "recipientTown": rcpt_town,
            "pickupLocation": pickup,
        }

    def fetch_shipments(self, access_token):
        r = requests.post(
            GRAPHQL_URL,
            json={"query": GRAPHQL_QUERY},
            headers={"Authorization": "Bearer " + access_token, "User-Agent": USER_AGENT},
            timeout=20,
        )
        if r.status_code == 401:
            raise PostNLAuthError("Unauthorized")
        if r.status_code != 200:
            raise PostNLError("Ophalen zendingen mislukt (status {}).".format(r.status_code))
        payload = r.json()
        shipments = (payload.get("data") or {}).get("trackedShipments") or {}
        receiver_raw = shipments.get("receiverShipments") or []
        sender_raw = shipments.get("senderShipments") or []

        receiver = [self.build_entry(i, "receiver", access_token) for i in receiver_raw]
        sender = [self.build_entry(i, "sender", access_token) for i in sender_raw]
        return receiver, sender

    # ------------------------------------------------------------- devices
    def format_line(self, e):
        who = e["senderCompany"] or e["senderLast"] or e["recipientTown"] or "Onbekend"
        status_nl = STATUS_NL.get(e["status"], e["status"])

        extra = ""
        if e["status"] == "Delivered" and e["deliveryDate"]:
            date = self._fmt_date(e["deliveryDate"])
            t = self._fmt_time(e["deliveryDate"])
            extra = " ".join(p for p in (date, t) if p)
        elif e["from"]:
            date = self._fmt_date(e["from"])
            t_from = self._fmt_time(e["from"])
            t_to = self._fmt_time(e["to"])
            window = "{}-{}".format(t_from, t_to) if t_from and t_to else (t_from or t_to)
            extra = " ".join(p for p in (date, window) if p)

        if extra:
            return "{}: {} ({})".format(who, status_nl, extra)
        return "{}: {}".format(who, status_nl)

    def _pending_entries(self, entries):
        """Nog niet bezorgd (en geen retour), gesorteerd op verwachte datum/tijd."""
        pending = [e for e in entries if e["status"] not in ("Delivered", "ReturnToSender")]
        pending.sort(key=lambda e: e["from"] or "")
        return pending[:MAX_TEXT_LINES]

    def _recent_delivered(self, entries):
        """Al bezorgd binnen de laatste self.delivered_days dagen, nieuwste eerst."""
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - self.delivered_days * 86400))
        delivered = [
            e for e in entries
            if e["status"] == "Delivered" and e["deliveryDate"][:10] >= cutoff
        ]
        delivered.sort(key=lambda e: e["deliveryDate"] or "", reverse=True)
        return delivered[:MAX_TEXT_LINES]

    def update_devices(self, receiver, sender):
        pending = self._pending_entries(receiver)
        Devices[UNIT_COUNT].Update(nValue=len(pending), sValue=str(len(pending)))

        pending_lines = [self.format_line(e) for e in pending]
        text_pending = "\n".join(pending_lines) or "Geen pakketten onderweg"
        Devices[UNIT_INBOX].Update(nValue=0, sValue=text_pending[:400])

        delivered_lines = [self.format_line(e) for e in self._recent_delivered(receiver)]
        text_delivered = "\n".join(delivered_lines) or "Nog niets bezorgd"
        Devices[UNIT_DELIVERED_LIST].Update(nValue=0, sValue=text_delivered[:400])

        sent_lines = [self.format_line(e) for e in self._pending_entries(sender) + self._recent_delivered(sender)]
        text_out = "\n".join(sent_lines) or "Geen verzonden pakketten"
        Devices[UNIT_SENT].Update(nValue=0, sValue=text_out[:400])

        today = time.strftime("%Y-%m-%d")
        delivered_today = any(
            e["status"] == "Delivered" and e["deliveryDate"].startswith(today) for e in receiver
        )
        Devices[UNIT_DELIVERED_TODAY].Update(
            nValue=1 if delivered_today else 0, sValue="On" if delivered_today else "Off"
        )

    # --------------------------------------------------------------- run
    def run_update(self):
        self.debug("Start PostNL update")
        try:
            access_token = self.get_access_token()
            try:
                receiver, sender = self.fetch_shipments(access_token)
            except PostNLAuthError:
                self.debug("Access token verlopen tijdens ophalen, opnieuw inloggen.")
                access_token = self.get_access_token(force_full=True)
                receiver, sender = self.fetch_shipments(access_token)
            self.update_devices(receiver, sender)
            self.debug(
                "PostNL update geslaagd: {} inkomend, {} verzonden".format(len(receiver), len(sender))
            )
        except Exception as e:
            Domoticz.Error("PostNL update mislukt: {}".format(e))

    # --------------------------------------------------------- Domoticz hooks
    def onStart(self):
        self.debug_enabled = Parameters.get("Mode6") == "Debug"
        if self.debug_enabled:
            Domoticz.Debugging(1)

        if requests is None:
            Domoticz.Error(
                "De python module 'requests' ontbreekt. Installeer met: pip3 install requests"
            )
            self.available = False
            return

        self.username = Parameters.get("Username")
        self.password = Parameters.get("Password")

        try:
            self.poll_interval_minutes = int(Parameters.get("Mode1") or 60)
        except ValueError:
            self.poll_interval_minutes = 60
        if self.poll_interval_minutes < 15:
            self.poll_interval_minutes = 15

        try:
            self.delivered_days = int(Parameters.get("Mode2") or DEFAULT_DELIVERED_DAYS)
        except ValueError:
            self.delivered_days = DEFAULT_DELIVERED_DAYS
        if self.delivered_days < 1:
            self.delivered_days = 1

        if UNIT_COUNT not in Devices:
            Domoticz.Device(
                Name="Aantal onderweg", Unit=UNIT_COUNT, TypeName="Custom",
                Options={"Custom": "1;pakketten"}
            ).Create()
        if UNIT_INBOX not in Devices:
            Domoticz.Device(Name="Inkomende pakketten", Unit=UNIT_INBOX, TypeName="Text").Create()
        if UNIT_SENT not in Devices:
            Domoticz.Device(Name="Verzonden pakketten", Unit=UNIT_SENT, TypeName="Text").Create()
        if UNIT_DELIVERED_TODAY not in Devices:
            Domoticz.Device(Name="Vandaag bezorgd", Unit=UNIT_DELIVERED_TODAY, TypeName="Switch").Create()
        if UNIT_DELIVERED_LIST not in Devices:
            Domoticz.Device(Name="Geleverde pakketten", Unit=UNIT_DELIVERED_LIST, TypeName="Text").Create()

        self.load_refresh_token()

        Domoticz.Heartbeat(HEARTBEAT_SECONDS)
        self.ticks_needed = max(1, (self.poll_interval_minutes * 60) // HEARTBEAT_SECONDS)
        # trigger an update on the very first heartbeat
        self.tick_count = self.ticks_needed

    def onStop(self):
        pass

    def onConnect(self, Connection, Status, Description):
        pass

    def onMessage(self, Connection, Data):
        pass

    def onCommand(self, Unit, Command, Level, Color):
        if Unit == UNIT_DELIVERED_TODAY:
            Domoticz.Log("Dit apparaat is alleen-lezen; commando genegeerd.")
            cur = Devices[Unit]
            Devices[Unit].Update(nValue=cur.nValue, sValue=cur.sValue)

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        pass

    def onDisconnect(self, Connection):
        pass

    def onHeartbeat(self):
        if not self.available:
            return
        self.tick_count += 1
        if self.tick_count < self.ticks_needed:
            return
        self.tick_count = 0
        self.run_update()


global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)


def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)


def onCommand(Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Color)


def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)


def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()
