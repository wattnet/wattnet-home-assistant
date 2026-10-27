#!/usr/bin/env python3
"""Onboard the dev Home Assistant so it is ready to use.

Creates the user ``dev`` / ``dev`` (or logs in if it already exists) and
finishes the onboarding wizard. The Wattnet integration is then added from the
UI with your own credentials. Standard library only.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8123"
CLIENT_ID = f"{BASE}/"
USER = PASSWORD = "dev"


def call(method, path, body=None, token=None, form=False):
    data = None
    headers = {}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        raise SystemExit(
            f"{method} {path} -> {err.code}: {err.read().decode()}"
        ) from err


def wait_for_ha():
    print("Waiting for Home Assistant", end="", flush=True)
    for _ in range(120):
        try:
            urllib.request.urlopen(BASE + "/api/onboarding", timeout=5).read()
            print(" up")
            return
        except Exception:
            print(".", end="", flush=True)
            time.sleep(3)
    raise SystemExit("\nHome Assistant did not start in time")


def exchange(code):
    return call(
        "POST",
        "/auth/token",
        {"grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID},
        form=True,
    )["access_token"]


def login():
    flow = call(
        "POST",
        "/auth/login_flow",
        {
            "client_id": CLIENT_ID,
            "handler": ["homeassistant", None],
            "redirect_uri": CLIENT_ID,
        },
    )
    result = call(
        "POST",
        f"/auth/login_flow/{flow['flow_id']}",
        {"username": USER, "password": PASSWORD, "client_id": CLIENT_ID},
    )
    return exchange(result["result"])


def get_token():
    steps = call("GET", "/api/onboarding")
    pending = {s["step"] for s in steps if not s["done"]}
    if "user" in pending:
        result = call(
            "POST",
            "/api/onboarding/users",
            {
                "client_id": CLIENT_ID,
                "name": "Dev",
                "username": USER,
                "password": PASSWORD,
                "language": "en",
            },
        )
        token = exchange(result["auth_code"])
        print(f"Created user {USER} / {PASSWORD}")
    else:
        token = login()
    for step in ("core_config", "analytics"):
        if step in pending:
            call("POST", f"/api/onboarding/{step}", {}, token)
    if "integration" in pending:
        call(
            "POST",
            "/api/onboarding/integration",
            {"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID},
            token,
        )
    return token


if __name__ == "__main__":
    wait_for_ha()
    get_token()
    print(f"\nReady at {BASE}  (login: {USER} / {PASSWORD})")
    print(
        "Add the integration: Settings > Devices & services > Add integration > Wattnet"
    )
