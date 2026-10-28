from .routes_briefings import router as briefings_router
from .routes_email_ingest import router as email_ingest_router
from .routes_health import router as health_router
from .routes_media import router as media_router
from .routes_podcast import router as podcast_router

__all__ = [
    "briefings_router",
    "email_ingest_router",
    "health_router",
    "media_router",
    "podcast_router",
]
