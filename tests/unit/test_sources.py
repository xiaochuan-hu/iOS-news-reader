from datetime import UTC, datetime, timedelta

import httpx

from morning_intelligence.config.settings import RSSFeedConfig
from morning_intelligence.sources import RSSSource


async def test_rss_source_is_a_real_vertical_adapter() -> None:
    now = datetime.now(UTC)
    fresh_date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    old_date = (now - timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    xml = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
    <item><title>Fresh story</title><link>https://example.com/fresh</link>
    <description>Useful detail</description><pubDate>{fresh_date}</pubDate></item>
    <item><title>Old story</title><link>https://example.com/old</link>
    <pubDate>{old_date}</pubDate></item>
    </channel></rss>"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xml.encode(), request=request)

    source = RSSSource(
        RSSFeedConfig(name="Test Feed", url="https://feed.example/rss", category="world")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await source.fetch(now - timedelta(hours=24), client)
    assert [item.title for item in items] == ["Fresh story"]
    assert items[0].source == "Test Feed"
