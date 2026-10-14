from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx

from morning_intelligence.domain import GitHubTrendingRepo

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"


class _TrendingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, object]] = []
        self.current: dict[str, object] | None = None
        self.depth = 0
        self.capture: str | None = None
        self.capture_depth = 0
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "Box-row" in classes and self.current is None:
            self.current = {}
            self.depth = 1
            return
        if self.current is None:
            return
        self.depth += 1
        href = attributes.get("href") or ""
        if tag == "h2":
            self._start_capture("name")
        elif tag == "p" and "col-9" in classes:
            self._start_capture("description")
        elif (
            tag == "span"
            and "itemprop" in attributes
            and attributes["itemprop"] == "programmingLanguage"
        ):
            self._start_capture("language")
        elif tag == "span" and "float-sm-right" in classes:
            self._start_capture("stars_today")
        elif tag == "a" and href.endswith("/stargazers"):
            self._start_capture("total_stars")
        elif tag == "a" and href.endswith("/forks"):
            self._start_capture("forks")

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.capture and self.depth == self.capture_depth:
            self.current[self.capture] = " ".join("".join(self.text).split())
            self.capture = None
            self.text = []
        self.depth -= 1
        if tag == "article" and self.depth == 0:
            self.rows.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.text.append(data)

    def _start_capture(self, name: str) -> None:
        self.capture = name
        self.capture_depth = self.depth
        self.text = []


def parse_github_trending(html: str) -> list[GitHubTrendingRepo]:
    parser = _TrendingParser()
    parser.feed(html)
    repositories: list[GitHubTrendingRepo] = []
    seen: set[str] = set()
    for row in parser.rows:
        full_name = re.sub(r"\s+", "", str(row.get("name", ""))).strip("/")
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", full_name):
            continue
        identity = full_name.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        repositories.append(
            GitHubTrendingRepo(
                rank=len(repositories) + 1,
                full_name=full_name,
                url=f"https://github.com/{full_name}",
                description=str(row.get("description", "")).strip(),
                language=str(row["language"]).strip() if row.get("language") else None,
                total_stars=_parse_count(row.get("total_stars")),
                forks=_parse_count(row.get("forks")),
                stars_today=_parse_count(row.get("stars_today")),
            )
        )
        if len(repositories) == 10:
            break
    return repositories


def _parse_count(value: object) -> int | None:
    if not value:
        return None
    match = re.search(r"([\d,.]+)\s*(?:stars?\s+today)?", str(value), re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", "").replace(".", ""))
    except ValueError:
        return None


class GitHubTrendingProvider:
    def __init__(
        self,
        *,
        timeout_seconds: float = 8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch_today(self) -> list[GitHubTrendingRepo]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
            follow_redirects=True,
            headers={
                "User-Agent": "Morning-Intelligence/4.0 (+https://github.com/trending)",
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            response = await client.get(GITHUB_TRENDING_URL)
            response.raise_for_status()
        repositories = parse_github_trending(response.text)
        return repositories
