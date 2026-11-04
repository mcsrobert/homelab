#!/bin/sh
set -eu

# renovate: datasource=github-releases depName=pingdotgg/t3code
T3CODE_VERSION=v0.1.6
# renovate: datasource=github-releases depName=jdx/mise
MISE_VERSION=v2026.10.25
# renovate: datasource=github-releases depName=cli/cli
GH_VERSION=v2.103.3

t3_version=$(printf '%s' "$T3CODE_VERSION" | sed 's/^v//')
t3_dir="$HOME/.t3/runtime/versions/$t3_version"
if [ "$(cat "$t3_dir/.install-complete" 2>/dev/null)" != "$t3_version" ]; then
  t3_url="https://github.com/pingdotgg/t3code/releases/download/$T3CODE_VERSION"
  t3_archive="t3-$t3_version-linux-arm64.tar.gz"
  mkdir -p "$HOME/.t3/runtime/versions"
  rm -rf "$HOME"/.t3/runtime/versions/.staging-*
  t3_staging=$(mktemp -d "$HOME/.t3/runtime/versions/.staging-XXXXXX")
  curl -fsSL -o "$t3_staging/SHA256SUMS" "$t3_url/SHA256SUMS"
  curl -fsSL -o "$t3_staging/$t3_archive" "$t3_url/$t3_archive"
  (cd "$t3_staging" && grep " $t3_archive\$" SHA256SUMS | sha256sum -c -)
  tar -xzf "$t3_staging/$t3_archive" -C "$t3_staging" --strip-components=1
  rm -f "$t3_staging/$t3_archive" "$t3_staging/SHA256SUMS"
  printf '%s\n' "$t3_version" > "$t3_staging/.install-complete"
  rm -rf "$t3_dir"
  mv "$t3_staging" "$t3_dir"
fi
mkdir -p "$HOME/.local/bin"
ln -sfn "$t3_dir/t3" "$HOME/.local/bin/t3"
for dir in "$HOME"/.t3/runtime/versions/*; do
  [ "$dir" = "$t3_dir" ] || rm -rf "$dir"
done

mise_version=$(printf '%s' "$MISE_VERSION" | sed 's/^v//')
if ! mise --version 2>/dev/null | grep -q "^$mise_version "; then
  mise_url="https://github.com/jdx/mise/releases/download/$MISE_VERSION"
  mise_asset="mise-$MISE_VERSION-linux-arm64"
  curl -fsSL -o /tmp/SHASUMS256.txt "$mise_url/SHASUMS256.txt"
  curl -fsSL -o "/tmp/$mise_asset" "$mise_url/$mise_asset"
  (cd /tmp && grep " \./$mise_asset\$" SHASUMS256.txt | sha256sum -c -)
  install -m 0755 "/tmp/$mise_asset" "$HOME/.local/bin/mise"
fi

gh_version=$(printf '%s' "$GH_VERSION" | sed 's/^v//')
mise use -g "gh@$gh_version" || echo "warning: installing gh failed" >&2

mkdir -p "$HOME/workspace"
# T3 Code syncs the repo to ~/homelab and creates worktrees under
# ~/.t3/worktrees. MISE_TRUSTED_CONFIG_PATHS trusts both. `mise run setup`
# installs the pre-commit hooks (including the commit-msg check) into the
# repo's git hooks directory, which the worktrees share.
repo="$HOME/homelab"
if [ -f "$repo/mise.toml" ]; then
  (cd "$repo" && mise install && mise run setup && echo "mise install and setup finished" || echo "warning: mise install or setup failed" >&2) &
fi

exec t3 serve "$HOME/workspace"
