"""Regenerate the LP README entry points across <meta-repo>.

Three artefacts in one script:

  1. Root ``README.org`` — the human-facing front door.
  2. ``lp/<group>/README.org`` for every group under ``lp/`` — per-
     submodule entry with Why / What / How files relate / Read order /
     full index.
  3. (Already lives in ``scripts/build_index.py``; this script does NOT
     touch ``lp/INDEX.org`` — invoke that script separately.)

Per-group narrative metadata is held in ``GROUP_NARRATIVE`` below. To
adjust an elevator pitch, file-role mapping, or read-order: edit the
metadata dict and re-run. The per-file alphabetical index at the bottom
of each README is auto-rebuilt from each .org file's ``#+TITLE``.

The root README's table of submodules is grouped into three buckets
declared in ``ROOT_BUCKETS`` so a senior engineer can scan by role.

Usage:
    uv run python scripts/build_overviews.py            # rebuild all
    uv run python scripts/build_overviews.py --check    # diff vs disk, exit 1 if drift

Companion: ``scripts/build_index.py`` for ``lp/INDEX.org``, and
``scripts/audit_lp.py`` for the file-level A-grade audit.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path
from textwrap import dedent

# Sibling-import load_config so rendered output can expand
# ${PROJECT_NAMESPACE} etc. from the consumer's .literate-agent/config.toml.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from load_config import load_config, expand_placeholders  # type: ignore
except ImportError:
    # Fallback no-op if load_config not importable (e.g. running
    # detached from literate-agent/scripts/). Placeholders stay literal.
    def load_config():  # type: ignore[misc]
        return {}

    def expand_placeholders(text: str, cfg=None) -> str:  # type: ignore[misc]
        return text


REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_lp_root() -> Path:
    """Resolve the LP root, honouring consumer-repo overrides.

    Resolution order (first hit wins):

      1. ``LP_ROOT`` env var (absolute path to ``<consumer-repo>/lp/``).
      2. ``LITERATE_AGENT_LP_ROOT`` env var, joined against ``$PWD`` so
         consumers can configure it relative to their own checkout
         (this is the form set by ``.claude/hooks/_env.sh`` in
         edo-literate-style meta-repos).
      3. ``$PWD/lp`` — the most common consumer-repo layout.
      4. ``<this-script's parent's parent>/lp`` — the legacy fallback
         when build_overviews.py is run inside its own repo (literate-
         agent's own tests etc.). Effectively the historical default.
    """
    import os

    raw = os.environ.get("LP_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    rel = os.environ.get("LITERATE_AGENT_LP_ROOT")
    if rel:
        return (Path.cwd() / rel).resolve()
    if (Path.cwd() / "lp").is_dir():
        return (Path.cwd() / "lp").resolve()
    return REPO_ROOT / "lp"


LP_ROOT = _resolve_lp_root()

# ────────────────────────────────────────────────────────────────────
# Per-group narrative metadata — hand-curated.
#
# Each entry has:
#   elevator    — one-paragraph "what this submodule is" (10-20 lines).
#   groups      — [(label, role)] conceptual grouping for "How files
#                 relate"; empty list ⇒ single-file submodule.
#   read_order  — bulleted reading sequence for a senior engineer
#                 landing on the README.
#
# To add a new submodule: append a new key here. The renderer will
# pick it up. (lp/<new-group>/ also needs to exist on disk.)
# ────────────────────────────────────────────────────────────────────
# Per-group narrative metadata — loaded from TOML, never hardcoded.
#
# ``GROUP_NARRATIVE`` maps each submodule name to a dict with three
# keys consumed by ``render_group_readme``:
#
#   elevator    — one-paragraph "what this submodule is" (10-20 lines).
#   groups      — [[label, role], ...] for "How files relate"; empty
#                 list ⇒ single-file submodule.
#   read_order  — [step, ...] reading sequence for a senior engineer
#                 landing on the README.
#
# Resolution mirrors ``_load_buckets`` below — same TOML file, same
# discovery order. Schema:
#
#     [groups.<submodule-name>]
#     elevator = "..."
#     groups = [["label", "role"], ...]
#     read_order = ["step 1", "step 2"]
#
# If the TOML file is absent (or has no ``[groups.*]`` tables), the
# narrative dict is empty and the renderer emits a degraded shell —
# bucket tables stay empty and per-group READMEs are not regenerated.
# That is intentional: literate-agent ships ZERO project-specific
# prose. Every consumer repo curates its own.
# ────────────────────────────────────────────────────────────────────


def _load_narrative() -> dict[str, dict]:
    """Load per-group narrative from the same TOML as ``_load_buckets``."""
    import os

    candidates: list[Path] = []
    env = os.environ.get("LITERATE_AGENT_BUCKETS_TOML")
    if env:
        candidates.append(Path(env))
    lp_root = os.environ.get("LP_ROOT")
    if lp_root:
        candidates.append(Path(lp_root) / ".literate-agent" / "buckets.toml")
    candidates.append(Path.cwd() / ".literate-agent" / "buckets.toml")

    for p in candidates:
        if p.is_file():
            try:
                import tomllib
            except ImportError:  # pragma: no cover
                import tomli as tomllib  # type: ignore[no-redef]
            data = tomllib.loads(p.read_text(encoding="utf-8"))
            groups = data.get("groups", {})
            # Normalise: convert [[label, role]] arrays to tuples
            out: dict[str, dict] = {}
            for grp, info in groups.items():
                out[grp] = {
                    "elevator": info.get("elevator", ""),
                    "groups": [
                        tuple(g) if isinstance(g, (list, tuple)) else (g, "")
                        for g in info.get("groups", [])
                    ],
                    "read_order": list(info.get("read_order", [])),
                }
            return out
    return {}


GROUP_NARRATIVE: dict[str, dict] = _load_narrative()

# ────────────────────────────────────────────────────────────────────
# Root README — three buckets group the 14 submodules by role.
# ────────────────────────────────────────────────────────────────────

def _load_buckets() -> list[tuple[str, list[str]]]:
    """Load the (bucket-name, [submodule-names]) list that drives root README
    grouping.

    Resolution order (first one found wins):

      1. ``$LITERATE_AGENT_BUCKETS_TOML`` — explicit override env var pointing
         at a TOML file with ``[[buckets]]`` tables.
      2. ``$LP_ROOT/.literate-agent/buckets.toml`` — project-local config
         (per the LP_ROOT env var the rest of the build scripts already read).
      3. ``./.literate-agent/buckets.toml`` (cwd) — fallback if LP_ROOT unset.
      4. Empty list — the script then falls back to a flat alphabetical scan
         of ``lp/*/`` directories. No hardcoded project names.

    TOML schema:

        [[buckets]]
        name = "My applications"
        members = ["project-a", "project-b"]

        [[buckets]]
        name = "Shared platform"
        members = ["python-foundation"]

    This is intentionally project-neutral. literate-agent ships NO default
    bucket list — every consumer repo defines its own membership.
    """
    import os

    candidates: list[Path] = []
    env = os.environ.get("LITERATE_AGENT_BUCKETS_TOML")
    if env:
        candidates.append(Path(env))
    lp_root = os.environ.get("LP_ROOT")
    if lp_root:
        candidates.append(Path(lp_root) / ".literate-agent" / "buckets.toml")
    candidates.append(Path.cwd() / ".literate-agent" / "buckets.toml")

    for p in candidates:
        if p.is_file():
            try:
                import tomllib  # Python 3.11+
            except ImportError:  # pragma: no cover
                import tomli as tomllib  # type: ignore[no-redef]
            data = tomllib.loads(p.read_text(encoding="utf-8"))
            return [
                (b["name"], list(b.get("members", [])))
                for b in data.get("buckets", [])
            ]
    return []


ROOT_BUCKETS = _load_buckets()


# ────────────────────────────────────────────────────────────────────
# Renderers
# ────────────────────────────────────────────────────────────────────


def _read_title(f: Path) -> str:
    for line in f.read_text().splitlines()[:20]:
        m = re.match(r"^#\+TITLE:\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return ""


def collect_lp_files(grp_dir: Path) -> list[tuple[str, str]]:
    """Return [(filename, #+TITLE)] for every .org in the group dir
    (recursing into a single ``tests/`` subdir for <scout-server>).

    Excludes ``_project.org`` (the file we're rendering — would self-
    reference) and other underscore-prefixed scratch files. README.org
    is INCLUDED so it shows up first in the rendered index — that is
    the hand-curated entry point readers should land on."""
    out: list[tuple[str, str]] = []
    for f in sorted(grp_dir.iterdir()):
        if f.is_dir() and f.name == "tests":
            for sub in sorted(f.iterdir()):
                if (sub.name.startswith(("#", "_"))
                        or sub.suffix != ".org"
                        or sub.name.endswith("~")):
                    continue
                out.append((f"tests/{sub.name}", _read_title(sub)))
            continue
        if (f.name.startswith(("#", "_"))
                or f.suffix != ".org"
                or f.name.endswith("~")):
            continue
        out.append((f.name, _read_title(f)))
    return out


def render_group_project(grp: str, info: dict, lp_files: list[tuple[str, str]]) -> str:
    """Render `lp/<grp>/_project.org` — the script-managed thin overview.

    This is the file `build_overviews.py` owns end-to-end (fully
    rewritten on every run). `lp/<grp>/README.org` is intentionally
    NOT managed by the script — see `render_group_readme_stub`.
    """
    elevator = info["elevator"]
    groups = info["groups"]
    read_order = info["read_order"]

    if groups:
        how_block = "\n\n".join(f"- {label} — {role}" for label, role in groups)
    else:
        how_block = "(single file — see the index below.)"

    read_block = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(read_order))

    index_lines = []
    for fname, ftitle in lp_files:
        if ftitle:
            index_lines.append(f"- [[file:{fname}][{fname}]] — {ftitle}")
        else:
            index_lines.append(f"- [[file:{fname}][{fname}]]")
    index_block = "\n".join(index_lines) if index_lines else "(empty)"

    return f"""\
# -*- Mode: POLY-ORG; indent-tabs-mode: nil;  -*- ---
#+TITLE: {grp} — project overview
#+OPTIONS: tex:verbatim toc:nil \\n:nil @:t ::t |:t ^:nil -:t f:t *:t <:t
#+STARTUP: noindent

* Why this file exists

Script-managed *project overview* for ``lp/{grp}/``. The senior
engineer's reading entry is [[file:README.org][=README.org=]] (rich,
hand-curated). This file holds the auto-regenerated short form: a
one-paragraph elevator, the file-role map, a recommended read order,
and the LP-files index. Every run of ``make build-overviews``
rewrites this file from scratch — do not hand-edit. To update the
narrative, edit ``[groups.{grp}]`` in
``.literate-agent/buckets.toml`` and re-run the script.

* What this submodule is

{elevator}

* How files relate

{how_block}

* Read order for newcomers

{read_block}

* LP files (full index)

Auto-generated from each ``.org``'s ``#+TITLE``. Regenerate via
``scripts/build_overviews.py``.

{index_block}
"""


def render_group_readme_stub(grp: str, info: dict) -> str:
    """Render the *bootstrap* README.org — only written when missing.

    Provides a minimal stub pointing at `_project.org`; the consumer
    is expected to fill in rich, hand-curated content here. The
    script never overwrites an existing README.org.
    """
    elevator = info["elevator"]
    return f"""\
# -*- Mode: POLY-ORG; indent-tabs-mode: nil;  -*- ---
#+TITLE: {grp}
#+OPTIONS: tex:verbatim toc:nil \\n:nil @:t ::t |:t ^:nil -:t f:t *:t <:t
#+STARTUP: noindent

* {grp}

{elevator}

This README is *hand-maintained* — fill it with the rich narrative
that survives the script. The auto-generated short form (elevator
+ file-role map + read order + LP-files index) lives in
[[file:_project.org][=_project.org=]]; ``make build-overviews``
regenerates that file on every run but never touches this one.

See also:

- [[file:_project.org][=_project.org=]] — script-managed project overview.
"""


def render_root_readme() -> str:
    """Render the root README.org with three-bucket submodule table."""
    bucket_blocks = []
    for bucket_name, members in ROOT_BUCKETS:
        rows = []
        for grp in members:
            if grp not in GROUP_NARRATIVE:
                continue
            # First sentence of elevator → root-table cell
            first = GROUP_NARRATIVE[grp]["elevator"].split(". ")[0].rstrip(".")
            # Strip the leading =grp= label if it duplicates the row name
            first = re.sub(rf"^=*{re.escape(grp)}=*\s+(?:is\s+)?", "", first)
            first = first[:1].upper() + first[1:] if first else ""
            rows.append(
                f"| [[file:lp/{grp}/_project.org][={grp}=]] | {first} |"
            )
        bucket_blocks.append(
            f"** {bucket_name}\n\n"
            "| Submodule | Elevator |\n"
            "|-----------+----------|\n"
            + "\n".join(rows)
        )
    submodules_section = "\n\n".join(bucket_blocks)

    n = sum(len(m) for _, m in ROOT_BUCKETS)
    return f"""\
# -*- Mode: POLY-ORG; indent-tabs-mode: nil;  -*- ---
#+TITLE: literate-programming meta-repo
#+OPTIONS: tex:verbatim toc:nil \\n:nil @:t ::t |:t ^:nil -:t f:t *:t <:t
#+STARTUP: noindent

* Why this file exists

Front door of this literate-programming meta-repo. A senior
engineer landing here should be able to leave with three things
settled: (1) what this repo is, (2) which submodule owns the
problem they came for, and (3) where to read next.

This README is auto-generated by
=$LITERATE_AGENT_HOME/scripts/build_overviews.py= from the bucket
+ narrative config at =.literate-agent/buckets.toml=. Hand-edits
will be overwritten on the next ``make build-readme``.

* What this is

A *literate-programming meta-repo*. Production source for each
submodule under =repos/= is authored prose-first as Org-mode under
=lp/<sub>/*.org=, then tangled back into the matching submodule's
source files. Read the .org for the *why*; the tangled file
exists for tooling that doesn't speak Org.

* How submodules relate

{n} submodules configured (see =.literate-agent/buckets.toml=):

{submodules_section if submodules_section else "_(no buckets configured yet — populate `.literate-agent/buckets.toml`.)_"}

* Read order for newcomers

1. *This file* — front-door overview.
2. =CLAUDE.md= — the agent-only layer at the root.
3. =lp/INDEX.org= — auto-generated catalogue.
4. =lp/<group>/README.org= — per-submodule entry.
5. =lp/<group>/<file>.org= — individual literate source; tangles
   to =repos/<group>/<…>=. Edit *here*, never the tangled file.

* Top-level Org files

- [[file:lp/INDEX.org][lp/INDEX.org]] — auto-generated cross-submodule index. Regenerate
  via ``make build-index``.
- [[file:lp/draft.org][lp/draft.org]] — open design proposals queue (if used).
- [[file:lp/decisions-log.org][lp/decisions-log.org]] — accepted / rejected design proposals +
  research notes (if used).

* Scoping LP to a registered subset

By default every matching-extension source file under
=LITERATE_AGENT_TANGLED_ROOTS= is LP-managed: edits are blocked (edit
the owning .org instead) and the file is tangled from that .org. In a
repo where teammates also work directly on the submodules, managing
*all* source prose-first is too heavy. Narrow =TANGLED_ROOTS= so it
names only the folders — or exact files — you want under LP. That list
*is* the registry.

Two questions, two predicates:

- *May the agent edit this source?* — blocked if the path is under
  =TANGLED_ROOTS= *or* is a known tangle target in
  =.cache/tangle-map.tsv= (the reverse-map backstop).
- *May a tangle write to this output?* — allowed only if the path is
  under =TANGLED_ROOTS=. Net-A-only on purpose: the reverse-map is
  derived from the .org's own =:tangle= lines, so testing an output
  against it would be circular.

The tangle gate is the clobber guard. Before an auto-tangle
(=LP_AUTO_TANGLE=1=), =hooks/check-tangle-scope.py= refuses any .org
whose =:tangle= output escapes the registry — otherwise the tangle
would overwrite a file the team edits directly. Invariant: every kept
.org tangles only into registered zones. Empty =TANGLED_ROOTS= means
the whole repo is the zone (single-repo default) and the gate is a
no-op.

#+begin_src bash
# .claude/hooks/_env.sh — TANGLED_ROOTS entries are folder prefixes and/or
# exact file paths; only these are edit-blocked AND tangle-writable.
export LITERATE_AGENT_TANGLED_ROOTS="repos/mega-code/,repos/pi/pi/config.py"
#+end_src

* Build + lint commands

These targets are provided by ``templates/Makefile.lp.mk`` from
=literate-agent=. Drop the snippet into your project's =Makefile=:

#+begin_src bash
include $(HOME)/projects/literate-agent/templates/Makefile.lp.mk
#+end_src

Then:

#+begin_src bash
make tangle FILE=lp/<sub>/<x>.org    # tangle one .org back to repos/<sub>/
make tangle-all                       # every non-underscore lp/**/*.org

make check-structure                  # depth ≤ 5, prose-before-src
make build-index                      # regenerate lp/INDEX.org
make build-readme                     # regenerate this file + lp/<group>/README.org
make build-tangle-map                 # refresh .cache/tangle-map.tsv
#+end_src
"""


# ────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────


def _write_or_check(path: Path, content: str, check_only: bool) -> bool:
    """Return True if content matches disk (or was written)."""
    if check_only:
        existing = path.read_text() if path.is_file() else ""
        if existing == content:
            return True
        print(f"DRIFT: {path.relative_to(LP_ROOT.parent)}")
        diff = difflib.unified_diff(
            existing.splitlines(), content.splitlines(),
            fromfile=f"a/{path.name}", tofile=f"b/{path.name}", lineterm="",
        )
        for line in list(diff)[:30]:
            print(f"    {line}")
        return False
    path.write_text(content)
    print(f"wrote: {path.relative_to(LP_ROOT.parent)}")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write; exit 1 if any README would change.",
    )
    args = parser.parse_args(argv[1:])

    ok = True
    # Discover consumer config; placeholders in rendered output expand against it.
    cfg = load_config()

    # Per-group artefacts:
    #   - _project.org — always rewritten (script-owned).
    #   - README.org   — bootstrap-only; never overwritten if it exists.
    for grp, info in sorted(GROUP_NARRATIVE.items()):
        grp_dir = LP_ROOT / grp
        if not grp_dir.is_dir():
            print(f"SKIP (missing): {grp}")
            continue
        lp_files = collect_lp_files(grp_dir)
        project_content = expand_placeholders(
            render_group_project(grp, info, lp_files), cfg
        )
        ok &= _write_or_check(grp_dir / "_project.org", project_content, args.check)

        # README bootstrap — write a thin stub ONLY if README.org is missing.
        # Existing READMEs (hand-curated rich content) are left untouched.
        readme_path = grp_dir / "README.org"
        if not readme_path.exists():
            stub = expand_placeholders(render_group_readme_stub(grp, info), cfg)
            ok &= _write_or_check(readme_path, stub, args.check)
            if not args.check:
                print(f"BOOTSTRAP: {readme_path.relative_to(LP_ROOT.parent)}")

    # Root README — only auto-generate for meta-repo shape. Plugin-consumer
    # and single-repo-lp consumers maintain their READMEs by hand.
    shape = cfg.get("SHAPE", "plugin-consumer")
    if shape == "meta-repo":
        ok &= _write_or_check(
            LP_ROOT.parent / "README.org",
            expand_placeholders(render_root_readme(), cfg),
            args.check,
        )
    else:
        print(f"SKIP root README (shape={shape!r}; only meta-repo gets auto-rendered)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
