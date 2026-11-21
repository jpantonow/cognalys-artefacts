#!/bin/bash
# 2026-11-16: after arm D (which must run at fd90351, like arm C) ends, fast-forwards the main
# checkout to custo-k1-k2 (the three fixes, 3dee64a) and runs the post-fix rerun (run_fixes.sh,
# 12 runs, US$ 5 ceiling). Untracked files the merge brings are removed only when identical to
# the branch's version; any other difference aborts before anything changes.
W=/mnt/c/Users/Antonow/Documents/tcc_v3
H=$W/outputs/comparison_20261113/cognalys_arm_k3_fixes
mkdir -p "$H/logs"
echo "== $(date -Iseconds) after_d: waiting for arm D to end"
while pgrep -f "[r]un_d_arm_k3_chain.sh" >/dev/null || pgrep -f "[t]p_sp2_then_d.sh" >/dev/null; do sleep 120; done
echo "== $(date -Iseconds) arm D ended: $(tail -1 $W/outputs/comparison_20261113/logs/plus_queue.log)"
while [ -d "$W/outputs/.repository_usage.lock" ]; do echo "   repository lock held; waiting"; sleep 120; done
cd "$W" || exit 1
[ "$(git rev-parse HEAD)" = fd9035152009a60ec54daebc8bd00755c8efb574 ] || { echo "== abort: HEAD is not fd90351"; exit 2; }
git merge-base --is-ancestor HEAD custo-k1-k2 || { echo "== abort: no fast-forward to custo-k1-k2"; exit 2; }
for f in $(git diff --name-only HEAD custo-k1-k2); do
  if [ -e "$f" ] && ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    git show custo-k1-k2:"$f" | cmp -s - "$f" || { echo "== abort: untracked $f differs from custo-k1-k2"; exit 2; }
  fi
done
for f in $(git diff --name-only HEAD custo-k1-k2); do
  if [ -e "$f" ] && ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then rm -- "$f" && echo "   removed identical untracked $f"; fi
done
git merge --ff-only custo-k1-k2 || { echo "== abort: merge failed"; exit 2; }
NEW=$(git rev-parse HEAD)
echo "== $(date -Iseconds) main checkout at $NEW; starting the post-fix rerun"
tr -d '\r' < "$H/run_fixes.sh" > /tmp/run_fixes_k3.sh
EXPECTED_COMMIT=$NEW bash /tmp/run_fixes_k3.sh >> "$H/logs/queue.log" 2>&1
echo "== $(date -Iseconds) post-fix rerun exit $?"
