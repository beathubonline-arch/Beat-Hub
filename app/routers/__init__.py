from . import album
from . import music_publish
from . import pages

# Register creator album and music-publishing routes through the already-mounted
# pages router. The publishing router is included first so its stricter
# content-type-aware POST /dashboard/upload handler wins over the legacy handler
# in dashboard.py without changing dashboard authorization.
pages.router.include_router(music_publish.router)
pages.router.include_router(album.router)
