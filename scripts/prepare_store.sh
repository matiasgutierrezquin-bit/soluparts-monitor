#!/usr/bin/env bash
set -euo pipefail
monitor_store="${RUNNER_TEMP:?}/soluparts-data"
if git ls-remote --exit-code --heads origin refs/heads/monitor-data >/dev/null; then
  git fetch --no-tags origin refs/heads/monitor-data:refs/remotes/origin/monitor-data
  git worktree add --detach "$monitor_store" refs/remotes/origin/monitor-data
else
  monitor_status=$?
  if [ "$monitor_status" -ne 2 ]; then
    exit "$monitor_status"
  fi
  git worktree add --detach "$monitor_store" HEAD
  git -C "$monitor_store" switch --orphan monitor-data
fi
