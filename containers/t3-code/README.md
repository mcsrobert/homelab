# T3 Code image

ARM64 image for the [T3 Code deployment](../../kubernetes/apps/development/t3-code/app/helm-release.yaml).
It bakes the server, agent CLIs and baseline cluster tools outside the
persistent home volume, so startup needs no tool downloads.

## Server tools

All server tools live in `/opt/t3-code/bin`, first in `PATH` even in login
shells. The entrypoint starts the server by its absolute path.

| Tool | Source | Verification |
| --- | --- | --- |
| `t3` | [T3 Code release][t3code] | SHA-256 |
| `mise` | [mise release][mise] | SHA-256 |
| `gh` | [GitHub CLI release][gh] | SHA-256 |
| `claude` | [`@anthropic-ai/claude-code`][claude] | npm lockfile |
| `codex` | [`@openai/codex`][codex] | npm lockfile |

The digest-pinned Node base supplies Node.js, git, curl and build tools.
`npm ci` verifies lockfile integrity without lifecycle scripts, and
`npm audit signatures` checks registry signatures. Claude's self-updates are
disabled; server tool updates arrive through a new image.

## Baseline tools

[`Dockerfile`](./Dockerfile)'s `MISE_TOOLS` selects kubectl, Flux, Helm,
Kustomize, jq, yq, kubeconform and flate. Their versions come only
from the repository's [`mise.toml`](../../mise.toml); the build writes them
to `/etc/mise/config.toml` and installs them with shims under
`/opt/t3-code/mise`. mise verifies downloads against its registry's checksums
and, where supported, attestations or signatures. This repository has no
`mise.lock`. ripgrep is not a baseline tool: Claude Code embeds it, and Codex
and the server's Cursor SDK ship their own `rg`.

`PATH` searches in this order:

1. `/opt/t3-code/bin`: server tools.
2. `/opt/t3-code/mise/shims`: baseline tool shims.
3. `~/.local/bin`: user-installed npm packages.
4. `~/.local/share/mise/shims`: tools installed by projects.
5. System paths.

The shims select each project's pinned version, or the system baseline
outside a project. `MISE_SHARED_INSTALL_DIRS` lets mise reuse the baked
installs after checking `~/.local/share/mise/installs`. A different pin in a
worktree installs into the home and applies only there. Do not force-reinstall
a baseline tool: a home copy of the same version takes precedence.

Python, Ansible, SOPS, pre-commit and other tools outside the baseline are
installed by `mise install` in the checkout.

## Runtime defaults

- [`gitconfig`](./gitconfig) lives at `/etc/gitconfig`. Its GitHub credential
  helper reads `GH_TOKEN` at runtime, and both GitHub SSH URL forms are
  rewritten to HTTPS. Git user, repository and command configuration can
  customize these defaults.
- `MISE_TRUSTED_CONFIG_PATHS` trusts `/home/node/homelab` and
  `/home/node/.t3/worktrees`, including future worktrees. Other locations
  retain mise's normal trust checks.
- `KUBECONFIG` lists `~/.kube/config` before the baked
  [`kubeconfig.yaml`](./kubeconfig.yaml), which supplies the pod service
  account's `homelab` context. The user config takes precedence.
- `LANG=C.UTF-8` enables Unicode in terminals and tools using Debian's built-in
  locale. The deployment supplies tokens, Git author identity and time zone.

`/home/node` holds writable state: T3 Code sessions and worktrees (`~/.t3`),
agent settings (`~/.claude`, `~/.codex`), caches, npm packages (`~/.local`) and
project tools (`~/.local/share/mise`). The npm prefix is `~/.local`. Deleting
mise's home data is safe; `mise install` restores tools outside the baseline.

## Build and updates

Renovate maintains tool versions and checksums, npm lockfiles and the base
image digest. Do not update them by hand. Baseline version changes in
`mise.toml` rebuild the image; other changes to that file do not.

Both workflows build `linux/arm64` from the repository root on native ARM
runners. [`Dockerfile.dockerignore`](./Dockerfile.dockerignore) limits the
context, and a read-only GitHub token is supplied as a build secret.

- [`t3-code-image.yaml`](../../.github/workflows/t3-code-image.yaml) builds
  and smoke tests image changes in PR CI, as
  [`image-changed.sh`](./image-changed.sh) determines.
- [`t3-code-image-publish.yaml`](../../.github/workflows/t3-code-image-publish.yaml)
  publishes from `main`: push by digest with an SBOM, smoke test, attest, then
  tag. Failed runs leave an untagged digest. Start a new run to rebuild a
  version that was already tagged.

[`smoke-test.sh`](./smoke-test.sh) runs as uid 1000 with an empty home, a
read-only root filesystem and no capabilities. It checks tool resolution and
versions, login-shell PATH, layer timestamps, project version overrides, mise
trust boundaries, GitHub credentials and URL rewrites, and server health.

Tools have separate image layers to keep updates small. `SOURCE_DATE_EPOCH=0`
and the exporters' `rewrite-timestamp=true` preserve unchanged layer digests;
the image's `created` timestamp is therefore 1970-01-01. Published layers use
OCI media types and zstd level 9 compression, including recompressed base
layers. PR builds load a gzip Docker archive; publishing tests the actual
zstd image by digest.

## Versions and deployment

Tags are immutable: `<T3 Code version>.<publish run number>`, such as
`0.1.9.4`. The run number continues across T3 Code releases. Do not rename the
publish workflow: that resets its counter and makes new tags sort below old
ones. The `revision` label identifies the source commit; `version` and
`description` identify the baked tools.

Renovate updates the HelmRelease's version and digest after publication,
with `renovate/skip-soak` because the source updates already soaked. The GHCR
package must be public so nodes can pull without credentials.

Verify a published image's provenance with:

```sh
gh attestation verify oci://ghcr.io/mcsrobert/t3-code:<tag> --repo mcsrobert/homelab
```

[t3code]: https://github.com/pingdotgg/t3code/releases
[mise]: https://mise.jdx.dev/installing-mise.html
[gh]: https://github.com/cli/cli/releases
[claude]: https://code.claude.com/docs/en/setup
[codex]: https://github.com/openai/codex
