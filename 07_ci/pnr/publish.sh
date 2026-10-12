#!/bin/bash
# publish.sh -- one block's logs into the branch ci-logs-pnr (drop 0.42, stream A2b). The other jobs each force-push an
# orphan commit of their ci_logs/<job>/ to ci-logs-<job>; the pnr job runs its blocks in parallel, so pnr-plan starts
# the run's branch that way (ci_logs/pnr/RUN.txt alone) and every block adds its own directory on top: fetch the branch,
# replace the block's directory in a worktree, commit, push -- a push that loses the race to another block fetches
# again and retries (each block owns its directory: nothing to merge). Under flock, so a job's background publisher
# and its final publish never overlap.
# usage: publish.sh <src dir> <path in the branch> <message>                      one publish
#        publish.sh --loop <seconds> <src dir> <path in the branch> <message>     every <seconds>, until killed
# PUBLISH_BRANCH (default ci-logs-pnr), PUBLISH_LOCK (default .ci_publish.lock); run from the repository checkout.
set -u
BR=${PUBLISH_BRANCH:-ci-logs-pnr}
LOCK=${PUBLISH_LOCK:-.ci_publish.lock}
if [ "${1:-}" = "--loop" ]; then
  every=$2
  shift 2
  while true; do
    sleep "$every"
    bash "$0" "$@" || true
  done
fi
[ $# -eq 3 ] || { echo "usage: publish.sh [--loop <seconds>] <src dir> <path in the branch> <message>"; exit 2; }
SRC=$1
DEST=$2
MSG=$3
[ -d "$SRC" ] || { echo "publish: no $SRC"; exit 1; }
exec 9> "$LOCK"
flock -w 600 9 || { echo "publish: lock $LOCK busy"; exit 1; }
WT="${RUNNER_TEMP:-/tmp}/pnr-publish-$$"
cleanup() {
  git worktree remove --force "$WT" > /dev/null 2>&1
  rm -rf "$WT"
  git worktree prune
}
ok=1
for i in 1 2 3 4 5 6 7 8 9 10; do
  cleanup
  if git fetch -q origin "+refs/heads/$BR:refs/remotes/origin/$BR" 2> /dev/null; then
    git worktree add -q --detach "$WT" "refs/remotes/origin/$BR" || break
  else                                              # no branch yet: an orphan commit with this directory alone
    git worktree add -q --detach "$WT" HEAD || break
    git -C "$WT" checkout -q --orphan "pnr-publish-$$" && git -C "$WT" rm -rfq --cached . > /dev/null && git -C "$WT" clean -fdxq
  fi
  rm -rf "${WT:?}/$DEST"
  mkdir -p "$WT/$DEST"
  cp -r "$SRC"/. "$WT/$DEST"/
  git -C "$WT" add -A -f "$DEST"
  if git -C "$WT" diff --cached --quiet; then
    echo "publish: $DEST unchanged on $BR"
    ok=0
    break
  fi
  git -C "$WT" -c user.name=neo-ci -c user.email=ci@users.noreply.github.com commit -q -m "$MSG"
  if git -C "$WT" push -q origin "HEAD:refs/heads/$BR"; then
    echo "publish: $DEST -> $BR $(git -C "$WT" rev-parse --short HEAD) (attempt $i)"
    ok=0
    break
  fi
  sleep $((RANDOM % 20 + 5))
done
cleanup
exit $ok
