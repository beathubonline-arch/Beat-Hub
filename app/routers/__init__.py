from . import album
from . import pages

# Register the album router through the already-mounted pages router.
# This keeps main.py's existing router registration unchanged while making
# album creation available to both artists and producers through require_creator.
pages.router.include_router(album.router)
