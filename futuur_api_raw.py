import datetime
import hashlib
import hmac
from collections import OrderedDict
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

import requests

from config import FUTUUR_BASE_URL, FUTUUR_PUBLIC_KEY, FUTUUR_PRIVATE_KEY

Json = Union[Dict[str, Any], list]


def build_signature(params: Dict[str, Any]) -> Dict[str, Any]:
    params_to_sign = OrderedDict(sorted(list(params.items())))
    encoded_params = urlencode(params_to_sign).encode("utf-8")
    encoded_private_key = FUTUUR_PRIVATE_KEY.encode("utf-8")
    return {
        "hmac": hmac.new(encoded_private_key, encoded_params, hashlib.sha512).hexdigest(),
        "Timestamp": params["Timestamp"],
    }


def build_headers(params: Dict[str, Any]) -> Dict[str, str]:
    sig = build_signature(params)
    return {"Key": FUTUUR_PUBLIC_KEY, "Timestamp": str(sig["Timestamp"]), "HMAC": sig["hmac"]}


def call_api(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
    auth: bool = True,
    timeout: int = 30,
) -> Json:
    method = method.upper()
    base_url = FUTUUR_BASE_URL.rstrip("/") + "/"
    endpoint = endpoint.lstrip("/")
    url = base_url + endpoint

    params = dict(params or {})
    payload = dict(payload or {})
    headers: Dict[str, str] = {}

    if auth:
        now_ts = int(datetime.datetime.utcnow().timestamp())

        # Signing rule:
        # - GET: sign query params (+Key/+Timestamp), include in query
        # - POST/PUT/PATCH: sign payload (+Key/+Timestamp), include in JSON
        if method == "GET":
            sign_params = dict(params)
            sign_params.setdefault("Key", FUTUUR_PUBLIC_KEY)
            sign_params.setdefault("Timestamp", now_ts)

            params.setdefault("Key", sign_params["Key"])
            params.setdefault("Timestamp", sign_params["Timestamp"])
        else:
            sign_params = dict(payload)
            sign_params.setdefault("Key", FUTUUR_PUBLIC_KEY)
            sign_params.setdefault("Timestamp", now_ts)

            payload.setdefault("Key", sign_params["Key"])
            payload.setdefault("Timestamp", sign_params["Timestamp"])

        headers = build_headers(sign_params)

    resp = requests.request(
        method=method,
        url=url,
        params=params if params else None,
        json=payload if method in {"POST", "PUT", "PATCH"} and payload else None,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    if not resp.text.strip():
        return {}
    return resp.json()
