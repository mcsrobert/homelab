# T3 Code image

The container image for `kubernetes/apps/development/t3-code`. It bakes the
T3 Code server and the tools it relies on into `/opt/t3-code`, so the pod no
longer installs them into its home volume on every start.

## Contents

| Path | Source | Pinned by |
| --- | --- | --- |
| `/opt/t3-code/bin/t3` | [T3 Code release][t3code] tarball | SHA-256 |
| `/opt/t3-code/bin/mise` | [mise release][mise] binary | SHA-256 |
| `/opt/t3-code/bin/gh` | [GitHub CLI release][gh] tarball | SHA-256 |
| `/opt/t3-code/bin/flux` | [Flux CLI release][flux] tarball | SHA-256 |
| `/opt/t3-code/bin/claude` | [`@anthropic-ai/claude-code`][claude] | lockfile |
| `/opt/t3-code/bin/codex` | [`@openai/codex`][codex] | lockfile |

The base is the same `node` image the pod used before, pinned by digest, so
Node.js, git, curl and the build tools are unchanged. `npm ci` checks the
npm packages against `package-lock.json`, runs no install scripts, and
`npm audit signatures` checks their registry signatures. `DISABLE_UPDATES`
stops Claude Code from updating itself; new versions come with a new image.

`/opt/t3-code/bin` comes first in `PATH`, also for login shells through
`/etc/profile.d/t3-code.sh`, and the entrypoint starts the server by its
absolute path. Nothing in the home volume can replace these tools.

`KUBECONFIG` lists `~/.kube/config`, then
[`kubeconfig.yaml`](./kubeconfig.yaml), which gives kubectl, helm and flux a
`homelab` context for the pod's service account token. Without it, kubectl
only uses that token when no connection flag is set; `--request-timeout`
alone makes it try `localhost:8080`. A `~/.kube/config` takes precedence.

Everything else stays writable in `/home/node`: T3 Code state and worktrees
(`~/.t3`), agent settings and sessions (`~/.claude`, `~/.codex`), caches, and
`npm install -g` packages (`~/.local`). Project tools, such as the ones in
this repository's `mise.toml`, are installed by `mise install` in each
checkout under `~/.local/share/mise`. Deleting that directory is safe; the
next `mise install` restores it. The baked `flux` comes first in `PATH`, so
its version should match `flux2` in `mise.toml`; Renovate's Flux CD group
updates both in one pull request.

## Updates

Renovate updates the versions in the `Dockerfile` together with their asset
checksums, the npm packages together with `package-lock.json`, and the base
image digest. Do not edit them by hand.

## Build and publish

Both workflows build `linux/arm64` on a native ARM runner and run
[`smoke-test.sh`](./smoke-test.sh) against it: it checks that every tool
resolves to `/opt/t3-code/bin` and that `t3 serve` becomes healthy as uid 1000
with a read-only root filesystem.

- [`t3-code-image.yaml`](../../.github/workflows/t3-code-image.yaml) runs as
  part of the `ci` check on pull requests that change the image. It only
  builds and tests, with a read-only token.
- [`t3-code-image-publish.yaml`](../../.github/workflows/t3-code-image-publish.yaml)
  runs on pushes to `main` and publishes the image with an SBOM and a signed
  build provenance attestation. Run it by hand from `main` to rebuild without
  a change.

## Versions

Each publish gets its own immutable version, `<T3 Code version>.<run
number>`, such as `0.1.9.4`, plus a `sha-<commit>` tag for the commit it was
built from. No tag is ever moved: a re-run of a run that already published
fails, so start a new run instead. The image's `version` and `description`
labels name the version of every baked tool.

The run number keeps counting across T3 Code releases (`0.1.9.4` can be
followed by `0.1.10.5`), and every version tag has four parts. Renovate compares
container tags part by part and only between tags with the same number of
parts, so it orders these builds and offers each new one as an update.
Moving or renaming the publish workflow file restarts its run number, which
would make new builds sort below old ones.

Verify a published image with:

```sh
gh attestation verify oci://ghcr.io/mcsrobert/t3-code:<tag> --repo mcsrobert/homelab
```

The HelmRelease pins the image by version and digest, and Renovate opens a
pull request when `main` publishes a new version. The nodes pull without
credentials, so the GHCR package must be public; GitHub creates new packages
as private.

[t3code]: https://github.com/pingdotgg/t3code/releases
[mise]: https://mise.jdx.dev/installing-mise.html
[gh]: https://github.com/cli/cli/releases
[flux]: https://fluxcd.io/flux/installation/#install-the-flux-cli
[claude]: https://code.claude.com/docs/en/setup
[codex]: https://github.com/openai/codex
