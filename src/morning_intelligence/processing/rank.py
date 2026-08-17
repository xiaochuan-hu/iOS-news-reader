from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import NewsEvent


def rank_events(events: list[NewsEvent], preferences: PreferencesConfig) -> list[NewsEvent]:
    eligible = [
        event
        for event in events
        if event.final_score >= preferences.briefing.min_event_score
    ]
    eligible.sort(key=lambda event: (event.final_score, event.latest_seen_at), reverse=True)
    return eligible[: preferences.briefing.max_events]
