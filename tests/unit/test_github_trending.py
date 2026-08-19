from pathlib import Path

import httpx

from morning_intelligence.briefing.spoken_script import format_github_section
from morning_intelligence.domain import GitHubRepositoryIntelligence
from morning_intelligence.providers import GitHubTrendingProvider, parse_github_trending

FIXTURE = Path(__file__).parents[1] / "fixtures" / "github_trending_today.html"


def test_parser_skips_malformed_row_and_keeps_first_ten_valid_in_page_order() -> None:
    repositories = parse_github_trending(FIXTURE.read_text(encoding="utf-8"))

    assert len(repositories) == 10
    assert [repository.rank for repository in repositories] == list(range(1, 11))
    assert repositories[0].full_name == "alpha/one"
    assert repositories[0].description == "Alpha project"
    assert repositories[0].language == "Python"
    assert repositories[0].stars_today == 1001
    assert repositories[-1].full_name == "kappa/ten"


async def test_provider_uses_official_global_daily_url_without_real_network() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"), request=request)

    repositories = await GitHubTrendingProvider(
        transport=httpx.MockTransport(handler)
    ).fetch_today()

    assert len(requests) == 1
    assert str(requests[0].url) == "https://github.com/trending?since=daily"
    assert len(repositories) == 10


def test_spoken_section_fills_missing_llm_row_and_keeps_all_top_ten_once() -> None:
    repositories = parse_github_trending(FIXTURE.read_text(encoding="utf-8"))
    intelligence = [
        GitHubRepositoryIntelligence(
            repo=repository.full_name,
            summary_zh=f"对第{repository.rank}名的输入内概括",
            why_it_matters="值得观察后续社区活动",
        )
        for repository in repositories
        if repository.rank != 7
    ]

    spoken = format_github_section(repositories, intelligence)

    positions = [spoken.index(repository.full_name) for repository in repositories]
    assert positions == sorted(positions)
    assert all(spoken.count(repository.full_name) == 1 for repository in repositories)
    assert "eta/seven，Eta project" in spoken
    assert spoken.endswith("以上就是今天 GitHub 热榜前十中值得关注的项目。")
