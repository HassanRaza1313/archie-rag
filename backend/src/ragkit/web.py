"""Website source: crawl one site from a start page or a sitemap into a local cache, then index it like a folder.

Pages under the start page's folder on the same host are followed, up to ``max_pages``, honouring robots.txt
and its crawl delay. HTML pages are kept, and so are linked documents whose types the source accepts (PDF, Word
and so on). Every request, redirect included, is refused when the host resolves to this machine or a private
network, unless ``RAG_WEB_ALLOW_PRIVATE`` is set: a source URL must not reach the server's own neighbourhood.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
import time
import xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from ragkit import __version__
from ragkit.config import SourceConfig

logger = logging.getLogger(__name__)

USER_AGENT = f"ArchieBot/{__version__} (document indexer)"
MAX_BYTES = 20 * 1024 * 1024  # per page or document
MAX_REDIRECTS = 5
MAX_CRAWL_DELAY_S = 5.0
HTML_TYPES = ("text/html", "application/xhtml+xml")


class WebError(ValueError):
    """The site can't be read; the message is safe to show."""


def check_address(url: str, allow_private: bool) -> None:
    """Refuse anything but http(s) to public addresses (every address the host resolves to)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebError(f"'{url}' is not a web address (http:// or https://)")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise WebError(f"'{parts.hostname}' could not be found") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            raise WebError(
                f"'{parts.hostname}' is on this machine or a private network; set RAG_WEB_ALLOW_PRIVATE to index it"
            )
    # shortcut: the name is resolved again when connecting (DNS rebinding could slip past); pin the address
    # in a custom transport if sources are ever editable by untrusted people.


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.nofollow = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href") and "nofollow" not in (values.get("rel") or ""):
            self.links.append(str(values["href"]))
        elif tag == "meta" and (values.get("name") or "").lower() == "robots":
            self.nofollow = "nofollow" in (values.get("content") or "").lower()


class Crawler:
    def __init__(self, config: SourceConfig, cache: Path, allow_private: bool, client: httpx.Client) -> None:
        self.config = config
        self.cache = cache
        self.allow_private = allow_private
        self.client = client
        self.suffixes = {suffix.lower() for suffix in config.file_types}
        start = urlsplit(config.url)
        self.origin = f"{start.scheme}://{start.netloc}"
        self.prefix = start.path.rsplit("/", 1)[0] + "/" if not config.url.endswith(".xml") else "/"
        self.robots = robotparser.RobotFileParser()
        self.delay = 0.0

    def get(self, url: str) -> httpx.Response:
        """One request, following redirects by hand so each hop is checked and stays on the site."""
        for _ in range(MAX_REDIRECTS + 1):
            check_address(url, self.allow_private)
            with self.client.stream("GET", url) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(url, response.headers["location"])
                    if not self.in_scope(url, check_prefix=False):
                        raise WebError(f"{url} is on another site")
                    continue
                body = bytearray()
                for block in response.iter_bytes():
                    body += block
                    if len(body) > MAX_BYTES:
                        raise WebError(f"{url} is larger than {MAX_BYTES // (1024 * 1024)} MB")
                return httpx.Response(response.status_code, headers=response.headers, content=bytes(body),
                                      request=response.request)  # fmt: skip
        raise WebError(f"{url} redirects too many times")

    def in_scope(self, url: str, check_prefix: bool = True) -> bool:
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self.origin:
            return False
        return not check_prefix or (parts.path or "/").startswith(self.prefix)

    def load_robots(self) -> None:
        try:
            response = self.get(f"{self.origin}/robots.txt")
            lines = response.text.splitlines() if response.status_code == 200 else []
        except (httpx.HTTPError, WebError):
            lines = []
        self.robots.parse(lines)
        delay = self.robots.crawl_delay(USER_AGENT)
        self.delay = min(float(delay), MAX_CRAWL_DELAY_S) if delay else 0.0

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(USER_AGENT, url)

    def sitemap(self, url: str, depth: int = 0) -> list[str]:
        response = self.get(url)
        response.raise_for_status()
        try:
            root = ET.fromstring(response.content)  # noqa: S314
        except ET.ParseError as exc:
            raise WebError(f"{url} is not a valid sitemap") from exc
        locations = [(node.text or "").strip() for node in root.iter() if node.tag.endswith("loc")]
        if root.tag.endswith("sitemapindex") and depth == 0:
            return [page for loc in locations[:50] if self.in_scope(loc, False) for page in self.sitemap(loc, 1)]
        return [loc for loc in locations if loc]

    def crawl(self) -> list[tuple[str, Path]]:
        """Fetch the site into the cache; returns (url, file) for every page kept."""
        self.cache.mkdir(parents=True, exist_ok=True)
        self.load_robots()
        from_sitemap = self.config.url.endswith(".xml")
        queue = deque(self.sitemap(self.config.url) if from_sitemap else [self.config.url])
        seen = set(queue)
        kept: list[tuple[str, Path]] = []
        failures = 0
        while queue and len(kept) < self.config.max_pages:
            url = queue.popleft()
            if not self.in_scope(url, check_prefix=not from_sitemap) or not self.allowed(url):
                continue
            try:
                response = self.get(url)
            except (httpx.HTTPError, WebError) as exc:
                if url == self.config.url:
                    raise WebError(f"couldn't read {url}: {exc}") from exc
                failures += 1
                logger.info("skipped %s: %s", url, exc)
                continue
            finally:
                if self.delay:
                    time.sleep(self.delay)
            if response.status_code != 200:
                if url == self.config.url:
                    raise WebError(f"{url} answered {response.status_code}")
                continue
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if kind in HTML_TYPES:
                kept.append((url, self.save(url, ".html", response.content)))
                links = _Links()
                links.feed(response.text)
                if links.nofollow or from_sitemap:
                    continue
                for href in links.links:
                    target = urldefrag(urljoin(url, href))[0]
                    suffix = Path(urlsplit(target).path).suffix.lower()
                    if target not in seen and (not suffix or suffix in self.suffixes or suffix in (".htm", ".php")):
                        seen.add(target)
                        queue.append(target)
            elif (suffix := Path(urlsplit(url).path).suffix.lower()) in self.suffixes:
                kept.append((url, self.save(url, suffix, response.content)))
        if failures:
            logger.info("crawl of %s: %d page(s) could not be read", self.config.url, failures)
        return kept

    def save(self, url: str, suffix: str, content: bytes) -> Path:
        """A readable, stable file name per URL, so citations name the page."""
        parts = urlsplit(url)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{parts.netloc}{parts.path}{parts.query}").strip("-")
        name = slug[:90] + "-" + hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:8]
        path = self.cache / f"{name}{suffix}"
        path.write_bytes(content)
        return path


def _client() -> httpx.Client:
    timeout = httpx.Timeout(20.0, connect=10.0)
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=False)


def probe_site(config: SourceConfig, allow_private: bool) -> None:
    """The start page (or sitemap) answers: a quick check before any crawl."""
    if not config.url:
        raise WebError("enter the website's address")
    with _client() as client:
        crawler = Crawler(config, Path(), allow_private, client)
        try:
            response = crawler.get(config.url)
        except httpx.HTTPError as exc:
            raise WebError(f"couldn't read {config.url}: {exc}") from exc
    if response.status_code != 200:
        raise WebError(f"{config.url} answered {response.status_code}")


def crawl_site(config: SourceConfig, cache: Path, allow_private: bool) -> list[tuple[str, Path]]:
    """Crawl and drop cached files of pages that are gone, so the cache doesn't grow forever."""
    if not config.url:
        raise WebError("enter the website's address")
    with _client() as client:
        kept = Crawler(config, cache, allow_private, client).crawl()
    current = {path for _, path in kept}
    for stale in cache.glob("*"):
        if stale.is_file() and stale not in current:
            stale.unlink()
    return kept
