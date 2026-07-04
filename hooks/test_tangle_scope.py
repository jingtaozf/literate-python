#!/usr/bin/env python3
"""Unit + E2E tests for the tangle-output registration gate.

The gate answers the dual of the edit-block: "may a tangle WRITE to this
output path?"  Only outputs inside a registered LP zone
(LITERATE_AGENT_TANGLED_ROOTS) may be written; anything else is a
de-registered tree the team edits directly, and tangling there would clobber
their source.  These tests pin:

- the pure predicate (``tangle_output_allowed``) and the .org extractor
  (``unregistered_tangle_outputs``) in-process, with explicit env so the
  cases are deterministic regardless of the test runner's environment;
- the ``check-tangle-scope.py`` CLI end-to-end (exit 0 clean / exit 2 offender);
- the ``tangle-org-buffer.sh`` PostToolUse hook end-to-end — a real payload
  through the real hook refuses (exit 2) when an output escapes the registry.

Run from anywhere: ``python3 hooks/test_tangle_scope.py``
Exit 0 on all-pass, non-zero on any failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.tangle_lookup import (  # noqa: E402
    tangle_output_allowed,
    unregistered_tangle_outputs,
)

CHECK_CLI = HOOKS_DIR / "check-tangle-scope.py"
TANGLE_HOOK = HOOKS_DIR / "tangle-org-buffer.sh"

PASS = 0
FAIL = 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        print(f"  ✓ {label:44s} {got!r}")
        PASS += 1
    else:
        print(f"  ✗ {label:44s} want={want!r} got={got!r}")
        FAIL += 1


def _make_project(tmp: Path) -> tuple[Path, Path, Path]:
    """Fake project: an .org under lp/foo/ that tangles one output INSIDE the
    registered zone (repos/) and — for the offender variant — one OUTSIDE it.
    Returns (project_root, clean_org, offender_org)."""
    proj = tmp / "proj"
    (proj / "lp" / "foo").mkdir(parents=True, exist_ok=True)
    (proj / "repos" / "foo").mkdir(parents=True, exist_ok=True)
    (proj / "elsewhere").mkdir(parents=True, exist_ok=True)

    clean = proj / "lp" / "foo" / "clean.org"
    clean.write_text(
        textwrap.dedent(
            """\
            * A registered module
            #+begin_src python :tangle ../../repos/foo/a.py
            x = 1
            #+end_src
            """
        )
    )
    offender = proj / "lp" / "foo" / "offender.org"
    offender.write_text(
        textwrap.dedent(
            """\
            * Half in, half out
            #+begin_src python :tangle ../../repos/foo/a.py
            x = 1
            #+end_src
            * This one escapes the registry
            #+begin_src python :tangle ../../elsewhere/b.py
            y = 2
            #+end_src
            """
        )
    )
    return proj, clean, offender


def run_unit(tmp: Path) -> None:
    proj, clean, offender = _make_project(tmp)
    env_repos = {"LITERATE_AGENT_TANGLED_ROOTS": "repos/"}
    env_empty = {"LITERATE_AGENT_TANGLED_ROOTS": ""}

    print("── predicate: tangle_output_allowed ──")
    check(
        "under registered root -> allowed",
        tangle_output_allowed(
            str(proj / "repos" / "foo" / "a.py"),
            project_root=proj,
            env=env_repos,
        ),
        True,
    )
    check(
        "outside registry -> refused",
        tangle_output_allowed(
            str(proj / "elsewhere" / "b.py"),
            project_root=proj,
            env=env_repos,
        ),
        False,
    )
    check(
        "empty roots -> whole repo is zone (legacy no-op)",
        tangle_output_allowed(
            str(proj / "elsewhere" / "b.py"),
            project_root=proj,
            env=env_empty,
        ),
        True,
    )
    check(
        "exact-file registry entry matches only itself",
        tangle_output_allowed(
            str(proj / "repos" / "foo" / "a.py"),
            project_root=proj,
            env={"LITERATE_AGENT_TANGLED_ROOTS": "repos/foo/a.py"},
        ),
        True,
    )
    check(
        "sibling of exact-file entry -> refused",
        tangle_output_allowed(
            str(proj / "repos" / "foo" / "other.py"),
            project_root=proj,
            env={"LITERATE_AGENT_TANGLED_ROOTS": "repos/foo/a.py"},
        ),
        False,
    )

    print("── extractor: unregistered_tangle_outputs ──")
    check(
        "clean .org -> no offenders",
        unregistered_tangle_outputs(clean, project_root=proj, env=env_repos),
        [],
    )
    check(
        "offender .org -> names only the escaping output",
        unregistered_tangle_outputs(offender, project_root=proj, env=env_repos),
        ["elsewhere/b.py"],
    )


def _run(cmd: list[str], *, stdin: str = "", extra_env: dict[str, str]) -> int:
    import os

    env = {**os.environ, **extra_env}
    return subprocess.run(
        cmd, input=stdin, env=env, capture_output=True, text=True
    ).returncode


def run_e2e(tmp: Path) -> None:
    proj, clean, offender = _make_project(tmp)
    env = {"CLAUDE_PROJECT_DIR": str(proj), "LITERATE_AGENT_TANGLED_ROOTS": "repos/"}

    print("── E2E: check-tangle-scope.py CLI ──")
    check(
        "clean .org -> exit 0",
        _run([sys.executable, str(CHECK_CLI), str(clean)], extra_env=env),
        0,
    )
    check(
        "offender .org -> exit 2",
        _run([sys.executable, str(CHECK_CLI), str(offender)], extra_env=env),
        2,
    )

    print("── E2E: tangle-org-buffer.sh PostToolUse hook ──")
    payload = json.dumps(
        {"tool_name": "Edit", "tool_input": {"file_path": str(offender)}}
    )
    check(
        "offender payload + LP_AUTO_TANGLE=1 -> hook refuses (exit 2)",
        _run(
            ["bash", str(TANGLE_HOOK)],
            stdin=payload,
            extra_env={**env, "LP_AUTO_TANGLE": "1"},
        ),
        2,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        run_unit(Path(td))
    with tempfile.TemporaryDirectory() as td:
        run_e2e(Path(td))
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
