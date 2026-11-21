# __init__.py ver 30
"""MDX Ride Analyzer offline ride-analysis package.

The internal package path remains ``tools.sim_player`` for compatibility with
existing imports, scripts, settings and saved data. The Ride Analyzer runtime
version is derived from the shared monotonic repository release sequence.
"""

try:
    from app_versions import app_version_label
except ImportError:
    def app_version_label(_app):
        return "v?"

PRODUCT_NAME = "MDX Ride Analyzer"
PRODUCT_SHORT_NAME = "Ride Analyzer"
PROFILE_REVIEW_VERSION = "0.1.0"

# Operator/researcher-facing app version. This is the most recent global
# release in which an Analyzer runtime dependency had a semantic change.
APP_VERSION = app_version_label("analyzer")

# Legacy internal task-workspace series retained for compatibility with code
# that imports it. It is no longer the published Analyzer application version.
WORKSPACE_VERSION = "0.2.38"
