#!/bin/sh
set -eu

. /opt/t3-code/t3-path.sh

mkdir -p "$HOME/workspace"
# T3 Code syncs the repo to ~/homelab and creates worktrees under
# ~/.t3/worktrees. MISE_TRUSTED_CONFIG_PATHS trusts both. `mise run setup`
# installs the pre-commit hooks (including the commit-msg check) into the
# repo's git hooks directory, which the worktrees share.
repo="$HOME/homelab"
if [ -f "$repo/mise.toml" ]; then
  (cd "$repo" && mise install && mise run setup && echo "mise install and setup finished" || echo "warning: mise install or setup failed" >&2) &
fi

# `t3 serve` prints an admin pairing token and QR code to stdout on startup.
# Keep that output out of container logs; stderr remains available for errors.
exec t3 serve "$HOME/workspace" > /dev/null
