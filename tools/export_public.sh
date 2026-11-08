#!/usr/bin/env bash
# Public snapshot ("Heimdalur") of HEAD: `git archive` leaves out every path marked
# export-ignore in .gitattributes (internal working notes). The snapshot is refused if
# any line of the denylist (case-insensitive ERE, one per line) matches a file's text
# or path. The denylist is local and never committed: tools/.public-denylist, or
# $HEIMDAL_PUBLIC_DENYLIST.
# Usage: tools/export_public.sh <new-dir>   (exports HEAD; uncommitted changes are not included)
set -euo pipefail
out=$(realpath -m "${1:?usage: tools/export_public.sh <new-dir>}")
cd "$(git rev-parse --show-toplevel)"
deny=$(realpath -m "${HEIMDAL_PUBLIC_DENYLIST:-tools/.public-denylist}")
[ -s "$deny" ] || { echo "export_public: denylist missing or empty: $deny" >&2; exit 2; }
[ ! -e "$out" ] || { echo "export_public: $out already exists" >&2; exit 2; }

mkdir -p "$out"
git archive HEAD | tar -x -C "$out"

set +e
text_hits=$(grep -rIl -i -E -f "$deny" "$out"); t=$?
path_hits=$(cd "$out" && find . | grep -i -E -f "$deny"); p=$?
set -e
# grep: 0 = match, 1 = no match, >1 = error. Only "no match" on both passes.
if [ "$t" -ne 1 ] || [ "$p" -ne 1 ]; then
    echo "export_public: REFUSED (grep exit $t/$p). Denylisted text or paths in:" >&2
    printf '%s\n' $text_hits $path_hits >&2
    exit 1
fi
echo "export_public: OK, HEAD $(git rev-parse --short HEAD), $(find "$out" -type f | wc -l) files in $out"
