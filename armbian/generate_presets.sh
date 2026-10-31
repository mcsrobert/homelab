#!/bin/bash
set -e

PRESETS=./userpatches/overlay/root/.not_logged_in_yet

export ROOT_PASSWORD=$(security find-generic-password -s rk1-armbian-k3s -a root -w)
export USER_PASSWORD=$(security find-generic-password -s rk1-armbian-k3s -a user -w)

git update-index --skip-worktree "$PRESETS"
git show "HEAD:$PRESETS" | envsubst '$ROOT_PASSWORD $USER_PASSWORD' > "$PRESETS"

echo "Successfully generated: $PRESETS"
