"""nix/package.nix must list every runtime dependency the backend declares.

Nix gives a Python application *only* the packages named in `dependencies`. There
is no site-packages to fall back on, so a dependency missing from that list does
not fail the build — it produces a panel that starts and dies on first import.

That is not hypothetical: `cryptography` was missing for the whole life of the
file. `secretbox.py` imports it to decrypt the stored Proxmox token, so the very
first request after startup would have crashed, on a host with no dev tooling on
it. The Nix build itself never noticed, because nothing imported the app.

`pythonImportsCheck` in package.nix now catches this in the builder. This test
catches it *here*, in a suite that runs on every PR without needing Nix installed
— which matters, because nobody runs `nix build` before merging a Python change.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "backend" / "pyproject.toml"
PACKAGE_NIX = ROOT / "nix" / "package.nix"

# PyPI name -> nixpkgs python3Packages attribute, where they differ.
NIX_ATTR = {
    "argon2-cffi": "argon2-cffi",
    "pydantic-settings": "pydantic-settings",
}


def _runtime_deps() -> set[str]:
    """The [project].dependencies of the backend, stripped of version specifiers."""
    meta = tomllib.loads(PYPROJECT.read_text())
    deps = meta["project"]["dependencies"]
    return {re.split(r"[<>=!~\[]", d, maxsplit=1)[0].strip().lower() for d in deps}


def _nix_dependencies() -> set[str]:
    """The attribute names inside package.nix's `dependencies = with ...; [ ... ];`."""
    text = PACKAGE_NIX.read_text()
    m = re.search(r"dependencies\s*=\s*with\s+python\d*Packages;\s*\[(.*?)\]", text, re.S)
    assert m, "could not find the `dependencies = with python312Packages; [...]` list"
    body = re.sub(r"#.*", "", m.group(1))  # strip trailing comments
    return {tok.strip().lower() for tok in body.split() if tok.strip()}


def test_every_runtime_dependency_is_packaged():
    missing = {
        dep for dep in _runtime_deps()
        if NIX_ATTR.get(dep, dep) not in _nix_dependencies()
    }
    assert not missing, (
        f"backend/pyproject.toml requires {sorted(missing)}, which nix/package.nix does "
        "not build into the app. Nix provides ONLY what is listed there, so the panel "
        "would start and then crash on import."
    )


def test_nix_package_does_not_carry_stale_dependencies():
    """The reverse drift: a dependency dropped from pyproject but left in the Nix
    closure. Harmless at runtime, but it is dead weight and a lie about what the
    app needs."""
    extra = _nix_dependencies() - {NIX_ATTR.get(d, d) for d in _runtime_deps()}
    assert not extra, f"nix/package.nix builds {sorted(extra)}, which the backend no longer requires"


def test_version_matches_pyproject():
    meta = tomllib.loads(PYPROJECT.read_text())
    version = meta["project"]["version"]
    assert f'version = "{version}"' in PACKAGE_NIX.read_text(), (
        f"nix/package.nix does not declare version {version} — a Nix deployment would "
        "report the wrong version in Settings -> Updates."
    )


@pytest.mark.parametrize("mod", ["hlidskjalf", "hlidskjalf.main", "hlidskjalf.secretbox"])
def test_the_modules_pythonimportscheck_asserts_actually_import(mod):
    """package.nix proves the app imports at build time. Keep that list honest: if
    one of these modules is renamed, the Nix build starts failing for a silly
    reason and someone deletes the check instead of fixing it."""
    __import__(mod)
    assert f'"{mod}"' in PACKAGE_NIX.read_text(), f"{mod} is missing from pythonImportsCheck"
