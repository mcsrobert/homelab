# T3 Code server with its agent CLIs and the baseline cluster tools baked in.
# Tools and system config live outside /home/node so the home volume cannot
# shadow them. Build it from the repository root, which
# provides mise.toml; Dockerfile.dockerignore limits the context.

# BuildKit takes this as the build's SOURCE_DATE_EPOCH. With the exporter
# option rewrite-timestamp=true it clamps the file timestamps in every layer
# this build adds, so a layer whose files did not change keeps its digest from
# one build to the next. It also sets the image config's created date.
ARG SOURCE_DATE_EPOCH=0

FROM docker.io/library/node:26.13.1@sha256:e18c3ccdf7d71619990b0e85639f506e2028a95410999cab24cc373cbd10e0ac AS build

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

# Renovate updates each version together with the SHA-256 of its asset.
# renovate: datasource=github-release-attachments depName=pingdotgg/t3code
ARG T3CODE_VERSION=v0.1.11
ARG T3CODE_SHA256=7d90a5af032fe0988a616403e0f51a6cb68eff7ed7a84bc40999cc3997dffd71
# renovate: datasource=github-release-attachments depName=jdx/mise
ARG MISE_VERSION=v2026.11.12
ARG MISE_SHA256=52f4a75495bde5571d5d0a45b4d9b5227c2c099b92f9be85328569e67b12c553
# renovate: datasource=github-release-attachments depName=cli/cli
ARG GH_VERSION=v2.105.0
ARG GH_SHA256=b1278c75107423d818f1937fd92cafc9e5e9b606631568f68d7fb816f5885a02
# Baseline tools from the repository's mise.toml, which pins their versions.
ARG MISE_TOOLS="kubectl flux2 helm kustomize jq yq kubeconform github:home-operations/flate ripgrep"

WORKDIR /download

RUN curl -fsSLo t3.tar.gz "https://github.com/pingdotgg/t3code/releases/download/${T3CODE_VERSION}/t3-${T3CODE_VERSION#v}-linux-arm64.tar.gz" \
    && echo "${T3CODE_SHA256}  t3.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/t3-code/bin /opt/t3-code/server \
    && tar -xzf t3.tar.gz -C /opt/t3-code/server --strip-components=1 --no-same-owner

RUN curl -fsSLo mise "https://github.com/jdx/mise/releases/download/${MISE_VERSION}/mise-${MISE_VERSION}-linux-arm64" \
    && echo "${MISE_SHA256}  mise" | sha256sum -c - \
    && install -m 0755 mise /opt/t3-code/bin/mise

RUN curl -fsSLo gh.tar.gz "https://github.com/cli/cli/releases/download/${GH_VERSION}/gh_${GH_VERSION#v}_linux_arm64.tar.gz" \
    && echo "${GH_SHA256}  gh.tar.gz" | sha256sum -c - \
    && tar -xzf gh.tar.gz --strip-components=2 --no-same-owner "gh_${GH_VERSION#v}_linux_arm64/bin/gh" \
    && install -m 0755 gh /opt/t3-code/bin/gh

# Write the baseline versions from mise.toml to the system config, which every
# mise config inherits, then install them and their shims into
# /opt/t3-code/mise. mise verifies each download against what its registry
# declares for the tool: the release checksum, and for some tools a GitHub
# attestation or Cosign signature. The token only raises GitHub's rate limit.
COPY mise.toml /tmp/homelab/mise.toml
RUN --mount=type=secret,id=github_token \
    for tool in ${MISE_TOOLS}; do \
      version=$(/opt/t3-code/bin/mise config get --file /tmp/homelab/mise.toml "tools.\"${tool}\""); \
      printf '%s\n' "$version" | grep -Eqx '[0-9][0-9A-Za-z.+-]*' || { echo "mise.toml does not pin ${tool} to a plain version: ${version}" >&2; exit 1; }; \
      printf '"%s" = "%s"\n' "$tool" "$version"; \
    done > /tmp/tools.toml \
    && mkdir -p /etc/mise \
    && { echo '[tools]'; cat /tmp/tools.toml; } > /etc/mise/config.toml \
    && MISE_GITHUB_TOKEN=$(cat /run/secrets/github_token 2> /dev/null || true) \
    MISE_DATA_DIR=/opt/t3-code/mise MISE_CACHE_DIR=/tmp/mise/cache MISE_STATE_DIR=/tmp/mise/state \
    /opt/t3-code/bin/mise install \
    && rm -rf /opt/t3-code/mise/downloads /tmp/mise /tmp/tools.toml

# npm ci checks every package against the integrity hashes in the lockfile,
# and npm audit checks their registry signatures. Lifecycle scripts are
# skipped: Claude's postinstall only copies the native binary over a
# placeholder, so the final stage links the binary directly instead.
WORKDIR /opt/t3-code/npm
COPY containers/t3-code/package.json containers/t3-code/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund --no-update-notifier \
    && npm audit signatures --no-update-notifier

# The final stage copies only these two scopes, so fail the build when a
# dependency lands anywhere else instead of shipping an incomplete tree.
RUN stray=$(find node_modules -mindepth 1 -maxdepth 1 ! -name @openai ! -name @anthropic-ai ! -name .bin ! -name .package-lock.json) \
    && [[ -z $stray ]] || { echo "npm installed packages outside the copied scopes: ${stray}" >&2; exit 1; }

RUN chmod -R a+rX,go-w /opt/t3-code /etc/mise

FROM docker.io/library/node:26.13.1@sha256:e18c3ccdf7d71619990b0e85639f506e2028a95410999cab24cc373cbd10e0ac

# One layer per tool, or per group of tools for the baseline, so an update
# only downloads the layers it changes. Layers are pulled by digest, so their
# order does not change that; it only limits how many layers above a changed
# one a node has to unpack again, which is why the most often updated tools
# come last. The npm manifests and lockfiles stay in the build stage.
COPY --from=build /opt/t3-code/bin/gh /opt/t3-code/bin/gh
COPY --from=build /etc/mise /etc/mise
COPY --from=build /opt/t3-code/mise /opt/t3-code/mise
COPY --from=build /opt/t3-code/server /opt/t3-code/server
COPY --from=build /opt/t3-code/bin/mise /opt/t3-code/bin/mise
COPY --from=build /opt/t3-code/npm/node_modules/@openai /opt/t3-code/npm/node_modules/@openai
COPY --from=build /opt/t3-code/npm/node_modules/@anthropic-ai /opt/t3-code/npm/node_modules/@anthropic-ai
COPY --chmod=0755 containers/t3-code/codex.sh /opt/t3-code/bin/codex
COPY --chmod=0755 containers/t3-code/entrypoint.sh /opt/t3-code/entrypoint.sh
COPY --chmod=0644 containers/t3-code/kubeconfig.yaml /opt/t3-code/kubeconfig.yaml
COPY --chmod=0644 containers/t3-code/gitconfig /etc/gitconfig

# The server tools come first, then the shims of the baseline tools, so
# nothing in /home/node can replace them. A shim runs the version the current
# mise config selects: a project's own pin, otherwise the system config's
# baseline. mise looks for that version in ~/.local/share/mise, then in the
# baked installs. The user-writable npm prefix and the shims of tools that
# only projects install follow, then the system paths.
# Trust the homelab checkout and T3 Code worktrees, including new worktrees,
# without prompting. Other paths still require explicit mise trust.
# KUBECONFIG lets ~/.kube/config override the baked service account context.
# DISABLE_UPDATES and DISABLE_AUTOUPDATER stop Claude Code from updating
# itself, also on request; new versions arrive with a new image.
# LANG selects UTF-8 instead of the POSIX locale, so terminals, git and Python
# handle non-ASCII text. Debian's libc-bin ships C.UTF-8, so no locales package
# is needed.
ENV HOME=/home/node \
    PATH=/opt/t3-code/bin:/opt/t3-code/mise/shims:/home/node/.local/bin:/home/node/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    MISE_SHARED_INSTALL_DIRS=/opt/t3-code/mise/installs \
    MISE_TRUSTED_CONFIG_PATHS=/home/node/homelab:/home/node/.t3/worktrees \
    NPM_CONFIG_PREFIX=/home/node/.local \
    KUBECONFIG=/home/node/.kube/config:/opt/t3-code/kubeconfig.yaml \
    DISABLE_UPDATES=1 \
    DISABLE_AUTOUPDATER=1 \
    LANG=C.UTF-8

# Link the server and Claude's native binary into bin. /etc/profile resets
# PATH for login shells, so restore it from profile.d, written from the PATH
# above so the two cannot drift apart.
RUN ln -s ../server/t3 /opt/t3-code/bin/t3 \
    && ln -s ../npm/node_modules/@anthropic-ai/claude-code-linux-arm64/claude /opt/t3-code/bin/claude \
    && printf 'export PATH="%s"\n' "$PATH" > /etc/profile.d/t3-code.sh

USER 1000:1000
WORKDIR /home/node
ENTRYPOINT ["/opt/t3-code/entrypoint.sh"]
