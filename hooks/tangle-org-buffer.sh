#!/usr/bin/env bash
# PostToolUse hook: tangle a project .org file after Claude edits it.
#
# Responsibility:
#
#   Tangle — call ``(literate-org-tangle-by-path ...)`` via
#   emacsclient (preferred) or ``make tangle`` (batch fallback).
#   Both end at ``org-babel-tangle-file``. Gated by
#   ``LP_AUTO_TANGLE=1`` until each migrated submodule's
#   ``make format`` post-tangle integration is verified byte-
#   equivalent.
#
# Escape hatches:
#   * LITERATE_ORG_NO_AUTO_TANGLE=1 → skip tangle entirely.
#   * LP_AUTO_TANGLE unset → skip tangle (default).
#
# Note: TOC regen via ``(toc-org-insert-toc)`` was removed (2026-05-28).
# GitHub auto-renders an org TOC sidebar, and consumer repos no longer
# maintain manual ``:noexport:TOC:`` blocks. See rules/lp-module-
# section-hierarchy.md for the rule that replaced it.

set -euo pipefail

payload=$(cat)
read -r tool_name file_path <<<"$(python3 - "$payload" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
print(d.get("tool_name", ""), d.get("tool_input", {}).get("file_path", "") or "")
PY
)"

[ "${LITERATE_ORG_NO_AUTO_TANGLE:-0}" = "1" ] && exit 0

case "$tool_name" in Edit|Write|MultiEdit) ;; *) exit 0 ;; esac
case "$file_path" in *.org) ;; *) exit 0 ;; esac

cd "$CLAUDE_PROJECT_DIR"

# ── Tangle (gated by LP_AUTO_TANGLE) ───────────────────────────────────
[ "${LP_AUTO_TANGLE:-0}" = "1" ] || exit 0

# ── Registration gate: never tangle into a de-registered (team-owned) tree ──
# If this .org declares any :tangle output outside LITERATE_AGENT_TANGLED_ROOTS,
# refuse — a tangle there would clobber source the team edits directly.
# check-tangle-scope.py prints the offenders + remediation to stderr.
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
if ! python3 "$HOOK_DIR/check-tangle-scope.py" "$file_path" >&2; then
  exit 2
fi

# Try host Emacs first.
if emacsclient -e "(literate-org-tangle-by-path \"$file_path\")" >/dev/null 2>&1; then
  echo "✓ tangled $file_path (via host Emacs)"
  exit 0
fi

# Fall back to batch tangle.
if make tangle FILE="$file_path" >/dev/null 2>&1; then
  echo "✓ tangled $file_path (batch fallback)"
  exit 0
fi

echo >&2 "tangle failed for $file_path"
echo >&2 "  - host Emacs unreachable (emacsclient -e errored)"
echo >&2 "  - batch fallback (make tangle FILE=$file_path) also failed"
echo >&2 "  set LITERATE_ORG_NO_AUTO_TANGLE=1 to bypass auto-tangle"
exit 2
