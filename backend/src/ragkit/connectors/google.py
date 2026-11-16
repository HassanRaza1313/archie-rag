"""Google Drive, with a service account.

Account: the service account's JSON key (create one in Google Cloud with the Drive API enabled). Share the folders,
or the shared drive, with the service account's email address, as you would with a colleague (Viewer is enough).

Location: a folder address (``https://drive.google.com/drive/folders/<id>``) or its id. Files of supported types
are read, subfolders included; Google Docs come as Markdown, Sheets as CSV (first sheet) and Slides as text. A
file's version number is its version, so only changed files are downloaded again.
"""

from __future__ import annotations

import base64
import json
import re
import time
from contextlib import closing
from functools import partial
from pathlib import PurePosixPath
from typing import Any

import httpx

from ragkit.config import FILE_TYPES
from ragkit.connectors import (
    ACCOUNT_KINDS,
    CONNECTORS,
    Account,
    AccountKind,
    Cache,
    Connector,
    ConnectorError,
    Item,
    http_client,
    request,
)

DRIVE = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER = "application/vnd.google-apps.folder"
EXPORTS = {  # Google's own formats: what to export them as, and the file type Archie then reads
    "application/vnd.google-apps.document": ("text/markdown", ".md"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}
MAX_FILE_BYTES = 200 * 1024 * 1024


def _key(account: Account) -> dict[str, Any]:
    try:
        key = json.loads(account.secret.get("service_account_json", ""))
    except ValueError as exc:
        raise ConnectorError("paste the service account's JSON key file") from exc
    if key.get("type") != "service_account" or not key.get("private_key") or not key.get("client_email"):
        raise ConnectorError("that isn't a service account key (it needs client_email and private_key)")
    return dict(key)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def signed_assertion(key: dict[str, Any], now: float) -> str:
    """The RS256-signed JWT a service account exchanges for an access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    header = {"alg": "RS256", "typ": "JWT", "kid": key.get("private_key_id", "")}
    claims = {"iss": key["client_email"], "scope": SCOPE, "aud": key.get("token_uri", "https://oauth2.googleapis.com/token"),
              "iat": int(now), "exp": int(now) + 3600}  # fmt: skip
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}".encode()
    private = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    if not isinstance(private, rsa.RSAPrivateKey):
        raise ConnectorError("the service account key isn't an RSA key")
    signature = private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode()}.{_b64(signature)}"


def _drive(account: Account) -> httpx.Client:
    key = _key(account)
    client = http_client()
    response = client.post(key.get("token_uri", "https://oauth2.googleapis.com/token"), data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": signed_assertion(key, time.time()),
    })  # fmt: skip
    if response.status_code != 200:
        client.close()
        raise ConnectorError(f"Google sign-in failed: {response.json().get('error_description', response.status_code)}")
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return client


def check(account: Account) -> str:
    with closing(_drive(account)) as client:
        data = request(client, "GET", f"{DRIVE}/files", "Google Drive", params={
            "pageSize": 1, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }).json()  # fmt: skip
    shared = "files shared with it" if data.get("files") else "nothing shared with it yet"
    return f"signed in as {_key(account)['client_email']}: {shared}"


def folder_id(location: str) -> str:
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", location) or re.fullmatch(r"([A-Za-z0-9_-]{10,})", location)
    if not match:
        raise ConnectorError("enter a Drive folder address, e.g. https://drive.google.com/drive/folders/1AbC...")
    return match.group(1)


def fetch(account: Account, location: str, cache: Cache, limit: int) -> list[Item]:
    root = folder_id(location)
    items: list[Item] = []
    fields = (
        "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink,version,lastModifyingUser(displayName))"
    )
    with closing(_drive(account)) as client:
        pending = [root]
        while pending and len(items) < limit:
            parent, token = pending.pop(), None
            while len(items) < limit:
                params = {"q": f"'{parent}' in parents and trashed = false", "fields": fields, "pageSize": 1000,
                          "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}  # fmt: skip
                if token:
                    params["pageToken"] = token
                data = request(client, "GET", f"{DRIVE}/files", "Google Drive", params=params).json()
                for entry in data.get("files", []):
                    if entry["mimeType"] == FOLDER:
                        pending.append(entry["id"])
                        continue
                    if item := _keep(client, cache, entry):
                        items.append(item)
                        if len(items) >= limit:
                            break
                token = data.get("nextPageToken")
                if not token:
                    break
    return items


def _content(client: httpx.Client, url: str, params: dict[str, str]) -> bytes:
    return request(client, "GET", url, "Google Drive", params=params).content


def _keep(client: httpx.Client, cache: Cache, entry: dict[str, Any]) -> Item | None:
    file_id, mime = entry["id"], entry["mimeType"]
    if mime in EXPORTS:
        export, suffix = EXPORTS[mime]
        url, params = f"{DRIVE}/files/{file_id}/export", {"mimeType": export}
    else:
        suffix = PurePosixPath(entry["name"]).suffix.lower()
        if suffix not in FILE_TYPES or int(entry.get("size") or 0) > MAX_FILE_BYTES:
            return None
        url, params = f"{DRIVE}/files/{file_id}", {"alt": "media", "supportsAllDrives": "true"}
    load = partial(_content, client, url, params)
    fields = {
        "modified": entry.get("modifiedTime"),
        "modified_by": (entry.get("lastModifyingUser") or {}).get("displayName"),
    }
    uri = entry.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}"
    return cache.keep(uri, str(entry.get("version") or entry.get("modifiedTime")), suffix, load,
                      {k: v for k, v in fields.items() if v})  # fmt: skip


ACCOUNT_KINDS["google"] = AccountKind(
    "Google Drive",
    settings=(),
    secrets=(("service_account_json", "Service account key (the JSON file's contents)"),),
    check=check,
)
CONNECTORS["google_drive"] = Connector("google", "A Drive folder address, shared with the service account", fetch)
