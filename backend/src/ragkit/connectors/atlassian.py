"""Confluence and Jira, Cloud or Data Center, through one Atlassian account.

Account: the site address (``https://company.atlassian.net`` or your own server), and for Cloud the account email
plus an API token; for Data Center a personal access token (leave the email empty).

- Confluence, location: one or more space keys (``HR, ENG``). Every page in those spaces, as the page's own HTML.
  A page's version number is its version, so only edited pages are downloaded again.
- Jira, location: a JQL query (``project = HELP AND updated >= -365d``). Each issue becomes a document with its
  description and comments; status, type, priority, assignee, labels and dates stay as structured fields.
"""

from __future__ import annotations

import html
from functools import partial
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
    version_of,
)

JIRA_FIELDS = [
    "summary", "description", "status", "issuetype", "priority", "assignee", "reporter", "labels",
    "created", "updated", "resolution", "comment", "project",
]  # fmt: skip


def _base(account: Account) -> str:
    base = account.settings.get("base_url", "").strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise ConnectorError("enter the site address, e.g. https://yourcompany.atlassian.net")
    return base.removesuffix("/wiki")


def _cloud(account: Account) -> bool:
    return ".atlassian.net" in _base(account) or bool(account.settings.get("email"))


def _client(account: Account) -> httpx.Client:
    token = account.secret.get("token", "")
    if not token:
        raise ConnectorError("enter an API token")
    if email := account.settings.get("email", "").strip():
        return http_client(auth=(email, token), headers={"Accept": "application/json"})
    return http_client(headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})


def check(account: Account) -> str:
    base = _base(account)
    with _client(account) as client:
        for path in ("/rest/api/2/myself", "/wiki/rest/api/user/current", "/rest/api/user/current"):
            try:
                me = request(client, "GET", base + path, "Atlassian").json()
            except ConnectorError as exc:
                if "refused" in str(exc):
                    raise
                continue
            return f"signed in as {me.get('displayName') or me.get('emailAddress') or me.get('username')}"
    raise ConnectorError("no Jira or Confluence found at that address")


# Confluence


def _page_html(title: str, body: str) -> bytes:
    name = html.escape(title)
    return f"<html><head><title>{name}</title></head><body><h1>{name}</h1>{body}</body></html>".encode()


def confluence(account: Account, location: str, cache: Cache, limit: int) -> list[Item]:
    keys = [key.strip() for key in location.replace(";", ",").split(",") if key.strip()]
    if not keys:
        raise ConnectorError("enter one or more space keys, e.g. HR, ENG")
    base, items = _base(account), list[Item]()
    with _client(account) as client:
        for key in keys:
            if _cloud(account):  # Cloud: REST v2, cursor paging
                spaces = request(client, "GET", f"{base}/wiki/api/v2/spaces", "Confluence", params={"keys": key})
                found = spaces.json().get("results", [])
                if not found:
                    raise ConnectorError(f"Confluence has no space with the key {key}, or the account can't see it")
                url: str | None = f"{base}/wiki/api/v2/spaces/{found[0]['id']}/pages"
                params: dict[str, Any] | None = {"body-format": "storage", "limit": 100, "status": "current"}
                while url and len(items) < limit:
                    data = request(client, "GET", url, "Confluence", params=params).json()
                    for page in data.get("results", []):
                        body = page.get("body", {}).get("storage", {}).get("value", "")
                        link = f"{base}/wiki{page.get('_links', {}).get('webui', '')}"
                        version = str(page.get("version", {}).get("number", ""))
                        updated = page.get("version", {}).get("createdAt")
                        load = partial(_page_html, page["title"], body)
                        items.append(cache.keep(link, version, ".html", load, {"space": key, "updated": updated}))
                        if len(items) >= limit:
                            break
                    nxt = data.get("_links", {}).get("next")
                    url, params = (f"{base}{nxt}" if nxt else None), None
            else:  # Data Center: REST v1, offset paging
                start = 0
                while len(items) < limit:
                    data = request(client, "GET", f"{base}/rest/api/content", "Confluence", params={
                        "spaceKey": key, "type": "page", "expand": "body.storage,version", "limit": 50, "start": start,
                    }).json()  # fmt: skip
                    results = data.get("results", [])
                    for page in results:
                        link = f"{base}{page.get('_links', {}).get('webui', '')}"
                        body = page["body"]["storage"]["value"]
                        version = str(page["version"]["number"])
                        load = partial(_page_html, page["title"], body)
                        fields = {"space": key, "updated": page["version"].get("when")}
                        items.append(cache.keep(link, version, ".html", load, fields))
                        if len(items) >= limit:
                            break
                    if len(results) < 50:
                        break
                    start += 50
    return items


# Jira


def adf_text(node: Any) -> str:
    """Plain text from Atlassian Document Format (Jira Cloud's rich text)."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    kind, children = node.get("type"), node.get("content") or []
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "status"):
        return str(node.get("attrs", {}).get("text", ""))
    inner = "".join(adf_text(child) for child in children)
    if kind == "listItem":
        return f"- {inner.strip()}\n"
    if kind in ("paragraph", "heading", "codeBlock", "blockquote", "tableRow"):
        return inner.strip() + "\n\n"
    if kind == "tableCell" or kind == "tableHeader":
        return inner.strip() + " | "
    return inner


def _name(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("name") or value.get("value") or value.get("key")
    return value


def issue_document(issue: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fields = issue.get("fields", {})
    text = lambda value: adf_text(value).strip() if isinstance(value, dict) else str(value or "").strip()  # noqa: E731
    structured = {
        "key": issue.get("key"),
        "project": _name(fields.get("project")),
        "status": _name(fields.get("status")),
        "type": _name(fields.get("issuetype")),
        "priority": _name(fields.get("priority")),
        "assignee": _name(fields.get("assignee")),
        "reporter": _name(fields.get("reporter")),
        "resolution": _name(fields.get("resolution")),
        "labels": fields.get("labels") or None,
        "created": fields.get("created"),
        "updated": fields.get("updated"),
    }
    structured = {key: value for key, value in structured.items() if value}
    lines = [f"# {issue.get('key')}: {fields.get('summary', '')}", ""]
    lines += [f"- **{key}**: {', '.join(value) if isinstance(value, list) else value}" for key, value in
              structured.items() if key not in ("key",)]  # fmt: skip
    if description := text(fields.get("description")):
        lines += ["", "## Description", "", description]
    comments = (fields.get("comment") or {}).get("comments", [])
    if comments:
        lines += ["", "## Comments", ""]
        for comment in comments:
            lines += [f"**{_name(comment.get('author'))}** ({comment.get('created', '')[:10]}):", "",
                      text(comment.get("body")), ""]  # fmt: skip
    return "\n".join(lines).strip() + "\n", structured


def jira(account: Account, location: str, cache: Cache, limit: int) -> list[Item]:
    if not location:
        raise ConnectorError("enter a JQL query, e.g. project = HELP AND updated >= -365d")
    base, items = _base(account), []
    with _client(account) as client:

        def keep(issue: dict[str, Any]) -> None:
            markdown, fields = issue_document(issue)
            data = markdown.encode()
            items.append(cache.keep(f"{base}/browse/{issue['key']}", version_of(markdown), ".md", lambda: data, fields))

        if _cloud(account):  # Cloud: the enhanced JQL search, token paging
            token: str | None = None
            while len(items) < limit:
                body: dict[str, Any] = {"jql": location, "fields": JIRA_FIELDS, "maxResults": 100}
                if token:
                    body["nextPageToken"] = token
                data = request(client, "POST", f"{base}/rest/api/3/search/jql", "Jira", json=body).json()
                for issue in data.get("issues", [])[: limit - len(items)]:
                    keep(issue)
                token = data.get("nextPageToken")
                if not token or data.get("isLast"):
                    break
        else:  # Data Center: REST v2, offset paging
            start = 0
            while len(items) < limit:
                data = request(client, "GET", f"{base}/rest/api/2/search", "Jira", params={
                    "jql": location, "fields": ",".join(JIRA_FIELDS), "startAt": start, "maxResults": 100,
                }).json()  # fmt: skip
                issues = data.get("issues", [])
                for issue in issues[: limit - len(items)]:
                    keep(issue)
                start += len(issues)
                if not issues or start >= data.get("total", 0):
                    break
    return items


ACCOUNT_KINDS["atlassian"] = AccountKind(
    "Atlassian (Confluence and Jira)",
    settings=(
        ("base_url", "Site address, e.g. https://yourcompany.atlassian.net"),
        ("email", "Account email (Cloud only; empty for a Data Center token)"),
    ),
    secrets=(("token", "API token (Cloud) or personal access token (Data Center)"),),
    check=check,
)
CONNECTORS["confluence"] = Connector("atlassian", "Space keys, e.g. HR, ENG", confluence)
CONNECTORS["jira"] = Connector("atlassian", "A JQL query, e.g. project = HELP AND updated >= -365d", jira)
