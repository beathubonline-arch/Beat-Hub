from . import album
from . import album_upload
from . import beat_catalog
from . import dashboard_analytics
from . import music_publish
from . import pages

# Register classification-aware routes through the already-mounted pages router.
# They are mounted before the legacy handlers so the public catalog and creator
# publishing flow use the new content-type rules without changing authorization.
pages.router.include_router(music_publish.router)
pages.router.include_router(beat_catalog.router)
pages.router.include_router(album.router)
pages.router.include_router(album_upload.router)
pages.router.include_router(dashboard_analytics.router)
