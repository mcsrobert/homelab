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

# Without a network, each server tool must resolve to its baked copy, and each
# baseline tool to its baked shim and the mise.toml version in the baked
# installs, not one installed in the home.
# shellcheck disable=SC2016 # the script expands inside the container
"${run[@]}" --rm --network none --env BASELINE="$baseline" --env MISE_OFFLINE=1 \
  --entrypoint /bin/bash "$image" -euo pipefail -c '
  fail() { echo "$*" >&2; exit 1; }
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
      ripgrep) bin=rg args=(--version) ;;
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
