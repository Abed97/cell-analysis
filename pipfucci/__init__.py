"""PIP-FUCCI Incucyte analysis: segmentation, tracking and cell-cycle phase calling."""

__version__ = "0.1.0"
APP_NAME = "PIP-FUCCI"

try:  # written by the release build
    from ._build_info import GITHUB_REPO, BUILD_VERSION  # type: ignore

    if BUILD_VERSION:
        __version__ = BUILD_VERSION
except Exception:  # pragma: no cover
    GITHUB_REPO = ""
