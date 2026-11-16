"""Connectors: read documents from services companies already use, into a local cache that builds index.

A connector lists the items at a ``location`` in an account (a bucket prefix, a SQL query, a Confluence space,
a Jira query, a SharePoint folder, a Drive folder, a Notion page) and saves each one as a file. A manifest keeps
each item's version (ETag, modified time, row hash), so a sync downloads only what changed; items that are gone
are removed. Builds then read the cache like a folder, with the item's address as its stable ``uri``.

Accounts hold the credentials: owners add them in Preferences, where they are checked live and encrypted at rest
(``connections.encrypt``); a chatbot's settings only name the account, so secrets never appear in its config.
Content from connectors is untrusted: passages with wording aimed at an AI are screened like web pages.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


class ConnectorError(ValueError):
    """The account or location can't be read; the message is safe to show."""


@dataclass(frozen=True)
class Account:
    kind: str
    settings: dict[str, str]  # not secret: addresses, regions, ids
    secret: dict[str, str]  # decrypted for this run only


@dataclass
class Item:
    """One document read from a service."""

    uri: str  # stable address: shown in citations, keys incremental builds
    path: Path
    fields: dict[str, Any] = field(default_factory=dict)  # structured data kept on every passage


@dataclass(frozen=True)
class AccountKind:
    label: str
    settings: tuple[tuple[str, str], ...]  # (key, label) shown in the form
    secrets: tuple[tuple[str, str], ...]
    check: Callable[[Account], str]  # a live test; returns what it found ("3 buckets")


@dataclass(frozen=True)
class Connector:
    account: str  # the AccountKind it needs
    location: str  # what to type as the source location, shown in Settings
    fetch: Callable[[Account, str, Cache, int], list[Item]]


class Cache:
    """A folder of downloaded items plus a manifest of their versions, so unchanged items are not fetched again."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._manifest_path = folder / ".manifest.json"
        try:
            self._old: dict[str, dict[str, Any]] = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._old = {}
        self._new: dict[str, dict[str, Any]] = {}

    def path_for(self, uri: str, suffix: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", uri.split("://", 1)[-1]).strip("-")[:90]
        return self.folder / f"{slug}-{hashlib.sha1(uri.encode(), usedforsecurity=False).hexdigest()[:8]}{suffix}"

    def keep(
        self, uri: str, version: str, suffix: str, load: Callable[[], bytes], fields: dict[str, Any] | None = None
    ) -> Item:
        """The cached file for ``uri``; ``load`` runs only when the version changed or the file is missing."""
        path = self.path_for(uri, suffix)
        old = self._old.get(uri)
        if not (old and old.get("version") == version and old.get("file") == path.name and path.exists()):
            path.write_bytes(load())
        self._new[uri] = {"version": version, "file": path.name, "fields": fields or {}}
        return Item(uri, path, fields or {})

    def finish(self) -> None:
        """Remove files of items that are gone, and save the manifest."""
        current = {entry["file"] for entry in self._new.values()}
        for stale in self.folder.iterdir():
            if stale.is_file() and stale.name != self._manifest_path.name and stale.name not in current:
                stale.unlink()
        self._manifest_path.write_text(json.dumps(self._new), encoding="utf-8")


def version_of(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()[:16]


def http_client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True, **kwargs)


def request(client: httpx.Client, method: str, url: str, service: str, **kwargs: Any) -> httpx.Response:
    """One API call: waits and retries when rate-limited, and turns failures into messages people can act on."""
    for attempt in range(4):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"couldn't reach {service}: {exc}") from exc
        if response.status_code in (429, 503) and attempt < 3:
            time.sleep(min(float(response.headers.get("Retry-After", 2 ** (attempt + 1))), 30.0))
            continue
        if response.status_code in (401, 403):
            raise ConnectorError(f"{service} refused the credentials, or they can't read this ({response.status_code})")
        if response.status_code == 404:
            raise ConnectorError(f"{service} couldn't find {url.split('?')[0]}")
        if response.status_code >= 400:
            raise ConnectorError(f"{service} answered {response.status_code}: {response.text[:200]}")
        return response
    raise ConnectorError(f"{service} is busy; try again later")


ACCOUNT_KINDS: dict[str, AccountKind] = {}
CONNECTORS: dict[str, Connector] = {}


def fetch(source_type: str, account: Account, location: str, folder: Path, limit: int) -> list[Item]:
    connector = CONNECTORS[source_type]
    cache = Cache(folder)
    items = connector.fetch(account, location.strip(), cache, limit)
    cache.finish()
    return items


def _register() -> None:
    # Imported here so each connector module can register itself and import this module's helpers.
    from ragkit.connectors import atlassian, database, google, microsoft, notion, s3  # noqa: F401


_register()
