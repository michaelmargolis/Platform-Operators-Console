# create_poc_handover_zip.py ver 21
"""Create a complete, reviewable POC handover archive.

Run this script from any location. The project root is the directory containing
this script. Every file named by file_versions.txt is mandatory. Also include
relevant unversioned source, configuration and image files. Restore missing
UI/display images from the known historical project copy (assets only), and
fail if essential runtime/installer assets remain missing. Do not package old
project trees, analysis exports, individual historical release notes, captures,
caches, logs, environments, existing archives, or build output.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import zipfile


ARCHIVE_ROOT = "PlatformOperatorsConsole"
DEFAULT_ARCHIVE_NAME = "PlatformOperatorsConsole_full_handover.zip"

INCLUDED_SUFFIXES = {
    ".bat", ".cfg", ".cmd", ".conf", ".cs", ".csv", ".desktop",
    ".docx", ".dll", ".gif", ".ico", ".ini", ".jpeg", ".jpg",
    ".json", ".md", ".png", ".ps1", ".py", ".qrc", ".rules",
    ".service", ".sh", ".svg", ".toml", ".txt", ".ui", ".webp",
    ".xml", ".yaml", ".yml",
}
INCLUDED_EXTENSIONLESS_NAMES = {"LICENSE", "COPYING", ".gitignore"}

EXCLUDED_DIR_NAMES = {
    ".git", ".idea", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", ".vscode", "__pycache__", "build", "captures",
    "dist", "logs", "venv", "analysis_exports",
}
EXCLUDED_RELATIVE_DIRS = {
    # Old update/staging trees that duplicate canonical project paths.
    Path("old PlatformOperatorsConsole"),
    Path("config/core"),
    Path("config/fan_controller"),
    Path("config/sim_host_service"),
    Path("config/sims"),
    Path("config/tests"),
    Path("config/ui"),
    Path("sims/from ai"),
    Path("source"),
    Path("ui/temp"),
}
EXCLUDED_SUFFIXES = {
    ".bak", ".log", ".orig", ".pyo", ".pyc", ".swp", ".tmp", ".zip",
}
EXCLUDED_RELATIVE_PATHS = {
    # Regenerated for every archive. Excluding an extracted copy prevents two
    # entries with the same archive path when building the next ground truth.
    Path("PACKAGE_CONTENTS_SHA256.txt"),
    # Superseded handovers, working notes, loose delta files and diagnostics.
    Path("POC_DEVELOPMENT_HANDOVER_v106.md"),
    Path("POC_DEVELOPMENT_HANDOVER_v118.md"),
    Path("POC_DEVELOPMENT_HANDOVER_v119.md"),
    Path("POC_DEVELOPMENT_HANDOVER_v120.md"),
    Path("POC_Raspberry_Pi_Technician_Guide.md"),
    Path("config.py"),
    Path("config/file_versions.txt"),
    Path("protoPOC_handoff_2026-08-30.md"),
    Path("sim.py"),
    Path("sims/spacecoaster/source/out.csv"),
    Path("sims/spacecoaster/source/sc_debugger.py"),
    Path("tests/poc_usage.sh"),
    Path("todo.docx"),
    Path("ui/main_window_from pi.py"),
    # Obsolete zero-byte placeholder. The active Touch Display 2 installer uses
    # raspi/assets/splash/poc_splash_touch2_boot.png instead.
    Path("raspi/assets/splash/poc_splash_1280x720.png"),
}
LEGACY_ASSET_ROOT = Path("old PlatformOperatorsConsole")
LEGACY_RECOVERY_DIRS = (
    Path("images"),
    Path("raspi/assets/splash"),
    Path("raspi/assets/wallpaper"),
)
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg"}
REQUIRED_ASSETS = {
    Path("images/POC.png"),
    Path("images/activate_warning.png"),
    Path("images/nogo.png"),
    Path("images/warning.png"),
    Path("images/ok.png"),
    Path("raspi/assets/splash/poc_splash_touch2_boot.png"),
    Path("raspi/assets/wallpaper/poc_wallpaper_1280x720.png"),
}
OLD_RELEASE_NOTE_RE = re.compile(r"README_v\d+\.txt$", re.IGNORECASE)

CAPTURE_NAME_RE = re.compile(
    r"(?:nolimits2|spacecoaster|xplane)_telemetry_\d{8}_\d{6}\.csv$",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Create a complete POC source/configuration handover ZIP."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root.parent / DEFAULT_ARCHIVE_NAME,
        help="output ZIP path (default: beside the project directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output ZIP",
    )
    return parser.parse_args()


def load_versioned_paths(manifest: Path) -> set[Path]:
    paths: set[Path] = set()
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) != 2:
            raise RuntimeError(
                f"{manifest.name}:{line_number}: expected relative_path|version"
            )
        relative = Path(fields[0].strip().replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe manifest path: {relative}")
        if relative in paths:
            raise RuntimeError(f"Duplicate manifest path: {relative}")
        paths.add(relative)
    obsolete = [path for path in paths if OLD_RELEASE_NOTE_RE.fullmatch(path.name)]
    if obsolete:
        raise RuntimeError(
            "Remove historical README_vXXX.txt entries from file_versions.txt; "
            "track changelog.txt|1 instead: " + ", ".join(map(str, sorted(obsolete)))
        )
    if Path("changelog.txt") not in paths:
        raise RuntimeError("file_versions.txt must track changelog.txt|1")
    return paths


def excluded_unversioned(relative: Path, output: Path, root: Path) -> bool:
    if relative in EXCLUDED_RELATIVE_PATHS:
        return True
    if OLD_RELEASE_NOTE_RE.fullmatch(relative.name):
        return True
    if relative.name.startswith("~$"):
        return True
    if any(relative == directory or directory in relative.parents for directory in EXCLUDED_RELATIVE_DIRS):
        return True
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
        return True
    if relative.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    if CAPTURE_NAME_RE.match(relative.name):
        return True
    try:
        if (root / relative).resolve() == output.resolve():
            return True
    except OSError:
        pass
    return False


def discover_files(root: Path, output: Path, versioned: set[Path]) -> dict[Path, Path]:
    """Return archive-relative path -> on-disk source, preferring canonical files."""
    missing = [path for path in sorted(versioned) if not (root / path).is_file()]
    if missing:
        formatted = "\n  ".join(str(path) for path in missing)
        raise RuntimeError(f"Files required by file_versions.txt are missing:\n  {formatted}")
    selected: dict[Path, Path] = {path: root / path for path in versioned}

    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.relative_to(root)
        if relative in selected or excluded_unversioned(relative, output, root):
            continue
        if (candidate.suffix.lower() in INCLUDED_SUFFIXES
                or candidate.name in INCLUDED_EXTENSIONLESS_NAMES):
            selected[relative] = candidate

    # Some exported handovers lost top-level UI and Raspberry Pi display assets,
    # while retaining them in a historical project copy. Recover only known asset
    # directories; never use that older copy as a source for code/configuration.
    # A canonical asset, if present, ALWAYS takes precedence.
    old_root = root / LEGACY_ASSET_ROOT
    for directory in LEGACY_RECOVERY_DIRS:
        source_dir = old_root / directory
        if not source_dir.is_dir():
            continue
        for candidate in source_dir.rglob("*"):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(old_root)
            if relative in selected or (root / relative).exists():
                continue
            if candidate.suffix.lower() not in ASSET_SUFFIXES:
                continue
            if relative.name.lower().endswith(".png.png"):
                continue  # malformed superseded asset copy
            if excluded_unversioned(relative, output, root):
                continue
            selected[relative] = candidate

    missing_assets = sorted(path for path in REQUIRED_ASSETS if path not in selected)
    if missing_assets:
        formatted = "\n  ".join(str(path) for path in missing_assets)
        raise RuntimeError(
            "Missing mandatory POC UI / Raspberry Pi display resources:\n  "
            + formatted + "\nRestore these files in the canonical project paths "
            "(or provide the historical copy) before packaging."
        )

    return dict(sorted(selected.items(), key=lambda item: item[0].as_posix().lower()))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_versions(root: Path) -> None:
    checker = root / "version_check.py"
    if not checker.is_file():
        raise RuntimeError("version_check.py is missing")
    result = subprocess.run(
        [sys.executable, str(checker)],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError("Version validation failed; archive was not created")


def package_manifest(files: dict[Path, Path], versioned: set[Path]) -> bytes:
    lines = [
        "# POC complete handover package contents",
        "# SHA-256 | source | relative path",
    ]
    for relative, source in files.items():
        source_kind = "versioned" if relative in versioned else "unversioned"
        lines.append(f"{sha256(source)} | {source_kind} | {relative.as_posix()}")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def build_archive(output: Path, files: dict[Path, Path], versioned: set[Path]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, source in files.items():
            archive.write(source, f"{ARCHIVE_ROOT}/{relative.as_posix()}")
        archive.writestr(
            f"{ARCHIVE_ROOT}/PACKAGE_CONTENTS_SHA256.txt",
            package_manifest(files, versioned),
        )

    with zipfile.ZipFile(output, "r") as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"ZIP integrity check failed at {bad_member}")
        expected_count = len(files) + 1
        if len(archive.namelist()) != expected_count:
            raise RuntimeError(
                f"ZIP contains {len(archive.namelist())} entries; expected {expected_count}"
            )


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent
    output = args.output.expanduser().resolve()
    if output.exists() and not args.force:
        print(f"ERROR: output already exists: {output}", file=sys.stderr)
        print("Use --force to replace it.", file=sys.stderr)
        return 2

    try:
        manifest = root / "file_versions.txt"
        versioned = load_versioned_paths(manifest)
        validate_versions(root)
        files = discover_files(root, output, versioned)
        unexpected_empty_files = [
            path for path, source in files.items()
            if source.stat().st_size == 0 and path.name != "__init__.py"
        ]
        if unexpected_empty_files:
            formatted = "\n  ".join(str(path) for path in unexpected_empty_files)
            raise RuntimeError(
                "Unexpected zero-length files must be replaced or removed before "
                f"packaging:\n  {formatted}"
            )

        if output.exists():
            output.unlink()
        build_archive(output, files, versioned)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Created: {output}")
    recovered = [
        relative for relative, source in files.items()
        if source != root / relative
    ]
    print(f"Included project files: {len(files)}")
    if recovered:
        print(f"Recovered {len(recovered)} missing asset(s) from historical project copy:")
        for path in recovered:
            print(f"  {path.as_posix()}")
    print(f"Versioned files: {len(versioned)}")
    print(f"Additional relevant files: {len(files) - len(versioned)}")
    print(f"ZIP size: {output.stat().st_size} bytes")
    print(f"SHA-256: {sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
