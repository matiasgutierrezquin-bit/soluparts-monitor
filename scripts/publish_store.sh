#!/usr/bin/env bash
set -euo pipefail
monitor_store="${RUNNER_TEMP:?}/soluparts-data"
git -C "$monitor_store" config user.name 'github-actions[bot]'
git -C "$monitor_store" config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git -C "$monitor_store" add -A
if ! git -C "$monitor_store" diff --cached --quiet; then
  git -C "$monitor_store" commit -m "Record public website check ${GITHUB_RUN_ID:-local}"
  git -C "$monitor_store" push origin HEAD:refs/heads/monitor-data
fi
