"""The NixOS module must not configure the panel behind the operator's back.

The panel's rule is **environment always wins** over anything the setup wizard
saved — deliberately, so an ops-managed deploy cannot be edited out from under
itself. The consequence is easy to get wrong, and this module got it wrong:

    HLIDSKJALF_PVE_NODE = "pve"       # module default, emitted unconditionally

A panel whose wizard had been told the node was `hella` therefore asked Proxmox
for a node called `pve` on every request. Proxmox answers a request for a node it
does not have by trying to *proxy* it to that hostname, so every node-scoped page
failed with `hostname lookup 'pve' failed — Name or service not known`, which
points at DNS, not at the actual cause. It took a live deployment to find.

So: an option the operator did not set must not become an environment variable.
`nullOr` + null default is how that is expressed, and this test enforces it — no
Nix required, because the failure mode is invisible in Nix's own type checking.
"""

import re
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "nix" / "module.nix"

# Settings the WIZARD owns. If the module has an opinion about any of these, that
# opinion silently beats whatever the operator typed during setup.
WIZARD_OWNED = [
    "pveHost",
    "pvePort",
    "pveNode",
    "pveTokenId",
    "pveFingerprint",
    "pveTls",
    "adminUser",
]


def _option_block(name: str) -> str:
    """The text of `name = lib.mkOption { ... };` — braces are balanced, so count."""
    text = MODULE.read_text()
    start = text.index(f"{name} = lib.mkOption {{")
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    raise AssertionError(f"unbalanced braces in option {name}")


@pytest.mark.parametrize("option", WIZARD_OWNED)
def test_wizard_owned_options_default_to_null(option):
    block = _option_block(option)
    assert "nullOr" in block, (
        f"services.hlidskjalf.settings.{option} is not nullable. The module must be "
        "able to say 'not set' — otherwise its default is emitted as an env var, and "
        "env beats the setup wizard."
    )
    m = re.search(r"default\s*=\s*([^;]+);", block)
    assert m, f"{option} has no explicit default"
    assert m.group(1).strip() == "null", (
        f"services.hlidskjalf.settings.{option} defaults to {m.group(1).strip()}, which "
        "the module will emit as an environment variable on every deploy — silently "
        "overriding what the operator configured in the wizard. Default it to null."
    )


def test_empty_values_are_filtered_out_of_the_environment():
    """null/"" must never reach systemd as an env var: an empty HLIDSKJALF_PVE_HOST
    is not a configuration choice, and the panel would treat a *set* variable as
    ops-managed."""
    text = MODULE.read_text()
    assert "nonEmpty = lib.filterAttrs" in text
    assert "settingsEnv = nonEmpty {" in text


def test_the_debug_page_can_be_switched_on():
    """The admin Debug page reads in-memory log/error buffers that only exist when
    `debug` (or logLevel=DEBUG) is set. Without an option for it, that page is
    permanently empty on a Nix deploy and looks broken."""
    text = MODULE.read_text()
    assert "HLIDSKJALF_DEBUG" in text and "HLIDSKJALF_LOG_LEVEL" in text
    assert "debug = lib.mkOption" in text
