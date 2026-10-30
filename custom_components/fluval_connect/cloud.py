"""Bounded, owner-scoped account discovery. Passwords and tokens are transient."""
import json
import re
import urllib.error
import urllib.request

API = "https://apieu.fluvalaquatics.com"


class CannotConnect(Exception):
    """The service could not be reached securely."""


class InvalidAuth(Exception):
    """The account sign-in was rejected."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_json(path, *, body=None, token=None):
    if path not in ("/login?grant_type=app", "/api/cyddevice/all?sortType=1"):
        raise ValueError("Unsupported account request")
    headers = {"Accept": "application/json", "accept-language": "en"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise CannotConnect("Account response exceeded the size limit")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise CannotConnect("Unexpected account response")
            return value
    except urllib.error.HTTPError as err:
        if err.code in (400, 401, 403):
            raise InvalidAuth("The account sign-in was rejected") from None
        raise CannotConnect("Fluval is unavailable") from None
    except (urllib.error.URLError, ValueError, OSError):
        raise CannotConnect("Fluval could not be reached securely") from None


def discover_devices(email, password, request_fn=api_json):
    if not email.strip() or not password or len(email) > 320 or len(password) > 4096:
        raise InvalidAuth("Enter your Fluval account details")
    response = request_fn("/login?grant_type=app", body={
        "principal": "PASSWORD&" + email.strip(), "credentials": password, "companyId": "1",
    })
    data = response.get("data")
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise InvalidAuth("The account sign-in was rejected")
    del response, data, password
    try:
        response = request_fn("/api/cyddevice/all?sortType=1", token=token)
    finally:
        del token
    result = {}

    def visit(value, depth=0):
        if depth > 12:
            return
        if isinstance(value, list):
            for item in value[:1000]:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            mac = value.get("wifiMac", value.get("lightWifiMac", ""))
            name = value.get("name", value.get("lightName", value.get("deviceName", "")))
            product = value.get("productId", value.get("lightProductId"))
            if isinstance(mac, str) and isinstance(name, str):
                mac = re.sub(r"[:-]", "", mac).upper()
                supported = str(product) in ("532", "0532") or "aquasky3.0" in name.replace(" ", "").lower()
                if supported and re.fullmatch(r"[0-9A-F]{12}", mac):
                    result[mac] = {"wifiMac": mac, "name": name[:100], "product_id": 532}
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item, depth + 1)
    visit(response.get("data"))
    return list(result.values())


def validate_connection(instance_id, access_key, signing_key):
    instance_id, access_key, signing_key = instance_id.strip(), access_key.strip(), signing_key.strip()
    if not re.fullmatch(r"mqtt-[A-Za-z0-9-]{3,80}", instance_id):
        raise ValueError("Invalid broker instance ID")
    if not access_key or not signing_key or any(c in access_key + signing_key for c in "|\r\n"):
        raise ValueError("Invalid broker credentials")
    if max(len(access_key), len(signing_key)) > 512:
        raise ValueError("Invalid broker credential length")
    return {"instance_id": instance_id, "access_key": access_key, "signing_key": signing_key}
