#!/usr/bin/env bash
# Smoke test a built image the way the pod runs it: as uid 1000 with a
# read-only root filesystem, no capabilities and an empty, writable home.
set -euo pipefail

image=$1
dir=$(dirname "$0")
# shellcheck disable=SC2054 # the commas separate tmpfs mount options
run=(docker run --read-only --user 1000:1000 --cap-drop ALL
  --security-opt no-new-privileges
  --tmpfs /tmp:rw,exec,size=1g
  --tmpfs /home/node:rw,exec,uid=1000,gid=1000,mode=0700,size=2g)

# kubectl=1.37.2 and so on, from the Dockerfile and mise.toml, not the image.
baseline=$(python3 -I "$dir/baseline-tools.py")
# The Dockerfile's SOURCE_DATE_EPOCH, which every file in its layers is clamped to.
epoch=$(sed -n 's/^ARG SOURCE_DATE_EPOCH=//p' "$dir/Dockerfile")
[[ $epoch =~ ^[0-9]+$ ]] || { echo "Dockerfile has no numeric SOURCE_DATE_EPOCH default" >&2; exit 1; }

# Without a network, each server tool must resolve to its baked copy, and each
# baseline tool to its baked shim and the mise.toml version in the baked
# installs, not one installed in the home.
# The writable /etc/claude-auth stands in for the deployment's Secret mount.
# shellcheck disable=SC2016 # the script expands inside the container
"${run[@]}" --rm --network none --env BASELINE="$baseline" --env EPOCH="$epoch" --env MISE_OFFLINE=1 \
  --tmpfs /etc/claude-auth:rw,uid=1000,gid=1000,mode=0700,size=1m \
  --entrypoint /bin/bash "$image" -euo pipefail -c '
  fail() { echo "$*" >&2; exit 1; }
  # BuildKit only logs a warning when it cannot rewrite a layer, and the layer
  # then gets a new digest with every build.
  newer=$(find /opt/t3-code /etc/mise /etc/gitconfig /etc/profile.d/t3-code.sh -newermt "@$EPOCH" | head -n 5)
  [[ -z $newer ]] || fail "files newer than SOURCE_DATE_EPOCH $EPOCH, so layer digests change with every build: $newer"
  for tool in t3 mise gh claude codex; do
    path=$(command -v "$tool")
    [[ $path == "/opt/t3-code/bin/$tool" ]] || fail "$tool resolves to $path"
    output=$("$tool" --version)
    echo "$tool: $(head -n 1 <<< "$output")"
  done
  for pin in $BASELINE; do
    tool=${pin%=*} version=${pin#*=}
    case $tool in
      kubectl) bin=kubectl args=(version --client) ;;
      flux2) bin=flux args=(--version) ;;
      helm) bin=helm args=(version --short) ;;
      kustomize) bin=kustomize args=(version) ;;
      jq | yq) bin=$tool args=(--version) ;;
      kubeconform) bin=kubeconform args=(-v) ;;
      github:home-operations/flate) bin=flate args=(--version) ;;
      *) fail "smoke-test.sh has no version check for $tool" ;;
    esac
    path=$(command -v "$bin")
    [[ $path == "/opt/t3-code/mise/shims/$bin" ]] || fail "$bin resolves to $path"
    where=$(mise where "$tool")
    [[ $where == /opt/t3-code/mise/installs/*/"$version" ]] || fail "$tool resolves to $where, not $version"
    output=$("$bin" "${args[@]}")
    output=$(head -n 1 <<< "$output")
    [[ $output == *"$version"* ]] || fail "$bin reports $output, not $version"
    echo "$bin: $output"
  done
  login_path=$(bash -lc "printf %s \"\$PATH\"")
  [[ $login_path == "$PATH" ]] || fail "login shell PATH is $login_path, not $PATH"
  [[ -r ${KUBECONFIG##*:} ]] || fail "default kubeconfig ${KUBECONFIG##*:} is missing"

  # No Helm environment overrides or saved trust state: new configs under the
  # image defaults must load, and a config outside them must still need trust.
  for project in "$HOME/homelab" "$HOME/.t3/worktrees/smoke"; do
    mkdir -p "$project"
    printf "[env]\nT3_CODE_SMOKE_TRUST = \"trusted\"\n" > "$project/mise.toml"
    value=$(cd "$project" && mise env --dotenv)
    [[ $value == *T3_CODE_SMOKE_TRUST=trusted* ]] || fail "mise did not load $project/mise.toml"
  done
  mkdir /tmp/untrusted
  cp "$HOME/homelab/mise.toml" /tmp/untrusted/
  if (cd /tmp/untrusted && mise env --dotenv) < /dev/null > /dev/null 2>&1; then
    fail "mise trusted a config outside the default directories"
  fi

  # Exercise the credential protocol with a dummy token only, without a network.
  credentials=$(printf "protocol=https\nhost=github.com\n\n" | GH_TOKEN=smoke-test-token git credential fill)
  [[ $credentials == *"username=x-access-token"* && $credentials == *"password=smoke-test-token"* ]] || fail "GitHub helper did not use the runtime token"
  if printf "protocol=https\nhost=example.invalid\n\n" | GH_TOKEN=smoke-test-token GIT_TERMINAL_PROMPT=0 git credential fill > /dev/null 2>&1; then
    fail "GitHub helper supplied credentials for another host"
  fi
  git init --quiet /tmp/git-smoke
  git -C /tmp/git-smoke remote add smoke https://github.com/owner/repo.git
  for url in git@github.com:owner/repo.git ssh://git@github.com/owner/repo.git https://github.com/owner/repo.git; do
    git -C /tmp/git-smoke remote set-url smoke "$url"
    rewritten=$(git -C /tmp/git-smoke remote get-url smoke)
    [[ $rewritten == https://github.com/owner/repo.git ]] || fail "Git did not rewrite $url to HTTPS"
  done
  echo "mise trust and GitHub defaults work without Helm configuration"

  # claude auth status exits 0 only with a credential. The launcher must take
  # a dummy token from the mounted file, which the environment lacks.
  if claude auth status > /dev/null; then
    fail "claude reports a credential without a token file"
  fi
  printf smoke-test-token > /etc/claude-auth/CLAUDE_CODE_OAUTH_TOKEN
  claude auth status > /dev/null || fail "claude did not load the token file"
  echo "claude loads its OAuth token from /etc/claude-auth"
'

# A project that pins another version gets it from the writable home, while
# the baseline stays the default everywhere else.
# MISE_GITHUB_TOKEN, when set, keeps the jq download clear of GitHub's
# unauthenticated rate limit.
jq_baseline=$(tr ' ' '\n' <<< "$baseline" | sed -n 's/^jq=//p')
# shellcheck disable=SC2016 # the script expands inside the container
"${run[@]}" --rm --env JQ_BASELINE="$jq_baseline" --env MISE_GITHUB_TOKEN --entrypoint /bin/bash "$image" -euo pipefail -c '
  fail() { echo "$*" >&2; exit 1; }
  alternate=1.7.1
  [[ $alternate != "$JQ_BASELINE" ]] || fail "pick an alternate jq other than the baseline $JQ_BASELINE"
  mkdir ~/project && cd ~/project
  printf "[tools]\njq = \"%s\"\n" "$alternate" > mise.toml
  MISE_TRUSTED_CONFIG_PATHS=$PWD mise install --quiet
  where=$(MISE_TRUSTED_CONFIG_PATHS=$PWD mise where jq)
  [[ $where == "$HOME/.local/share/mise/installs/jq/$alternate" ]] || fail "project jq is $where"
  [[ $(MISE_TRUSTED_CONFIG_PATHS=$PWD jq --version) == "jq-$alternate" ]] || fail "project jq is not $alternate"
  cd ~
  [[ $(jq --version) == "jq-$JQ_BASELINE" ]] || fail "default jq is not $JQ_BASELINE after a project install"
  echo "jq: $alternate in the project, $JQ_BASELINE elsewhere"
'

name=t3-code-smoke-$$
"${run[@]}" --detach --name "$name" "$image" > /dev/null
trap 'docker rm --force "$name" > /dev/null' EXIT
# The entrypoint discards stdout because it holds the pairing token, so the
# logs only show stderr. Report the container state too, and stop waiting as
# soon as the server exits.
fail() {
  echo "t3 serve $1" >&2
  docker inspect --format 'state={{.State.Status}} exit={{.State.ExitCode}} error={{.State.Error}}' "$name" >&2
  docker logs "$name" >&2
  exit 1
}
for _ in $(seq 60); do
  if docker exec "$name" curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:3773/.well-known/t3/environment 2> /dev/null; then
    echo "t3 serve is healthy"
    exit 0
  fi
  [[ $(docker inspect --format '{{.State.Running}}' "$name") == true ]] || fail "exited"
  sleep 2
done
fail "did not become healthy"
