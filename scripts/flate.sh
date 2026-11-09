#!/usr/bin/env bash
set -euo pipefail

# Offline render inputs only. Keep dummy Secrets outside the deployed manifests
# and never read SOPS keys or OpenBao values when validating the repository.
# https://github.com/home-operations/flate#supply-objects-from-outside-git
# Match the Gateway API capability for Helm renders in every validation path.
exec flate "$@" --api-versions gateway.networking.k8s.io/v1/HTTPRoute --supply - <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: cluster-secrets
  namespace: flux-system
stringData:
  CLUSTER_DOMAIN: example.invalid
  ADMIN_EMAIL: admin@example.invalid
---
apiVersion: v1
kind: Secret
metadata:
  name: flux-web-client
  namespace: flux-system
stringData:
  client-id: flux-web
  client-secret: offline-dummy
EOF
