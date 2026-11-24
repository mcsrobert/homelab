#!/usr/bin/env bash
set -euo pipefail

base=$1
cd "$(dirname "$0")/../.."
baseline=containers/t3-code/baseline-tools.py

# Rebuild if the base commit is missing, e.g. after a force push.
if ! git cat-file -e "${base}^{commit}" 2> /dev/null ||
  ! git diff --quiet "$base" HEAD -- containers/t3-code \
    .github/workflows/t3-code-image.yaml .github/workflows/t3-code-image-publish.yaml; then
  echo true
elif git diff --quiet "$base" HEAD -- mise.toml; then
  echo false
else
  before=$(python3 -I "$baseline" <(git show "${base}:mise.toml") 2> /dev/null) || true
  after=$(python3 -I "$baseline")
  if [[ $before == "$after" ]]; then echo false; else echo true; fi
fi
