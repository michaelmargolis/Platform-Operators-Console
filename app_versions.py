#app_versions.py ver 1
"""Derived POC and Ride Analyzer runtime versions.

The repository keeps one monotonic global release number in ``file_versions.txt``.
Each runtime dependency in ``runtime_dependencies.txt`` records the global
release in which that dependency last had a runtime-semantic change.  The app
version is the highest such release for that app.

A value-only configuration/calibration edit does not require changing the
runtime-release field.  A format, interpretation, API, or executable-behaviour
change does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

FILE_MANIFEST_NAME = "file_versions.txt"
RUNTIME_MANIFEST_NAME = "runtime_dependencies.txt"
VALID_APPS = frozenset({"poc", "analyzer"})
_GLOBAL_RELEASE_RE = re.compile(r"^#\s*Global release:\s*v?(\d+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class RuntimeDependency:
    path: str
    apps: frozenset[str]
    last_runtime_release: int


def repository_root() -> Path:
    return Path(__file__).resolve().parent


def read_global_release(root: Path | None = None) -> int:
    root = Path(root) if root is not None else repository_root()
    manifest = root / FILE_MANIFEST_NAME
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        match = _GLOBAL_RELEASE_RE.match(raw_line.strip())
        if match:
            return int(match.group(1))
    raise ValueError(f"{manifest.name}: missing '# Global release: vN' header")


def load_runtime_dependencies(root: Path | None = None) -> list[RuntimeDependency]:
    root = Path(root) if root is not None else repository_root()
    manifest = root / RUNTIME_MANIFEST_NAME
    entries: list[RuntimeDependency] = []
    seen: set[str] = set()

    for line_no, raw_line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3:
            raise ValueError(
                f"{manifest.name}:{line_no}: expected path|apps|last_runtime_release"
            )

        relative_path, apps_text, release_text = parts
        relative_path = relative_path.replace("\\", "/")
        if not relative_path or relative_path.startswith("/") or ".." in Path(relative_path).parts:
            raise ValueError(f"{manifest.name}:{line_no}: unsafe path {relative_path!r}")
        if relative_path in seen:
            raise ValueError(f"{manifest.name}:{line_no}: duplicate entry {relative_path}")

        apps = frozenset(app.strip().casefold() for app in apps_text.split(",") if app.strip())
        if not apps or not apps <= VALID_APPS:
            raise ValueError(
                f"{manifest.name}:{line_no}: invalid apps {apps_text!r}; "
                f"expected poc, analyzer, or poc,analyzer"
            )
        try:
            release = int(release_text.removeprefix("v"))
        except ValueError as exc:
            raise ValueError(
                f"{manifest.name}:{line_no}: invalid runtime release {release_text!r}"
            ) from exc
        if release < 1:
            raise ValueError(f"{manifest.name}:{line_no}: runtime release must be positive")

        seen.add(relative_path)
        entries.append(RuntimeDependency(relative_path, apps, release))

    if not entries:
        raise ValueError(f"{manifest.name}: no runtime dependencies defined")
    return entries


def get_app_version(app: str, root: Path | None = None) -> int:
    app_key = str(app).strip().casefold()
    if app_key not in VALID_APPS:
        raise ValueError(f"Unknown application {app!r}; expected one of {sorted(VALID_APPS)}")
    releases = [
        item.last_runtime_release
        for item in load_runtime_dependencies(root)
        if app_key in item.apps
    ]
    if not releases:
        raise ValueError(f"No runtime dependencies are assigned to {app_key}")
    version = max(releases)
    global_release = read_global_release(root)
    if version > global_release:
        raise ValueError(
            f"{app_key} runtime version v{version} is newer than global release v{global_release}"
        )
    return version


def get_app_versions(root: Path | None = None) -> dict[str, int]:
    return {app: get_app_version(app, root) for app in sorted(VALID_APPS)}


def app_version_label(app: str, root: Path | None = None) -> str:
    """Return an operator-facing version label without making startup fragile."""
    try:
        return f"v{get_app_version(app, root)}"
    except (OSError, ValueError):
        return "v?"
