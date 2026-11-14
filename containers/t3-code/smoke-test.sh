#!/usr/bin/env bash
# Smoke test a built image the way the pod runs it: as uid 1000 with a
# read-only root filesystem, no capabilities and an empty, writable home.
set -euo pipefail

image=$1
# shellcheck disable=SC2054 # the commas separate tmpfs mount options
run=(docker run --read-only --user 1000:1000 --cap-drop ALL
  --security-opt no-new-privileges
  --tmpfs /tmp:rw,exec,size=1g
  --tmpfs /home/node:rw,exec,uid=1000,gid=1000,mode=0700,size=2g)

# Each tool must resolve to its baked copy, not one installed in the home.
# shellcheck disable=SC2016 # the script expands inside the container
"${run[@]}" --rm --entrypoint /bin/bash "$image" -euo pipefail -c '
  for tool in t3 mise gh flux claude codex; do
    path=$(command -v "$tool")
    [[ $path == "/opt/t3-code/bin/$tool" ]] || { echo "$tool resolves to $path" >&2; exit 1; }
    echo "$tool: $("$tool" --version | head -n 1)"
  done
  bash -lc "[[ \$(command -v t3) == /opt/t3-code/bin/t3 ]]" || { echo "login shell PATH lacks /opt/t3-code/bin" >&2; exit 1; }
  [[ -r ${KUBECONFIG##*:} ]] || { echo "default kubeconfig ${KUBECONFIG##*:} is missing" >&2; exit 1; }
'

name=t3-code-smoke-$$
"${run[@]}" --detach --name "$name" "$image" > /dev/null
trap 'docker rm --force "$name" > /dev/null' EXIT
for _ in $(seq 60); do
  if docker exec "$name" curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:3773/.well-known/t3/environment 2> /dev/null; then
    echo "t3 serve is healthy"
    exit 0
  fi
  sleep 2
done
echo "t3 serve did not become healthy" >&2
docker logs "$name" >&2
exit 1
