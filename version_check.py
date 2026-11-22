#version_check.py  ver 7
"""Check protoPOC source-file versions against file_versions.txt.

Run from anywhere with:
    python path/to/protoPOC/version_check.py

Exit status is 0 only when every tracked file exists and has the expected
version marker near the top of the file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MANIFEST_NAME = "file_versions.txt"
HEADER_LINES_TO_SCAN = 10

# Accept both Python/text comments, e.g.:
#   #poc_core.py  ver 5
#   # poc_core.py ver 5
# Windows batch comments, e.g.:
#   rem start_shs.cmd ver 1
# and XML comments, e.g.:
#   <!-- poc_main.ui  ver 2 -->
# JSON cannot contain comments, so config files may use a first metadata field:
#   "_file_version": "poc_config.json ver 3"
VERSION_RE = re.compile(
    r'(?:#|<!--|rem\s+|"_file_version"\s*:\s*")\s*([^"\r\n]+?)\s+ver\s+(\d+)\b',
    re.IGNORECASE,
)


def load_manifest(path: Path) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    seen: set[str] = set()

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("|")
        if len(parts) != 2:
            raise ValueError(f"{path.name}:{line_no}: expected path|version")

        relative_path = parts[0].strip().replace("\\", "/")
        try:
            expected_version = int(parts[1].strip())
        except ValueError as exc:
            raise ValueError(
                f"{path.name}:{line_no}: invalid version {parts[1].strip()!r}"
            ) from exc

        if relative_path in seen:
            raise ValueError(f"{path.name}:{line_no}: duplicate entry {relative_path}")

        seen.add(relative_path)
        entries.append((relative_path, expected_version))

    return entries


def read_version(path: Path) -> tuple[str, int] | None:
    try:
        with path.open("r", encoding="utf-8") as source:
            for _ in range(HEADER_LINES_TO_SCAN):
                line = source.readline()
                if not line:
                    break
                match = VERSION_RE.search(line)
                if match:
                    return match.group(1), int(match.group(2))
    except UnicodeDecodeError:
        return None

    return None


def shs_server_version(manifest: Path, entries: list[tuple[str, int]]) -> str:
    """Return the composite SHS dashboard version shown by server.py."""
    version_info = read_version(manifest)
    if version_info is None or version_info[0] != manifest.name:
        raise ValueError(f"{manifest.name}: cannot determine manifest file version")

    server_file_version = next(
        (
            version
            for relative_path, version in entries
            if relative_path == "sim_host_service/server.py"
        ),
        None,
    )
    if server_file_version is None:
        raise ValueError(
            f"{manifest.name}: sim_host_service/server.py is not version-tracked"
        )
    return f"{version_info[1]} : {server_file_version}"


def load_runtime_version_summary(root: Path, tracked_paths: set[str]):
    """Load and validate the derived POC/Analyzer runtime-version metadata."""
    from app_versions import get_app_versions, load_runtime_dependencies, read_global_release

    global_release = read_global_release(root)
    dependencies = load_runtime_dependencies(root)
    app_versions = get_app_versions(root)
    errors: list[str] = []

    for dependency in dependencies:
        if dependency.path not in tracked_paths:
            errors.append(
                f"RUNTIME  {dependency.path}  is not tracked by {MANIFEST_NAME}"
            )
        if not (root / dependency.path).is_file():
            errors.append(f"RUNTIME  {dependency.path}  dependency file is missing")
        if dependency.last_runtime_release > global_release:
            errors.append(
                f"RUNTIME  {dependency.path}  v{dependency.last_runtime_release} "
                f"is newer than global release v{global_release}"
            )

    poc_count = sum("poc" in item.apps for item in dependencies)
    analyzer_count = sum("analyzer" in item.apps for item in dependencies)
    shared_count = sum(item.apps == frozenset({"poc", "analyzer"}) for item in dependencies)
    return global_release, app_versions, poc_count, analyzer_count, shared_count, errors

def main() -> int:
    root = Path(__file__).resolve().parent
    manifest = root / MANIFEST_NAME

    if not manifest.is_file():
        print(f"ERROR: version manifest not found: {manifest}")
        return 2

    try:
        entries = load_manifest(manifest)
        shs_version = shs_server_version(manifest, entries)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read version manifest: {exc}")
        return 2

    tracked_paths = {relative_path for relative_path, _version in entries}
    try:
        (
            global_release,
            app_versions,
            poc_runtime_files,
            analyzer_runtime_files,
            shared_runtime_files,
            runtime_errors,
        ) = load_runtime_version_summary(root, tracked_paths)
    except (ImportError, OSError, ValueError) as exc:
        print(f"ERROR: cannot read runtime version metadata: {exc}")
        return 2

    errors: list[str] = list(runtime_errors)

    print(f"Repository update level : v{global_release}")
    print(f"POC runtime version      : v{app_versions['poc']}")
    print(f"Ride Analyzer runtime    : v{app_versions['analyzer']}")
    print(f"SHS server version       : {shs_version}")
    print(
        "Runtime dependencies     : "
        f"POC {poc_runtime_files}, Analyzer {analyzer_runtime_files}, "
        f"shared {shared_runtime_files}"
    )
    print()
    print(f"Checking {len(entries)} versioned files in {root}")

    for relative_path, expected_version in entries:
        path = root / relative_path

        if not path.is_file():
            errors.append(f"MISSING  {relative_path}  expected ver {expected_version}")
            continue

        version_info = read_version(path)
        if version_info is None:
            errors.append(f"NO VER   {relative_path}  expected ver {expected_version}")
            continue

        header_name, actual_version = version_info
        expected_name = path.name

        if header_name != expected_name:
            errors.append(
                f"NAME     {relative_path}  header says {header_name!r}, "
                f"expected {expected_name!r}"
            )

        if actual_version != expected_version:
            errors.append(
                f"VERSION  {relative_path}  found ver {actual_version}, "
                f"expected ver {expected_version}"
            )

    if errors:
        print("\nVERSION CHECK FAILED")
        for error in errors:
            print(f"  {error}")
        print(f"\n{len(errors)} problem(s) found.")
        return 1

    print("VERSION CHECK OK - all tracked files match the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
