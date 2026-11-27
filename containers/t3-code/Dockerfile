# The exporter uses this epoch with rewrite-timestamp to keep unchanged layer digests stable.
ARG SOURCE_DATE_EPOCH=0

FROM docker.io/library/node:26.13.1@sha256:f8e5d4bb28fa55c74f1345166a082a10b7c68f22304350c1ce0d18358ae94996 AS build

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

# renovate: datasource=github-release-attachments depName=pingdotgg/t3code
ARG T3CODE_VERSION=v0.1.13
ARG T3CODE_SHA256=c553c17db07030d032808989427db5022bf04027ebcec2218c69fb1a5fa1377f
# renovate: datasource=github-release-attachments depName=jdx/mise
ARG MISE_VERSION=v2026.11.14
ARG MISE_SHA256=7bd3b2d5ffff077a7662ba186c09600e5b40c1eee9df0c264ed67642ce0a8698
# renovate: datasource=github-release-attachments depName=cli/cli
ARG GH_VERSION=v2.106.0
ARG GH_SHA256=5e8a0c51e1a8345f36bd0b8bb88604b6a5388df16f3ab3e3b9b6d794fa223a67
ARG MISE_TOOLS="kubectl flux2 helm kustomize jq yq kubeconform github:home-operations/flate"

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

WORKDIR /opt/t3-code/npm
COPY containers/t3-code/package.json containers/t3-code/package-lock.json ./
# Skipping Claude's postinstall leaves a placeholder. The final stage links the native binary.
RUN npm ci --ignore-scripts --no-audit --no-fund --no-update-notifier \
    && npm audit signatures --no-update-notifier

RUN stray=$(find node_modules -mindepth 1 -maxdepth 1 ! -name @openai ! -name @anthropic-ai ! -name .bin ! -name .package-lock.json) \
    && [[ -z $stray ]] || { echo "npm installed packages outside the copied scopes: ${stray}" >&2; exit 1; }

RUN chmod -R a+rX,go-w /opt/t3-code /etc/mise

FROM docker.io/library/node:26.13.1@sha256:f8e5d4bb28fa55c74f1345166a082a10b7c68f22304350c1ce0d18358ae94996

# Keep frequently updated tools in later layers to reduce unpacking after an update.
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

# Put baked tools first in PATH so the writable home cannot shadow them.
ENV HOME=/home/node \
    PATH=/opt/t3-code/bin:/opt/t3-code/mise/shims:/home/node/.local/bin:/home/node/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    MISE_SHARED_INSTALL_DIRS=/opt/t3-code/mise/installs \
    MISE_TRUSTED_CONFIG_PATHS=/home/node/homelab:/home/node/.t3/worktrees \
    NPM_CONFIG_PREFIX=/home/node/.local \
    KUBECONFIG=/home/node/.kube/config:/opt/t3-code/kubeconfig.yaml \
    DISABLE_UPDATES=1 \
    DISABLE_AUTOUPDATER=1 \
    LANG=C.UTF-8

# /etc/profile resets PATH for login shells.
RUN ln -s ../server/t3 /opt/t3-code/bin/t3 \
    && ln -s ../npm/node_modules/@anthropic-ai/claude-code-linux-arm64/claude /opt/t3-code/bin/claude \
    && printf 'export PATH="%s"\n' "$PATH" > /etc/profile.d/t3-code.sh

USER 1000:1000
WORKDIR /home/node
ENTRYPOINT ["/opt/t3-code/entrypoint.sh"]
