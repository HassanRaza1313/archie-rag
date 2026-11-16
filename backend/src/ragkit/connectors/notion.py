"""Notion, with an internal integration token.

Account: an internal integration's secret (Notion › Settings › Connections › Develop or manage integrations). Share
pages with the integration from each page's ··· menu › Connections.

Location: a page or database address (or id), or empty for everything shared with the integration. A page brings
its sub-pages; a database brings its rows, whose properties stay as structured fields. A page's last-edited time
is its version, so only edited pages are read again.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

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

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MAX_DEPTH = 6  # nested blocks read below a page


def _client(account: Account) -> httpx.Client:
    token = account.secret.get("token", "").strip()
    if not token:
        raise ConnectorError("enter the integration's secret")
    return http_client(headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION})


def check(account: Account) -> str:
    with _client(account) as client:
        me = request(client, "GET", f"{API}/users/me", "Notion").json()
    return f"connected as {me.get('name') or 'the integration'}"


def notion_id(location: str) -> str | None:
    """The page or database id in an address like https://www.notion.so/Team-Wiki-1a2b... (or a bare id)."""
    if not location:
        return None
    match = re.search(r"([0-9a-f]{32})(?:[?#]|$)", location.replace("-", "")) or re.search(
        r"([0-9a-f]{32})", location.replace("-", "")
    )
    if not match:
        raise ConnectorError("enter a Notion page or database address, or leave it empty for everything shared")
    raw = match.group(1)
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def plain(rich: list[dict[str, Any]] | None) -> str:
    return "".join(part.get("plain_text", "") for part in rich or [])


def title_of(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return plain(prop.get("title")) or "Untitled"
    return "Untitled"


def property_value(prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    value = prop.get(kind) if kind else None
    if kind in ("title", "rich_text"):
        return plain(value) or None
    if kind in ("select", "status"):
        return (value or {}).get("name")
    if kind == "multi_select":
        return [option["name"] for option in value or []] or None
    if kind == "date":
        return (value or {}).get("start")
    if kind == "people":
        return [person.get("name") for person in value or [] if person.get("name")] or None
    if kind in ("number", "checkbox", "url", "email", "phone_number", "created_time", "last_edited_time"):
        return value
    return None


_BLOCK_PREFIX = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "- ",
                 "numbered_list_item": "1. ", "to_do": "- [ ] ", "quote": "> ", "callout": "> "}  # fmt: skip


def blocks_markdown(client: httpx.Client, block_id: str, depth: int = 0) -> tuple[str, list[str]]:
    """A page's content as Markdown, and the ids of its sub-pages."""
    lines: list[str] = []
    children: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = request(client, "GET", f"{API}/blocks/{block_id}/children", "Notion", params=params).json()
        for block in data.get("results", []):
            kind = block.get("type", "")
            body = block.get(kind) or {}
            if kind == "child_page":
                children.append(block["id"])
                continue
            if kind == "code":
                lines += ["```", plain(body.get("rich_text")), "```"]
            elif kind == "table_row":
                lines.append("| " + " | ".join(plain(cell) for cell in body.get("cells", [])) + " |")
            elif "rich_text" in body:
                lines.append(_BLOCK_PREFIX.get(kind, "") + plain(body["rich_text"]))
            if block.get("has_children") and depth < MAX_DEPTH and kind not in ("child_database",):
                nested, more = blocks_markdown(client, block["id"], depth + 1)
                lines.append(nested)
                children += more
        if not data.get("has_more"):
            return "\n\n".join(line for line in lines if line.strip()), children
        cursor = data.get("next_cursor")


def fetch(account: Account, location: str, cache: Cache, limit: int) -> list[Item]:
    target = notion_id(location)
    items: list[Item] = []
    with _client(account) as client:
        pages: list[dict[str, Any]] = []
        if target is None:
            pages = _paged(client, "POST", f"{API}/search", {"filter": {"property": "object", "value": "page"}}, limit)
        else:
            try:
                request(client, "GET", f"{API}/databases/{target}", "Notion")
                pages = _paged(client, "POST", f"{API}/databases/{target}/query", {}, limit)
            except ConnectorError:  # not a database (or not shared as one): a page
                pages = [request(client, "GET", f"{API}/pages/{target}", "Notion").json()]
        seen: set[str] = set()
        while pages and len(items) < limit:
            page = pages.pop(0)
            if page["id"] in seen or page.get("archived") or page.get("in_trash"):
                continue
            seen.add(page["id"])
            title = title_of(page)
            properties = {name.lower(): property_value(prop) for name, prop in page.get("properties", {}).items()
                          if prop.get("type") != "title"}  # fmt: skip
            fields = {name: value for name, value in properties.items() if value not in (None, "", [])}
            subpages: list[str] = []

            def load(
                page: dict[str, Any] = page,
                title: str = title,
                fields: dict[str, Any] = fields,
                found: list[str] = subpages,
            ) -> bytes:
                body, more = blocks_markdown(client, page["id"])
                found.extend(more)
                meta = [
                    f"- **{key}**: {', '.join(map(str, v)) if isinstance(v, list) else v}" for key, v in fields.items()
                ]
                return "\n\n".join(part for part in (f"# {title}", "\n".join(meta), body) if part).encode()

            items.append(
                cache.keep(page.get("url", page["id"]), str(page.get("last_edited_time")), ".md", load, fields)
            )
            if target is not None:  # sub-pages of the chosen page; "everything shared" already lists them
                # shortcut: an unchanged page's blocks are read again to find its sub-pages; keep them in the
                # manifest if large Notion trees sync too slowly.
                if not subpages:
                    subpages = blocks_markdown(client, page["id"])[1]
                pages += [request(client, "GET", f"{API}/pages/{child}", "Notion").json() for child in subpages]
    return items


def _paged(client: httpx.Client, method: str, url: str, body: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(results) < limit:
        data = request(
            client,
            method,
            url,
            "Notion",
            json={**body, "page_size": 100, **({"start_cursor": cursor} if cursor else {})},
        ).json()
        results += [entry for entry in data.get("results", []) if entry.get("object") == "page"]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results[:limit]


ACCOUNT_KINDS["notion"] = AccountKind(
    "Notion",
    settings=(),
    secrets=(("token", "Internal integration secret"),),
    check=check,
)
CONNECTORS["notion"] = Connector("notion", "A page or database address; empty for everything shared", fetch)
