#!/bin/sh
set -eu

. /opt/t3-code/t3-path.sh

# renovate: datasource=github-releases depName=pingdotgg/t3code
T3CODE_VERSION=v0.1.7
# renovate: datasource=github-releases depName=jdx/mise
MISE_VERSION=v2026.11.3
# renovate: datasource=github-releases depName=cli/cli
GH_VERSION=v2.103.3
# renovate: datasource=npm depName=@anthropic-ai/claude-code
CLAUDE_VERSION=2.1.311
# renovate: datasource=npm depName=@openai/codex
CODEX_VERSION=0.166.0

# Stage T3 before switching the executable to the pinned version.
t3_version=$(printf '%s' "$T3CODE_VERSION" | sed 's/^v//')
t3_dir="$HOME/.t3/runtime/versions/$t3_version"
if [ "$(cat "$t3_dir/.install-complete" 2>/dev/null)" != "$t3_version" ]; then
  t3_url="https://github.com/pingdotgg/t3code/releases/download/$T3CODE_VERSION"
  t3_archive="t3-$t3_version-linux-arm64.tar.gz"
  mkdir -p "$HOME/.t3/runtime/versions"
  rm -rf "$HOME"/.t3/runtime/versions/.staging-*
  t3_staging=$(mktemp -d "$HOME/.t3/runtime/versions/.staging-XXXXXX")
  curl -fsSL -o "$t3_staging/$t3_archive" "$t3_url/$t3_archive"
  tar -xzf "$t3_staging/$t3_archive" -C "$t3_staging" --strip-components=1
  rm -f "$t3_staging/$t3_archive"
  printf '%s\n' "$t3_version" > "$t3_staging/.install-complete"
  rm -rf "$t3_dir"
  mv "$t3_staging" "$t3_dir"
fi
mkdir -p "$HOME/.local/bin"
ln -sfn "$t3_dir/t3" "$HOME/.local/bin/t3"
# Remove older T3 runtimes after switching the symlink.
for dir in "$HOME"/.t3/runtime/versions/*; do
  [ "$dir" = "$t3_dir" ] || rm -rf "$dir"
done

# Install the pinned mise binary.
mise_version=$(printf '%s' "$MISE_VERSION" | sed 's/^v//')
if ! mise --version 2>/dev/null | grep -q "^$mise_version "; then
  mise_url="https://github.com/jdx/mise/releases/download/$MISE_VERSION"
  mise_asset="mise-$MISE_VERSION-linux-arm64"
  curl -fsSL -o "/tmp/$mise_asset" "$mise_url/$mise_asset"
  install -m 0755 "/tmp/$mise_asset" "$HOME/.local/bin/mise"
fi

# Install gh through mise; keep startup working if it fails.
gh_version=$(printf '%s' "$GH_VERSION" | sed 's/^v//')
mise use -g "gh@$gh_version" || echo "warning: installing gh failed" >&2

# Use the official native installer with the pinned Claude version.
if [ "$(claude --version 2>/dev/null | cut -d ' ' -f 1)" != "$CLAUDE_VERSION" ]; then
  curl -fsSL -o /tmp/claude-install.sh https://claude.ai/install.sh
  bash /tmp/claude-install.sh "$CLAUDE_VERSION"
fi
# Fail startup if the installer did not provide the requested version.
[ "$(claude --version | cut -d ' ' -f 1)" = "$CLAUDE_VERSION" ]

# Install the pinned standalone Codex CLI using npm.
if [ "$(codex --version 2>/dev/null)" != "codex-cli $CODEX_VERSION" ]; then
  npm install --global --no-audit --no-fund "@openai/codex@$CODEX_VERSION"
fi
