# test_dynamic_optimizer_dependency_closure_v206.py ver 1
"""Release guard for the Analyzer-managed dynamic optimizer dependency closure."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PREFIXES = ("tools.", "core.", "config.", "sims.", "kinematics.", "output.")


def _module_path(module: str) -> Path | None:
    base = ROOT.joinpath(*module.split("."))
    source = base.with_suffix(".py")
    if source.is_file():
        return source
    package = base / "__init__.py"
    if package.is_file():
        return package
    return None


def _reachable(start: str) -> set[Path]:
    stack = [_module_path(start)]
    seen: set[Path] = set()
    while stack:
        path = stack.pop()
        if path is None or path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = ".".join(path.with_suffix("").relative_to(ROOT).parts)
        if path.name == "__init__.py":
            module = ".".join(path.parent.relative_to(ROOT).parts)
        package = module.split(".")[:-1] if path.name != "__init__.py" else module.split(".")
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - node.level + 1]
                    if node.module:
                        base += node.module.split(".")
                    imported = ".".join(base)
                else:
                    imported = node.module or ""
                if imported:
                    candidates.append(imported)
                for alias in node.names:
                    submodule = (imported + "." + alias.name).strip(".")
                    if _module_path(submodule):
                        candidates.append(submodule)
            for candidate in candidates:
                if candidate.startswith(PREFIXES):
                    dependency = _module_path(candidate)
                    if dependency:
                        stack.append(dependency)
    return seen


def test_dynamic_optimizer_internal_import_closure_exists_and_is_version_tracked():
    manifest = {
        line.split("|", 1)[0]
        for line in (ROOT / "file_versions.txt").read_text(encoding="utf-8").splitlines()
        if "|" in line and not line.lstrip().startswith("#")
    }
    reachable = _reachable("tools.sim_player.dynamic_optimizer")
    assert reachable
    untracked = sorted(path.relative_to(ROOT).as_posix() for path in reachable if path.relative_to(ROOT).as_posix() not in manifest)
    assert untracked == [], "Dynamic optimizer dependencies missing from file_versions.txt: " + ", ".join(untracked)
