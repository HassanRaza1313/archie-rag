"""SharePoint and OneDrive, through Microsoft Graph.

Account: an app registration in Microsoft Entra ID (tenant ID, client ID and a client secret) with the application
permissions ``Sites.Read.All`` and ``Files.Read.All`` (or ``Sites.Selected`` for chosen sites), admin-consented.

Location: a SharePoint folder as it appears in the browser
(``https://contoso.sharepoint.com/sites/HR/Shared Documents/Policies``), a whole library or site, or a person's
OneDrive as ``person@contoso.com`` or ``person@contoso.com/Documents/Projects``. Files of supported types are read,
subfolders included; a file's cTag is its version, so only changed files are downloaded again.
"""

from __future__ import annotations

from contextlib import closing
from functools import partial
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

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

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
MAX_FILE_BYTES = 200 * 1024 * 1024


def _token(account: Account, client: httpx.Client) -> str:
    tenant = account.settings.get("tenant_id", "").strip()
    client_id = account.settings.get("client_id", "").strip()
    if not tenant or not client_id or not account.secret.get("client_secret"):
        raise ConnectorError("enter the tenant ID, client ID and client secret of the app registration")
    response = client.post(f"{LOGIN}/{quote(tenant)}/oauth2/v2.0/token", data={
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": account.secret["client_secret"],
        "scope": "https://graph.microsoft.com/.default",
    })  # fmt: skip
    if response.status_code != 200:
        detail = response.json().get("error_description", "") if response.content else ""
        raise ConnectorError(f"Microsoft sign-in failed: {detail.splitlines()[0] if detail else response.status_code}")
    return str(response.json()["access_token"])


def _graph(account: Account) -> httpx.Client:
    client = http_client()
    client.headers["Authorization"] = f"Bearer {_token(account, client)}"
    return client


def check(account: Account) -> str:
    with closing(_graph(account)) as client:
        site = request(client, "GET", f"{GRAPH}/sites/root", "Microsoft Graph").json()
    return f"connected to {site.get('displayName') or site.get('webUrl', 'SharePoint')}"


def resolve(client: httpx.Client, location: str) -> tuple[str, str]:
    """(drive id, folder path inside the drive) for a SharePoint URL or an OneDrive owner."""
    if location.startswith(("https://", "http://")):
        parts = urlsplit(location)
        segments = [unquote(s) for s in parts.path.strip("/").split("/") if s]
        if len(segments) < 2 or segments[0] not in ("sites", "teams"):
            raise ConnectorError(
                "use a SharePoint address like https://contoso.sharepoint.com/sites/HR/Shared Documents"
            )
        site_path = "/".join(segments[:2])
        site = request(client, "GET", f"{GRAPH}/sites/{parts.netloc}:/{quote(site_path)}", "SharePoint").json()
        drives = request(client, "GET", f"{GRAPH}/sites/{site['id']}/drives", "SharePoint").json().get("value", [])
        wanted = unquote(location).rstrip("/")
        # The library is the drive whose address starts the location; with none named, the site's default library.
        match = max((d for d in drives if wanted.startswith(unquote(d["webUrl"]).rstrip("/"))),
                    key=lambda d: len(d["webUrl"]), default=None)  # fmt: skip
        if match is None:
            drive = request(client, "GET", f"{GRAPH}/sites/{site['id']}/drive", "SharePoint").json()
            return drive["id"], ""
        return match["id"], wanted[len(unquote(match["webUrl"]).rstrip("/")) :].strip("/")
    owner, _, folder = location.partition("/")
    if "@" not in owner:
        raise ConnectorError("enter a SharePoint folder address, or a OneDrive as person@company.com/Folder")
    drive = request(client, "GET", f"{GRAPH}/users/{quote(owner)}/drive", "OneDrive").json()
    return drive["id"], folder.removeprefix("Documents").strip("/")


def fetch(account: Account, location: str, cache: Cache, limit: int) -> list[Item]:
    if not location:
        raise ConnectorError("enter a SharePoint folder address or person@company.com for a OneDrive")
    items: list[Item] = []
    with closing(_graph(account)) as client, http_client() as downloads:
        drive, folder = resolve(client, location)
        root = f"{GRAPH}/drives/{drive}/root" + (f":/{quote(folder)}:" if folder else "")
        pending: list[str] = [f"{root}/children?$top=200"]
        while pending and len(items) < limit:
            url: str | None = pending.pop()
            while url and len(items) < limit:
                data = request(client, "GET", url, "Microsoft Graph").json()
                for entry in data.get("value", []):
                    if "folder" in entry:
                        pending.append(f"{GRAPH}/drives/{drive}/items/{entry['id']}/children?$top=200")
                        continue
                    suffix = PurePosixPath(entry.get("name", "")).suffix.lower()
                    link = entry.get("@microsoft.graph.downloadUrl")
                    if suffix not in FILE_TYPES or not link or entry.get("size", 0) > MAX_FILE_BYTES:
                        continue
                    fields = _fields(entry)
                    version = entry.get("cTag") or entry.get("eTag", "")
                    load = partial(_download, downloads, link)
                    items.append(cache.keep(entry.get("webUrl", entry["id"]), version, suffix, load, fields))
                    if len(items) >= limit:
                        break
                url = data.get("@odata.nextLink")
    return items


def _download(client: httpx.Client, url: str) -> bytes:
    # The download address is pre-authorised and short-lived: no bearer token is sent to it.
    return request(client, "GET", url, "SharePoint").content


def _fields(entry: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "modified": entry.get("lastModifiedDateTime"),
        "modified_by": (entry.get("lastModifiedBy") or {}).get("user", {}).get("displayName"),
        "folder": (entry.get("parentReference") or {}).get("path", "").split("root:", 1)[-1] or "/",
    }
    return {key: value for key, value in fields.items() if value}


ACCOUNT_KINDS["microsoft"] = AccountKind(
    "Microsoft 365 (SharePoint and OneDrive)",
    settings=(("tenant_id", "Directory (tenant) ID"), ("client_id", "Application (client) ID")),
    secrets=(("client_secret", "Client secret"),),
    check=check,
)
CONNECTORS["sharepoint"] = Connector(
    "microsoft", "A SharePoint folder address, or person@company.com/Folder for a OneDrive", fetch
)
