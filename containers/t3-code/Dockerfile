# T3 Code server with its agent CLIs baked in. Everything lives in
# /opt/t3-code, outside /home/node, so the home volume cannot shadow it.
# Project tools stay in mise under the writable home directory.
FROM docker.io/library/node:26.13.0-trixie@sha256:438603bbb5cda404b9416f0b5c8026f61501ab911760d861ec28c36b5d675725 AS build

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

# Renovate updates each version together with the SHA-256 of its asset.
# renovate: datasource=github-release-attachments depName=pingdotgg/t3code
ARG T3CODE_VERSION=v0.1.9
ARG T3CODE_SHA256=52023bc5728a4dee4de361ad6fef7c236f1efb2742ff25e29d5c55e2fb662eea
# renovate: datasource=github-release-attachments depName=jdx/mise
ARG MISE_VERSION=v2026.10.25
ARG MISE_SHA256=bb09e88b13164ca90691a5cc042726d079f0ba26ab55527344cb173c2e83c998
# renovate: datasource=github-release-attachments depName=cli/cli
ARG GH_VERSION=v2.103.3
ARG GH_SHA256=ee32df2323c8847f5fac6cb6b4b69ca75828e9329a8f22f677021f7b3306ce63
# Keep in step with flux2 in the repository's mise.toml; Renovate groups both.
# renovate: datasource=github-release-attachments depName=fluxcd/flux2
ARG FLUX_VERSION=v2.10.0
ARG FLUX_SHA256=c3c435052e5e18156858bc73f26bcadb62821a6e30951b4b302beb4e65a532c2

WORKDIR /download

RUN curl -fsSLo t3.tar.gz "https://github.com/pingdotgg/t3code/releases/download/${T3CODE_VERSION}/t3-${T3CODE_VERSION#v}-linux-arm64.tar.gz" \
    && echo "${T3CODE_SHA256}  t3.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/t3-code/bin /opt/t3-code/server \
    && tar -xzf t3.tar.gz -C /opt/t3-code/server --strip-components=1 --no-same-owner \
    && ln -s ../server/t3 /opt/t3-code/bin/t3

RUN curl -fsSLo mise "https://github.com/jdx/mise/releases/download/${MISE_VERSION}/mise-${MISE_VERSION}-linux-arm64" \
    && echo "${MISE_SHA256}  mise" | sha256sum -c - \
    && install -m 0755 mise /opt/t3-code/bin/mise

RUN curl -fsSLo gh.tar.gz "https://github.com/cli/cli/releases/download/${GH_VERSION}/gh_${GH_VERSION#v}_linux_arm64.tar.gz" \
    && echo "${GH_SHA256}  gh.tar.gz" | sha256sum -c - \
    && tar -xzf gh.tar.gz --strip-components=2 --no-same-owner "gh_${GH_VERSION#v}_linux_arm64/bin/gh" \
    && install -m 0755 gh /opt/t3-code/bin/gh

RUN curl -fsSLo flux.tar.gz "https://github.com/fluxcd/flux2/releases/download/${FLUX_VERSION}/flux_${FLUX_VERSION#v}_linux_arm64.tar.gz" \
    && echo "${FLUX_SHA256}  flux.tar.gz" | sha256sum -c - \
    && tar -xzf flux.tar.gz --no-same-owner flux \
    && install -m 0755 flux /opt/t3-code/bin/flux

# npm ci checks every package against the integrity hashes in the lockfile,
# and npm audit checks their registry signatures. Lifecycle scripts are
# skipped: Claude's postinstall only copies the native binary over a
# placeholder, so link the binary directly instead.
WORKDIR /opt/t3-code/npm
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund --no-update-notifier \
    && npm audit signatures --no-update-notifier \
    && ln -s ../npm/node_modules/@anthropic-ai/claude-code-linux-arm64/claude /opt/t3-code/bin/claude

COPY --chmod=0755 codex.sh /opt/t3-code/bin/codex
COPY --chmod=0755 entrypoint.sh /opt/t3-code/entrypoint.sh
COPY --chmod=0644 kubeconfig.yaml /opt/t3-code/kubeconfig.yaml
RUN chmod -R a+rX,go-w /opt/t3-code

FROM docker.io/library/node:26.13.0-trixie@sha256:438603bbb5cda404b9416f0b5c8026f61501ab911760d861ec28c36b5d675725

COPY --from=build /opt/t3-code /opt/t3-code
# /etc/profile resets PATH for login shells, so restore it from profile.d.
COPY --chmod=0644 profile.sh /etc/profile.d/t3-code.sh

# The baked tools come first so nothing in /home/node can replace them. The
# user-writable npm prefix and the mise shims follow, then the system paths.
# KUBECONFIG lets ~/.kube/config override the baked service account context.
# DISABLE_UPDATES stops Claude Code from updating itself, also on request;
# new versions arrive with a new image.
ENV HOME=/home/node \
    PATH=/opt/t3-code/bin:/home/node/.local/bin:/home/node/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    NPM_CONFIG_PREFIX=/home/node/.local \
    KUBECONFIG=/home/node/.kube/config:/opt/t3-code/kubeconfig.yaml \
    DISABLE_UPDATES=1

USER 1000:1000
WORKDIR /home/node
ENTRYPOINT ["/opt/t3-code/entrypoint.sh"]
