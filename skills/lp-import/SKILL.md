---
name: lp-import
description: Import a source file (.py/.ts/.rs/.el/…) into an LP `.org` as prose-first, per-definition sections — NEVER by hand-pasting the whole file into one `#+begin_src` block. Use when the user asks to "import <file> into <x>.org", "onboard <file> into LP", "add <file> to the LP layer", "LP-ify <file>", or "create an org section for <file>". Wraps the Emacs `literate-org-import` / `literate-org-resplit-buffer` tools, which auto-split any file over the resplit threshold (120 lines) into one section per top-level def/class with a `:header-args: :tangle` drawer — avoiding the two defects the `warn-oversized-atomic-src.sh` hook flags: an oversized single atomic block, and inline `:tangle` on the `#+begin_src` line. Falls back to stop-and-defer (never hand-author) if Emacs is unreachable.
---

# LP Import

Bring a source file into the LP layer as a **prose-first, per-definition**
`.org` section set. The cardinal rule: **never hand-author the import** by
pasting a whole `.py`/`.ts`/`.rs` into a single `#+begin_src` block. That
reproduces the exact two defects the `warn-oversized-atomic-src.sh`
PreToolUse hook exists to catch:

1. **Oversized atomic block** — a file over the resplit threshold
   (`literate-org-resplit-threshold`, default 120 lines) crammed into one
   block instead of one section per top-level def/class. Violates
   `rules/lp-module-section-hierarchy.md` + `rules/lp-noweb-for-big-blocks.md`.
2. **Inline `:tangle`** — `:tangle <path>` written on the `#+begin_src`
   line instead of in the section's `:header-args:` `:PROPERTIES:` drawer.
   This is non-canonical AND silently breaks the repair tool
   (`literate-org-resplit-block-at-point` errors `No :tangle target`).

The Emacs tooling produces the correct shape automatically. Reach for it
instead of hand-writing.

**Always — regardless of file size.** There is no "small enough to
hand-paste" threshold. A 10-line file goes through `literate-org-import`
(or `literate-org-resplit-buffer` for the repair path) exactly like a
1000-line one. The size-based shortcut ("single-block import for small
files") is forbidden everywhere, including the `lp-resync` new-file
triage: always use the tool, never hand-author, never paste a whole file
into one block.

## Triggers

- "import `<file>` into `<x>.org`" / "onboard `<file>` into LP"
- "add `<file>` to the LP layer" / "LP-ify `<file>`"
- "create an org section for `<file>`"
- Any time you would otherwise paste a whole source file into one
  `#+begin_src` block.

## Two paths

### Path A — fresh import of a file with no existing `.org` section

Use `literate-org-import`. Its signature (verify with the live docstring):

```
(literate-org-import &key LEVEL MODULE-NAME MODULE-PATH)
```

It reads the source at `MODULE-PATH`, tiles it via `literate-org--file-units`
(one section per top-level definition over the chunk floor, smaller ones
grouped), noweb-splits any big class via `literate-org-split-node`, and
emits each section with a `:header-args: :tangle` drawer.

Invoke through the host Emacs (precedent: `hooks/tangle-org-buffer.sh`):

```bash
emacsclient -e '(with-current-buffer (find-file-noselect "<lp/.../x.org>")
                  (goto-char (point-max))
                  (literate-org-import :module-name "<pkg.mod>"
                                       :module-path "<abs/path/to/source>")
                  (save-buffer))'
```

Two required fix-ups (the proposal's open risks):

1. **Tangle path must be LP-relative, not absolute.** `literate-org-import`
   writes the module path as given. After import, confirm each new
   section's `:header-args: :tangle` is a path *relative to the `.org`
   file* (e.g. `../../repos/<sub>/<pkg>/<mod>.py`). An absolute path
   breaks byte-equivalence and the `.cache/tangle-map.tsv` reverse map —
   rewrite it if needed.
2. **Set the language first.** If the heading lacks `LITERATE_ORG_LANGUAGE`,
   `emacsclient` may block on a minibuffer prompt and freeze single-threaded
   Emacs. Set it from the file extension (`python`/`typescript`/`rust`/…)
   on the parent heading before the call, or pass it through the import.

### Path B — a single atomic block already exists (repair)

If the file is already in the `.org` as ONE oversized block (the defect
state), restructure in place — this is the path verified this session on
`lp/experiments/langsmith.org`:

1. Move the inline `:tangle … :mkdirp yes` off the `#+begin_src` line into
   the section's `:PROPERTIES:` drawer as
   `:header-args: :tangle … :mkdirp yes`; make the `#+begin_src` line bare.
   (Required — else step 2 errors `No :tangle target`.)
2. With point inside the block, run `literate-org-resplit-block-at-point`
   (single block) or `literate-org-resplit-buffer` (every >threshold block
   in the file). Both flat-split top-level defs and noweb-split big classes.

```bash
emacsclient -e '(with-current-buffer (find-file-noselect "<lp/.../x.org>")
                  (literate-org-resplit-buffer)
                  (save-buffer))'
```

## Verify (mandatory — byte-equivalence gate)

After either path, re-tangle and confirm the output is byte-equivalent to
the pre-import source. Org-Babel trims inter-block blank lines, so the only
acceptable diff is blank-line spacing (restore with the project's formatter
if one is wired into tangle; otherwise the tighter spacing is acceptable —
see `rules/lp-noweb-for-big-blocks.md`).

```bash
make tangle FILE=<lp/.../x.org> NO_POST_FORMAT=1
git -C <source-root> diff --stat        # content unchanged; blank-line-only OK
# or, for non-submodule targets: ruff/prettier-normalise both sides and diff
python3 -m py_compile <tangled.py>      # still compiles
```

Then refresh metadata if the project uses the resync schema:
`python3 scripts/lp_metadata_refresh.py <lp/.../x.org>`.

## Fallback — no Emacs reachable

If `emacsclient` cannot reach a running Emacs (headless CI, no server),
**do NOT hand-author the single-block import.** Stop and defer: leave the
source un-imported, emit a TODO naming the file + target `.org`, and let
the `warn-oversized-atomic-src.sh` hook / `make check-structure` keep the
un-imported file visible. Hand-authoring is exactly the failure this skill
exists to prevent.

## See also

- `rules/lp-noweb-for-big-blocks.md` — flat-split vs noweb decision + the
  `:header-args: :noweb-ref` mechanics.
- `rules/lp-module-section-hierarchy.md` — the per-definition section shape.
- `hooks/warn-oversized-atomic-src.sh` — the PreToolUse hook that warns
  when an edit newly introduces the defect this skill prevents.
