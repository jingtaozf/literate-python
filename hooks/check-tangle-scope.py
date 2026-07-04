#!/usr/bin/env python3
# PostToolUse helper: verify a literate .org tangles ONLY into registered LP
# zones (LITERATE_AGENT_TANGLED_ROOTS) before tangle-org-buffer.sh runs the
# tangle.
#
#   exit 0 — safe to tangle (every :tangle output is under the registry, or
#            TANGLED_ROOTS is empty so the whole repo is the zone).
#   exit 2 — at least one :tangle output lands OUTSIDE the registry; the
#            offenders + remediation are printed to stderr and the tangle must
#            NOT proceed.  A tangle into a de-registered tree would clobber
#            source the team now edits directly.
#
# Scope logic lives in lib/tangle_lookup.py (shared with the block hooks so the
# edit-block and tangle-gate agree on what "LP-owned" means).  The .sh suffix
# convention doesn't apply here — this is invoked as a plain script by
# tangle-org-buffer.sh and by hooks/test_tangle_scope.py.

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.tangle_lookup import (  # noqa: E402
    TANGLED_ROOTS,
    unregistered_tangle_outputs,
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check-tangle-scope.py <file.org>", file=sys.stderr)
        return 0  # nothing to check → don't block the tangle
    org = Path(argv[1])
    if org.suffix != ".org" or not org.is_file():
        return 0

    bad = unregistered_tangle_outputs(org)
    if not bad:
        return 0

    roots = ", ".join(TANGLED_ROOTS) if TANGLED_ROOTS else "(none)"
    print(
        f"Refusing to tangle {argv[1]}: {len(bad)} tangle output(s) fall "
        f"outside the registered LP zone "
        f"(LITERATE_AGENT_TANGLED_ROOTS={roots}):",
        file=sys.stderr,
    )
    for b in bad:
        print(f"    {b}", file=sys.stderr)
    print(
        "\nThese paths are editable directly by the team — tangling would "
        "clobber them.\n"
        "Either add the path (or its containing folder) to "
        "LITERATE_AGENT_TANGLED_ROOTS in\n"
        ".claude/hooks/_env.sh to bring it under LP, or fix / remove the "
        ":tangle target.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
