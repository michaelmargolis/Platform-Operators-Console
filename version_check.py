#version_check.py  ver 5
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


def main() -> int:
    root = Path(__file__).resolve().parent
    manifest = root / MANIFEST_NAME

    if not manifest.is_file():
        print(f"ERROR: version manifest not found: {manifest}")
        return 2

    try:
        entries = load_manifest(manifest)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read version manifest: {exc}")
        return 2

    errors: list[str] = []

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
