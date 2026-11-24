"""
<plugin key="PostNL" name="PostNL &amp; DHL Package Tracking" author="patrick" version="1.1.1" externallink="https://github.com/MadPatrick/domoticz_postnl">
    <description>
        <h2>PostNL &amp; DHL Package Tracking v1.1.1</h2>
        <p>Shows incoming and outgoing PostNL and DHL shipments (Track &amp; Trace) from your PostNL and/or My DHL account.</p>
        <p>PostNL part ported from the Toon app <i>postnl</i> (ToonSoftwareCollective) to a Domoticz plugin.</p>
        <h3>Requirements</h3>
        <ul style="list-style-type:square">
            <li>A PostNL account (jouw.postnl.nl) and/or a My DHL account (my.dhlecommerce.nl). Fill in at least one of the two.</li>
            <li>Python module 'requests' (pip3 install requests)</li>
        </ul>
        <h3>Notes</h3>
        <ul style="list-style-type:square">
            <li>This plugin uses unofficial, reverse-engineered login flows. If PostNL or DHL change their login process, the plugin may stop working.</li>
            <li>Do not poll too often (15 minute minimum interval) to avoid your account being flagged.</li>
            <li>DHL: only DHL eCommerce (DHL Parcel NL) parcels that show up in your My DHL account are tracked, not DHL Express.</li>
        </ul>
    </description>
    <params>
        <param field="Username" label="PostNL e-mail address (empty = skip PostNL)" width="300px"/>
        <param field="Password" label="PostNL password" width="300px" password="true"/>
        <param field="DHLUsername" label="My DHL e-mail address (empty = skip DHL)" width="300px"/>
        <param field="DHLPassword" label="My DHL password" width="300px" password="true"/>
        <param field="PollMinutes" type="number" label="Poll interval (minutes)" min="15" max="720" step="15" default="60" width="100px"/>
        <param field="DeliveredDays" type="number" label="Show delivered for (days)" min="1" max="30" step="1" default="2" width="100px"/>
        <param field="Language" label="Device text language" width="150px">
            <options>
                <option label="English" value="en" default="true"/>
                <option label="Nederlands" value="nl"/>
            </options>
        </param>
        <param field="DeliveredEmptyText" label="Text shown when nothing was delivered" width="300px" default="Niets onderweg"/>
        <group label="Advanced">
            <param field="Debug" type="boolean" label="Debug" default="false"/>
        </group>
    </params>
</plugin>
"""
import base64
import datetime
import hashlib
import re
import secrets
import time
import traceback
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

# ---- DHL eCommerce NL (My DHL) consumer API, same endpoints as my.dhlecommerce.nl ----
DHL_LOGIN_URL = "https://my.dhlecommerce.nl/api/user/login"
DHL_PARCELS_URL = "https://my.dhlecommerce.nl/receiver-parcel-api/parcels"
DHL_SENT_URL = "https://my.dhlecommerce.nl/api/orders/sentShipments"
DHL_XSRF_COOKIE = "XSRF-TOKEN"
DHL_XSRF_HEADER = "x-xsrf-token"

# Granular DHL statuses that mean "ready to collect at a ServicePoint / locker".
DHL_PICKUP_STATUSES = {
    "NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT",
    "NOTIFICATION_FOR_PARCELSTATION_COLLECTION_HAS_BEEN_SENT",
    "AWAITING_RECEIVER_COLLECTION",
    "CLOSED_AWAITING_COLLECTION",
    "DELIVERED_AT_ACCESSPOINT",
    "DELIVERED_AT_PARCELSTATION",
    "DELIVERY_CODE_MISSING_PICK_UP",
    "ON_HOLD_FOR_COLLECTION",
    "PARCEL_FOUND_AT_PARCELSHOP",
    "PARCEL_HELD_FOR_COLLECTION_AT_LOCAL_DEPOT",
    "REMINDER_FOR_COLLECTION_SENT_EMAIL",
    "REMINDER_FOR_COLLECTION_SENT_LETTER",
    "REMINDER_FOR_COLLECTION_SENT_SMS",
}
# Granular DHL statuses that mean the parcel failed and goes back to the sender.
DHL_RETURN_STATUSES = {
    "ADDRESS_UNKNOWN",
    "CUSTOMS_DATA_INCORRECT_RETURN_TO_SHIPPER",
    "DAMAGE_RETURN",
    "DELIVERED_AT_SHIPPER",
    "DELIVERY_CODE_MISSING_RETURN",
    "DELIVERY_DATA_INCORRECT_RETURN",
    "INTERVENTION_RECEIVER_REQUEST_DELIVERY_CANCELLED",
    "INTERVENTION_SHIPPER_REQUEST_DELIVERY_CANCELLED",
    "MISROUTED_RETURN_TO_SHIPPER",
    "NOT_HOME_RETURN_TO_SHIPPER",
    "ON_ROUTE_TO_SHIPPER",
    "PARCEL_ALREADY_RETURNED",
    "PARCEL_RELABELED_FOR_RETURN_TO_SHIPPER",
    "PARCEL_SCANNED_AT_RETURN_HUB",
    "POSTAL_CODE_INCORRECT",
    "RECEIVER_RETURN",
    "RECEIVER_UNKNOWN_RETURN",
    "REFUSED_AT_PARCELSHOP",
    "REFUSED_BY_RECEIVER",
    "REFUSED_NOT_COLLECTED",
    "REFUSED_RETURN",
    "RETURNED_NOT_COLLECTED",
    "RETURNED_TO_SHIPPER",
    "STORAGE_PERIOD_ENDED_AT_ACCESSPOINT",
    "STORAGE_PERIOD_ENDED_AT_PARCELSHOP",
    "STORAGE_PERIOD_ENDED_AT_PARCELSTATION",
}
DHL_PROBLEM_CATEGORIES = {"INTERVENTION", "EXCEPTION", "PROBLEM"}

DEFAULT_LANGUAGE = "en"
TRANSLATIONS = {
    "en": {
        "status": {
            "Open": "In transit",
            "InTransit": "In transit",
            "Delivered": "Delivered",
            "ReturnToSender": "Returned to sender",
            "AtPickupPoint": "Ready for pickup",
            "Problem": "Delayed",
        },
        "unknown_sender": "Unknown",
        "no_pending": "Nothing on the way",
        "no_sent": "No shipments sent",
    },
    "nl": {
        "status": {
            "Open": "Onderweg",
            "InTransit": "Onderweg",
            "Delivered": "Bezorgd",
            "ReturnToSender": "Retour afzender",
            "AtPickupPoint": "Klaar om op te halen",
            "Problem": "Vertraagd",
        },
        "unknown_sender": "Onbekend",
        "no_pending": "Geen pakketten onderweg",
        "no_sent": "Geen verzonden pakketten",
    },
}

UNIT_COUNT = 1
UNIT_INBOX = 2
UNIT_SENT = 3
UNIT_DELIVERED_TODAY = 4
UNIT_DELIVERED_LIST = 5

HEARTBEAT_SECONDS = 30
DEFAULT_DELIVERED_DAYS = 2
DEFAULT_DELIVERED_EMPTY_TEXT = "Niets onderweg"
MAX_TEXT_LINES = 10


class PostNLError(Exception):
    pass


class PostNLAuthError(PostNLError):
    pass


class PostNLClient:
    """Talks to PostNL's (unofficial) account API: login, session refresh and shipment
    lookup. Has no Domoticz dependency, so it can be exercised or unit-tested on its own
    (e.g. with requests-mock) independent of a running Domoticz instance."""

    def __init__(self, username, password, refresh_token=None, log_debug=None):
        self.username = username
        self.password = password
        self.refresh_token = refresh_token
        self._log_debug = log_debug or (lambda msg: None)

    def _debug(self, msg):
        self._log_debug(msg)

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
            raise PostNLError("No hosted-login redirect received from authorize.")

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
            raise PostNLError("Could not read login widget settings.")
        self._debug("capture appId={} client={} flow={}".format(app_id, client_id, flow_name))

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
            raise PostNLError("PostNL rejected the username/password.")
        cap_token = self._first_match(r'"accessToken":"([^"]*)"', r3b.text)
        if not cap_token:
            raise PostNLError("No capture access token received (login failed).")

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
            raise PostNLError("No authorization code received after login.")

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
            raise PostNLError("Token exchange failed (status {}).".format(r5.status_code))
        tokens = r5.json()
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token:
            raise PostNLError("No access token received from token endpoint.")
        if refresh_token:
            self.refresh_token = refresh_token
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
            self._debug("Refresh token rejected (status {}).".format(r.status_code))
            return None
        tokens = r.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return None
        new_rt = tokens.get("refresh_token")
        if new_rt:
            self.refresh_token = new_rt
        return access_token

    def get_access_token(self, force_full=False):
        if not force_full:
            token = self.refresh_access_token()
            if token:
                self._debug("Reused login via refresh token.")
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
            self._debug("Track & trace lookup failed for {}: {}".format(barcode, e))
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
        rcpt_company = ""
        rcpt_person = ""
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
            recipient_block = colli.get("recipient") or {}
            recipient_addr = recipient_block.get("address") or {}
            rcpt_company = recipient_block.get("companyName") or ""
            rcpt_person = recipient_block.get("personName") or ""
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

        # Who to show depends on role: for something arriving TO you, that's the sender;
        # for something YOU are sending, restating your own shipment title is not useful,
        # so identify it by the recipient instead.
        if role == "receiver":
            who = sender_company or sender_last or sender_town or ""
        else:
            who = rcpt_company or rcpt_person or rcpt_town or sender_company or ""

        return {
            "carrier": "PostNL",
            "role": role,
            "barcode": barcode,
            "shipmentType": stype,
            "title": title,
            "status": status,
            "deliveryDate": deldate,
            "from": tf_from,
            "to": tf_to,
            "who": who,
            "senderCompany": sender_company,
            "senderLast": sender_last,
            "senderTown": sender_town,
            "recipientCompany": rcpt_company,
            "recipientPerson": rcpt_person,
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
            raise PostNLError("Fetching shipments failed (status {}).".format(r.status_code))
        payload = r.json()
        shipments = (payload.get("data") or {}).get("trackedShipments") or {}
        receiver_raw = shipments.get("receiverShipments") or []
        sender_raw = shipments.get("senderShipments") or []

        receiver = [self.build_entry(i, "receiver", access_token) for i in receiver_raw]
        sender = [self.build_entry(i, "sender", access_token) for i in sender_raw]
        return receiver, sender


class DHLError(Exception):
    pass


class DHLAuthError(DHLError):
    pass


class DHLClient:
    """Talks to the (unofficial) consumer API behind my.dhlecommerce.nl: a plain JSON
    login that sets session cookies, then the incoming-parcels and sent-shipments
    lists. Only covers DHL eCommerce NL (DHL Parcel), not DHL Express. Like
    PostNLClient it has no Domoticz dependency."""

    def __init__(self, username, password, log_debug=None):
        self.username = username
        self.password = password
        self._log_debug = log_debug or (lambda msg: None)
        self.session = None

    def _debug(self, msg):
        self._log_debug(msg)

    # ------------------------------------------------------------- login
    def login(self):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        r = session.post(
            DHL_LOGIN_URL,
            json={"email": self.username, "password": self.password},
            timeout=20,
        )
        if r.status_code in (401, 403):
            raise DHLError("DHL rejected the username/password (status {}).".format(r.status_code))
        if r.status_code != 200:
            raise DHLError("DHL login failed (status {}).".format(r.status_code))
        self.session = session
        self._debug("DHL login succeeded.")

    def _get_json(self, url, params=None):
        headers = {}
        for c in self.session.cookies:
            if c.name == DHL_XSRF_COOKIE:
                headers[DHL_XSRF_HEADER] = c.value
        r = self.session.get(url, params=params, headers=headers, timeout=20)
        if r.status_code in (401, 403):
            raise DHLAuthError("Unauthorized")
        if r.status_code != 200:
            raise DHLError("DHL request failed (status {}).".format(r.status_code))
        return r.json()

    def _get_with_relogin(self, url, params=None):
        if self.session is None:
            self.login()
        try:
            return self._get_json(url, params)
        except DHLAuthError:
            self._debug("DHL session expired, logging in again.")
            self.login()
            return self._get_json(url, params)

    # ------------------------------------------------------------- mapping
    @staticmethod
    def _to_local_iso(ts):
        """DHL timestamps are UTC ('...Z'); convert to local time so the time shown on the
        device and the date comparisons match the PostNL entries."""
        if not ts:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return str(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S")

    @classmethod
    def _window(cls, item):
        ind = item.get("receivingTimeIndication") or {}
        itype = ind.get("indicationType")
        if itype == "MomentIndication":
            return cls._to_local_iso(ind.get("moment")), ""
        if itype == "IntervalIndication":
            return cls._to_local_iso(ind.get("start")), cls._to_local_iso(ind.get("end"))
        return "", ""

    @staticmethod
    def _map_status(item, window_from):
        raw_status = item.get("status") or ""
        category = item.get("category") or ""
        if category == "DELIVERED":
            return "Delivered"
        if raw_status in DHL_RETURN_STATUSES:
            return "ReturnToSender"
        if raw_status in DHL_PICKUP_STATUSES:
            return "AtPickupPoint"
        if category in DHL_PROBLEM_CATEGORIES and not raw_status.startswith(
            "INTERVENTION_RECEIVER_REQUESTS_DELIVERY"
        ):
            return "Problem"
        return "Open" if window_from else "InTransit"

    @staticmethod
    def _party_name(block):
        block = block or {}
        return block.get("name") or (block.get("address") or {}).get("city") or ""

    def build_entry(self, item, role):
        w_from, w_to = self._window(item)
        status = self._map_status(item, w_from)
        destination = item.get("destination") or {}
        pickup = destination.get("name") if destination.get("locationType") == "SERVICEPOINT" else ""
        if role == "receiver":
            who = self._party_name(item.get("sender"))
        else:
            who = self._party_name(item.get("receiver")) or self._party_name(item.get("sender"))
        delivered = status == "Delivered"
        return {
            "carrier": "DHL",
            "role": role,
            "barcode": item.get("barcode") or "",
            "shipmentType": item.get("type") or "",
            "title": "",
            "status": status,
            "deliveryDate": w_from if delivered else "",
            "from": "" if delivered else w_from,
            "to": "" if delivered else w_to,
            "who": who,
            "pickupLocation": pickup or "",
        }

    # ------------------------------------------------------------- data
    def fetch_shipments(self):
        parcels_payload = self._get_with_relogin(DHL_PARCELS_URL)
        parcels = (parcels_payload or {}).get("parcels") or []
        try:
            sent_payload = self._get_with_relogin(DHL_SENT_URL, params={"max": 250})
        except Exception as e:
            # sent shipments are a nice-to-have; never lose the incoming list over them
            self._debug("DHL sent shipments lookup failed: {}".format(e))
            sent_payload = []
        sent = [s for s in (sent_payload if isinstance(sent_payload, list) else [])
                if (s.get("type") or "outgoing") == "outgoing"]

        # The same parcel can come back more than once: twice in the parcels list, or a
        # return label both as an isReturn parcel and as a sent shipment. Keep each parcel
        # once (first one wins, the parcels list has the richer status).
        seen = set()

        def unique(items):
            out = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                key = self._parcel_key(it)
                if key:
                    if key in seen:
                        self._debug("DHL: skipping duplicate parcel {}".format(key))
                        continue
                    seen.add(key)
                out.append(it)
            return out

        incoming = unique([p for p in parcels if isinstance(p, dict) and not p.get("isReturn")])
        # A return you ship back (webshop return label) is listed among the incoming parcels
        # with isReturn=true; for this plugin it is something you send, so it goes to "Sent".
        returns = unique([p for p in parcels if isinstance(p, dict) and p.get("isReturn")])
        sent = unique(sent)

        receiver = [self.build_entry(p, "receiver") for p in incoming]
        sender = [self.build_entry(p, "sender") for p in returns + sent]
        return receiver, sender

    @staticmethod
    def _parcel_key(item):
        """Identity of a DHL parcel: its barcode (Track & Trace code), else its id."""
        for field in ("barcode", "parcelId", "id"):
            value = item.get(field)
            if value:
                return str(value).strip().upper()
        return ""


class BasePlugin:
    def __init__(self):
        self.client = None
        self.dhl_client = None
        # last successful result per carrier: {"PostNL": (receiver, sender), "DHL": (...)}
        # so a failing carrier keeps its last known packages instead of emptying the devices
        self.results = {}
        self.debug_enabled = False
        self.available = True
        self.poll_interval_minutes = 60
        self.delivered_days = DEFAULT_DELIVERED_DAYS
        self.delivered_empty_text = DEFAULT_DELIVERED_EMPTY_TEXT
        self.i18n = TRANSLATIONS[DEFAULT_LANGUAGE]
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
        return (cfg or {}).get("refresh_token")

    def save_refresh_token(self, refresh_token):
        try:
            cfg = Domoticz.Configuration() or {}
        except Exception:
            cfg = {}
        if refresh_token:
            cfg["refresh_token"] = refresh_token
        else:
            cfg.pop("refresh_token", None)
        try:
            Domoticz.Configuration(cfg)
        except Exception as e:
            Domoticz.Error("Failed to save refresh token: {}".format(e))

    @staticmethod
    def _fmt_time(iso_ts):
        m = re.search(r"T(\d{2}:\d{2})", iso_ts or "")
        return m.group(1) if m else ""

    @staticmethod
    def _fmt_short_date(iso_ts):
        m = re.search(r"\d{4}-(\d{2})-(\d{2})", iso_ts or "")
        return "{}/{}".format(m.group(2), m.group(1)) if m else ""

    # ------------------------------------------------------------- devices
    def format_line(self, e):
        who = e["who"] or self.i18n["unknown_sender"]
        if self.client is not None and self.dhl_client is not None:
            # both carriers active: tell them apart on the device
            who = "{} ({})".format(who, e.get("carrier") or "?")
        status_label = self.i18n["status"].get(e["status"], e["status"])

        date = ""
        time_part = ""
        if e["status"] == "Delivered" and e["deliveryDate"]:
            date = self._fmt_short_date(e["deliveryDate"])
            time_part = self._fmt_time(e["deliveryDate"])
        elif e["from"]:
            date = self._fmt_short_date(e["from"])
            t_from = self._fmt_time(e["from"])
            t_to = self._fmt_time(e["to"])
            time_part = "{}-{}".format(t_from, t_to) if t_from and t_to else (t_from or t_to)

        prefix = "[{}] ".format(date) if date else ""
        suffix = " {}".format(time_part) if time_part else ""
        return "{}{}: {}{}".format(prefix, who, status_label, suffix)

    def _pending_entries(self, entries):
        """Not yet delivered (and not returned), sorted by expected date/time."""
        pending = [e for e in entries if e["status"] not in ("Delivered", "ReturnToSender")]
        pending.sort(key=lambda e: e["from"] or "")
        return pending[:MAX_TEXT_LINES]

    def _recent_delivered(self, entries):
        """Delivered within the last self.delivered_days days, newest first."""
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
        text_pending = "\n".join(pending_lines) or self.i18n["no_pending"]
        Devices[UNIT_INBOX].Update(nValue=0, sValue=text_pending[:400])

        delivered_lines = [self.format_line(e) for e in self._recent_delivered(receiver)]
        text_delivered = "\n".join(delivered_lines) or self.delivered_empty_text
        Devices[UNIT_DELIVERED_LIST].Update(nValue=0, sValue=text_delivered[:400])

        sent_entries = (self._pending_entries(sender) + self._recent_delivered(sender))[:MAX_TEXT_LINES]
        sent_lines = [self.format_line(e) for e in sent_entries]
        text_out = "\n".join(sent_lines) or self.i18n["no_sent"]
        Devices[UNIT_SENT].Update(nValue=0, sValue=text_out[:400])

        today = time.strftime("%Y-%m-%d")
        delivered_today = any(
            e["status"] == "Delivered" and e["deliveryDate"].startswith(today) for e in receiver
        )
        Devices[UNIT_DELIVERED_TODAY].Update(
            nValue=1 if delivered_today else 0, sValue="On" if delivered_today else "Off"
        )

    # --------------------------------------------------------------- run
    def _report_error(self, carrier, e):
        if self.debug_enabled:
            Domoticz.Error("{} update failed: {}\n{}".format(carrier, e, traceback.format_exc()))
        else:
            Domoticz.Error("{} update failed: {}".format(carrier, e))

    def update_postnl(self):
        self.debug("Starting PostNL update")
        access_token = self.client.get_access_token()
        self.save_refresh_token(self.client.refresh_token)
        try:
            receiver, sender = self.client.fetch_shipments(access_token)
        except PostNLAuthError:
            self.debug("Access token expired while fetching, logging in again.")
            access_token = self.client.get_access_token(force_full=True)
            self.save_refresh_token(self.client.refresh_token)
            receiver, sender = self.client.fetch_shipments(access_token)
        self.debug(
            "PostNL update succeeded: {} incoming, {} sent".format(len(receiver), len(sender))
        )
        return receiver, sender

    def update_dhl(self):
        self.debug("Starting DHL update")
        receiver, sender = self.dhl_client.fetch_shipments()
        self.debug(
            "DHL update succeeded: {} incoming, {} sent".format(len(receiver), len(sender))
        )
        return receiver, sender

    def run_update(self):
        carriers = []
        if self.client is not None:
            carriers.append(("PostNL", self.update_postnl))
        if self.dhl_client is not None:
            carriers.append(("DHL", self.update_dhl))

        any_ok = False
        for name, fetch in carriers:
            try:
                self.results[name] = fetch()
                any_ok = True
            except Exception as e:
                self._report_error(name, e)

        if not any_ok:
            # nothing new: leave the devices as they are
            return
        receiver = []
        sender = []
        for name, _ in carriers:
            r, s = self.results.get(name, ([], []))
            receiver += r
            sender += s
        try:
            self.update_devices(receiver, sender)
        except Exception as e:
            self._report_error("Device", e)

    # --------------------------------------------------------- Domoticz hooks
    def onStart(self):
        self.debug_enabled = str(Parameters.get("Debug", "false")).strip().lower() == "true"
        if self.debug_enabled:
            Domoticz.Debugging(1)

        if requests is None:
            Domoticz.Error(
                "The python module 'requests' is missing. Install it with: pip3 install requests"
            )
            self.available = False
            return

        try:
            self.poll_interval_minutes = int(Parameters.get("PollMinutes") or 60)
        except ValueError:
            self.poll_interval_minutes = 60
        if self.poll_interval_minutes < 15:
            self.poll_interval_minutes = 15

        try:
            self.delivered_days = int(Parameters.get("DeliveredDays") or DEFAULT_DELIVERED_DAYS)
        except ValueError:
            self.delivered_days = DEFAULT_DELIVERED_DAYS
        if self.delivered_days < 1:
            self.delivered_days = 1

        language = str(Parameters.get("Language") or DEFAULT_LANGUAGE).strip().lower()
        self.i18n = TRANSLATIONS.get(language, TRANSLATIONS[DEFAULT_LANGUAGE])

        self.delivered_empty_text = Parameters.get("DeliveredEmptyText") or DEFAULT_DELIVERED_EMPTY_TEXT

        if UNIT_COUNT not in Devices:
            Domoticz.Device(
                Name="Packages in transit", Unit=UNIT_COUNT, TypeName="Custom",
                Options={"Custom": "1;packages"}
            ).Create()
        if UNIT_INBOX not in Devices:
            Domoticz.Device(Name="Packages Incoming", Unit=UNIT_INBOX, TypeName="Text").Create()
        if UNIT_SENT not in Devices:
            Domoticz.Device(Name="Packages Sent", Unit=UNIT_SENT, TypeName="Text").Create()
        if UNIT_DELIVERED_TODAY not in Devices:
            Domoticz.Device(Name="Delivered Today", Unit=UNIT_DELIVERED_TODAY, TypeName="Switch").Create()
        if UNIT_DELIVERED_LIST not in Devices:
            Domoticz.Device(Name="Packages Delivered", Unit=UNIT_DELIVERED_LIST, TypeName="Text").Create()

        postnl_user = (Parameters.get("Username") or "").strip()
        postnl_pass = Parameters.get("Password") or ""
        dhl_user = (Parameters.get("DHLUsername") or "").strip()
        dhl_pass = Parameters.get("DHLPassword") or ""

        self.client = None
        self.dhl_client = None
        if postnl_user and postnl_pass:
            self.client = PostNLClient(
                username=postnl_user,
                password=postnl_pass,
                refresh_token=self.load_refresh_token(),
                log_debug=self.debug,
            )
        elif postnl_user or postnl_pass:
            Domoticz.Error("PostNL e-mail address or password is empty; PostNL is skipped.")
        if dhl_user and dhl_pass:
            self.dhl_client = DHLClient(username=dhl_user, password=dhl_pass, log_debug=self.debug)
        elif dhl_user or dhl_pass:
            Domoticz.Error("My DHL e-mail address or password is empty; DHL is skipped.")

        if self.client is None and self.dhl_client is None:
            Domoticz.Error("No PostNL or DHL account configured. Fill in at least one in the hardware settings.")
            self.available = False
            return
        Domoticz.Log("Tracking: {}".format(", ".join(
            name for name, c in (("PostNL", self.client), ("DHL", self.dhl_client)) if c is not None
        )))

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
            Domoticz.Log("This device is read-only; command ignored.")
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
