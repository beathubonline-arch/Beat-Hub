from . import album
from . import beat_catalog
from . import music_publish
from . import pages

# Register classification-aware routes through the already-mounted pages router.
# They are mounted before the legacy handlers so the public catalog and creator
# publishing flow use the new content-type rules without changing authorization.
pages.router.include_router(music_publish.router)
pages.router.include_router(beat_catalog.router)
pages.router.include_router(album.router)
