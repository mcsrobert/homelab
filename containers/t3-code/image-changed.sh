#!/usr/bin/env bash
# Print true when the image's inputs differ between the given base commit and
# HEAD, otherwise false. A change to mise.toml only counts when it changes a
# baseline tool, since the image bakes no other pin. A base that is not in the
# checkout, such as after a force push, counts as a change.
set -euo pipefail

base=$1
cd "$(dirname "$0")/../.."
baseline=containers/t3-code/baseline-tools.py

if ! git cat-file -e "${base}^{commit}" 2> /dev/null ||
  ! git diff --quiet "$base" HEAD -- containers/t3-code \
    .github/workflows/t3-code-image.yaml .github/workflows/t3-code-image-publish.yaml; then
  echo true
elif git diff --quiet "$base" HEAD -- mise.toml; then
  echo false
else
  # A base whose mise.toml lacks a baseline pin prints nothing and so differs.
  before=$(python3 -I "$baseline" <(git show "${base}:mise.toml") 2> /dev/null) || true
  after=$(python3 -I "$baseline")
  if [[ $before == "$after" ]]; then echo false; else echo true; fi
fi
