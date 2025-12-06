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
    """Build SHA-512 HMAC signature from params. Requires 'Timestamp' in params."""
    params_to_sign = OrderedDict(sorted(list(params.items())))
    encoded_params = urlencode(params_to_sign).encode("utf-8")
    encoded_private_key = FUTUUR_PRIVATE_KEY.encode("utf-8")
    return {
        "hmac": hmac.new(encoded_private_key, encoded_params, hashlib.sha512).hexdigest(),
        "Timestamp": params["Timestamp"],
    }


def build_headers(params: Dict[str, Any]) -> Dict[str, str]:
    sig = build_signature(params)
    return {
        "Key": FUTUUR_PUBLIC_KEY,
        "Timestamp": str(sig["Timestamp"]),
        "HMAC": sig["hmac"],
    }


def call_api(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
    auth: bool = True,
) -> Json:
    """
    Low-level helper around Futuur public API.

    - endpoint: e.g. "markets/" or "bets/" (with or without leading slash).
    - params: query parameters.
    - payload: JSON body for POST/PUT/PATCH.
    - auth: if True, send Key/Timestamp/HMAC headers.
    """
    method = method.upper()
    base_url = FUTUUR_BASE_URL.rstrip("/") + "/"
    endpoint = endpoint.lstrip("/")
    url = base_url + endpoint

    params = dict(params or {})
    payload = dict(payload or {})

    if auth:
        # Build the set of parameters to sign (either query or payload).
        sign_params: Dict[str, Any] = dict(params or payload)
        now_ts = int(datetime.datetime.utcnow().timestamp())
        sign_params.setdefault("Key", FUTUUR_PUBLIC_KEY)
        sign_params.setdefault("Timestamp", now_ts)

        # Apply Key/Timestamp back to params/payload depending on method.
        if method == "GET":
            params.setdefault("Key", sign_params["Key"])
            params.setdefault("Timestamp", sign_params["Timestamp"])
        else:
            payload.setdefault("Key", sign_params["Key"])
            payload.setdefault("Timestamp", sign_params["Timestamp"])

        headers = build_headers(sign_params)
    else:
        headers = {}

    url_params = "?" + urlencode(params) if params else ""
    full_url = url + url_params

    request_kwargs: Dict[str, Any] = {
        "method": method,
        "url": full_url,
        "headers": headers,
    }
    if method in {"POST", "PUT", "PATCH"} and payload:
        request_kwargs["json"] = payload

    resp = requests.request(**request_kwargs)
    resp.raise_for_status()

    text = resp.text.strip()
    if not text:
        return {}
    return resp.json()
