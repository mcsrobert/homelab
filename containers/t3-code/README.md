# T3 Code image

The container image for `kubernetes/apps/development/t3-code`. It bakes the
T3 Code server, the tools it relies on and a baseline of this repository's
development and cluster tools into the image, so the pod no longer installs
them into its home volume on every start.

## Server tools

| Path | Source | Pinned by |
| --- | --- | --- |
| `/opt/t3-code/bin/t3` | [T3 Code release][t3code] tarball | SHA-256 |
| `/opt/t3-code/bin/mise` | [mise release][mise] binary | SHA-256 |
| `/opt/t3-code/bin/gh` | [GitHub CLI release][gh] tarball | SHA-256 |
| `/opt/t3-code/bin/claude` | [`@anthropic-ai/claude-code`][claude] | lockfile |
| `/opt/t3-code/bin/codex` | [`@openai/codex`][codex] | lockfile |

The base is the same `node` image the pod used before, pinned by digest, so
Node.js, git, curl and the build tools are unchanged. `npm ci` checks the
npm packages against `package-lock.json`, runs no install scripts, and
`npm audit signatures` checks their registry signatures. `DISABLE_UPDATES`
and `DISABLE_AUTOUPDATER` stop Claude Code from updating itself; new versions
come with a new image.

`/opt/t3-code/bin` comes first in `PATH`, also for login shells through
`/etc/profile.d/t3-code.sh`, and the entrypoint starts the server by its
absolute path. Neither the home volume nor a project's mise config can
replace these tools.

`LANG` is `C.UTF-8`, a UTF-8 locale that Debian's `libc-bin` package ships, so
the shell, git and Python handle non-ASCII text instead of falling back to
`POSIX`. The image installs no locales package.

## Git defaults

[`gitconfig`](./gitconfig) is installed at `/etc/gitconfig`, outside the home
volume. Its GitHub credential helper reads `GH_TOKEN` at runtime; no token is
stored in the image. GitHub SSH URLs (`git@github.com:` and
`ssh://git@github.com/`) are rewritten to HTTPS so clones use that helper
without needing an SSH key. Other hosts are unchanged.

These are system defaults. Git's user, repository and command configuration
can customize them. The deployment supplies the commit author name and email,
tokens and time zone at runtime.

## Baseline tools

The tools agents run for most reviews and validations come from the
repository's [`mise.toml`](../../mise.toml), which stays the only place their
versions are pinned: kubectl, Flux (`flux2`), Helm, Kustomize, jq, yq,
kubeconform, flate and ripgrep (`rg`). `MISE_TOOLS` in the
[`Dockerfile`](./Dockerfile) selects them. The build reads their versions from
`mise.toml`, writes them to the system config `/etc/mise/config.toml`, and
installs them with their shims into `/opt/t3-code/mise`. Tools that only some
tasks need, such as Python, Ansible, SOPS or pre-commit, are left to
`mise install` in each checkout.

mise checks each download against what its registry declares for the tool:
the checksum the publisher releases, and for some tools a GitHub attestation
or Cosign signature. Unlike the server tools, no checksum is pinned in this
repository, which has no `mise.lock`.

### How tools resolve

`PATH` lists, in order:

1. `/opt/t3-code/bin`: the server tools.
2. `/opt/t3-code/mise/shims`: shims for the baseline tools.
3. `~/.local/bin`: `npm install -g` packages.
4. `~/.local/share/mise/shims`: shims for the tools that projects install.
5. The system paths.

A shim runs the version that the mise config for the current directory
selects. The system config selects the baseline, and this repository's
`mise.toml` selects the same versions, so outside a project and in a checkout
of `main` the shims run the baked copies. `MISE_SHARED_INSTALL_DIRS` makes
mise look in `/opt/t3-code/mise/installs` after `~/.local/share/mise/installs`,
and `mise install` skips what is baked.

A worktree that pins another version, such as a Renovate branch with a newer
kubectl, selects it on purpose: `mise install` puts it in
`~/.local/share/mise`, and the shims run it only inside that worktree. A copy
in the home also wins over the baked copy of the same version, so do not
`mise install --force` a baseline version.

### What stays writable

Everything else in `/home/node`: T3 Code state and worktrees (`~/.t3`), agent
settings and sessions (`~/.claude`, `~/.codex`), caches, `npm install -g`
packages (`~/.local`) and the tools projects install with mise
(`~/.local/share/mise`). Deleting `~/.local/share/mise` is safe; the next
`mise install` restores what is not baked.

`MISE_TRUSTED_CONFIG_PATHS` trusts mise configs in `/home/node/homelab` and
`/home/node/.t3/worktrees`, including future worktrees. Other locations still
need explicit trust. The deployment can override these image defaults.

`KUBECONFIG` lists `~/.kube/config`, then
[`kubeconfig.yaml`](./kubeconfig.yaml), which gives kubectl, helm and flux a
`homelab` context for the pod's service account token. Without it, kubectl
only uses that token when no connection flag is set; `--request-timeout`
alone makes it try `localhost:8080`. A `~/.kube/config` takes precedence.

## Updates

Renovate updates the versions in the `Dockerfile` together with their asset
checksums, the npm packages together with `package-lock.json`, the base image
digest, and the baseline tools in `mise.toml`. Do not edit them by hand. A change
to a baseline tool in `mise.toml` rebuilds the image in its pull request and
publishes a new version from `main`; other `mise.toml` changes do not.

Renovate opens the pull request that bumps the image in the HelmRelease on its
first run after a new version is published, and labels it `renovate/skip-soak`.
[`renovate-soak.yaml`](../../.github/workflows/renovate-soak.yaml) then passes
its soak check right away instead of after six days, so it merges once its
checks pass, because everything the image bakes in already soaked in its own
pull request.

The HelmRelease temporarily retains its mise trust paths, GitHub credential
helper and URL rewrites so the currently pinned image keeps working. Once
Renovate deploys an image containing these defaults, remove those environment
entries in a follow-up and keep only the Git author entries, renumbering them
and updating `GIT_CONFIG_COUNT`.

## Build and publish

Both workflows build `linux/arm64` on a native ARM runner from the repository
root. [`Dockerfile.dockerignore`](./Dockerfile.dockerignore) limits the
context to `mise.toml` and this directory. The workflow's read-only token is
passed as a build secret, so mise's GitHub requests are not rate limited.

- [`t3-code-image.yaml`](../../.github/workflows/t3-code-image.yaml) runs as
  part of the `ci` check on pull requests that change the image or a baseline
  tool, as [`image-changed.sh`](./image-changed.sh) decides. It only builds
  and tests, with a read-only token.
- [`t3-code-image-publish.yaml`](../../.github/workflows/t3-code-image-publish.yaml)
  runs on pushes to `main` that change them. It pushes the image by digest
  with an SBOM, smoke tests that digest, adds signed build provenance, and
  only then tags it. A failed smoke test or attestation leaves an untagged
  digest in the package, so a retry can finish before the version is published.
  Run it by hand from `main` to rebuild without a change.

The final stage copies each tool into its own layer, and the baseline tools
into one, with the most often updated last. Layers are pulled by digest, so
an update only downloads the layers it changes, such as Claude Code's, instead
of the whole `/opt/t3-code`. The `ARG SOURCE_DATE_EPOCH=0` in the
[`Dockerfile`](./Dockerfile) and `rewrite-timestamp=true` in both workflows
clamp every file timestamp in the layers the build adds, so a tool that did
not change keeps its layer digest in the next version. Base image layers are
not rewritten. The same setting dates the image config's `created` field
1970-01-01; the `revision` label and the publish run tell when and from what
it was built.

Published images use OCI media types and zstd level 9 compression for every
layer, including the Node base layers (`force-compression=true`), to reduce
download size and decoding time. Recompression changes the base layers'
compressed digests, so they are not shared with gzip copies of the Node image.
The pull-request build uses a gzip Docker archive for local loading; the
publish smoke test pulls and tests the actual zstd image by digest.

Both run [`smoke-test.sh`](./smoke-test.sh) as uid 1000 with a read-only root
filesystem, no capabilities and an empty home. It checks that:

- without a network, every server tool resolves to `/opt/t3-code/bin`, and
  every baseline tool to its baked shim and the version `mise.toml` pins, also
  in a login shell. [`baseline-tools.py`](./baseline-tools.py) reads the
  expected tools and versions from the sources, not the image;
- no file under `/opt/t3-code` or `/etc/mise` is newer than the epoch, which
  would mean the timestamps were not clamped and every build gets new layer
  digests;
- a project that pins another jq installs it into the home and runs it there,
  while the baseline stays the default elsewhere;
- mise loads configs in the checkout and worktree directories without a trust
  prompt, while a config outside those directories still needs trust;
- Git uses a dummy runtime token for GitHub only and rewrites both forms of
  GitHub SSH URL to HTTPS without contacting a remote;
- `t3 serve` becomes healthy.

## Versions

Each publish gets its own immutable version, `<T3 Code version>.<run
number>`, such as `0.1.9.4`. The image's `revision` label names the commit
it was built from. No tag is ever moved: a re-run of a run that already
published fails, so start a new run instead. The image's `version` and `description`
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
[claude]: https://code.claude.com/docs/en/setup
[codex]: https://github.com/openai/codex
