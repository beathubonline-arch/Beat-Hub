from . import album
from . import album_upload
from . import beat_catalog
from . import dashboard
from . import dashboard_analytics
from . import marketplace
from . import music_publish
from . import pages
from . import creator_merch_integration

# Register canonical public marketplace discovery before the legacy catalog.
# /beats remains the compatibility entry point for the Marketplace navigation,
# while /marketplace/beats is the dedicated beat catalogue.
pages.router.include_router(music_publish.router)
pages.router.include_router(marketplace.router)
pages.router.include_router(beat_catalog.router)
pages.router.include_router(album.router)
pages.router.include_router(album_upload.router)
pages.router.include_router(dashboard_analytics.router)
