"""The Binderscope app: a small local server with an import page, a project list and the review pages (docs/app.md).

``binder-review app`` (or the Binderscope application the installer creates) starts it. It listens on this computer only,
reads run folders in place and writes only below the Binderscope folder (default ``~/Binderscope``).
"""

DEFAULT_PORT = 51731        # fixed on purpose: the review pages keep your decisions per address, so the address must not change
APP_NAME = "binderscope"
BUILD_REVISION = 2          # bump when an analysis change makes older projects worth rebuilding: 2 = designs placed by a fit around the interface, the Inspector shows each design's own target
