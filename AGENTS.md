# AGENTS.md

GitOps homelab: a k3s cluster on ARM, reconciled by FluxCD from this repo. See
[README.md](./README.md) for hardware and the software stack.

## Rules

- Work on a branch and open a pull request. Never commit or push to `main`.
- Git is the only way changes reach the cluster. Do not run anything that
  mutates live state: `kubectl apply/edit/patch/delete/scale`,
  `flux reconcile`, `helmfile apply`, `helm install/upgrade`, or
  `ansible-playbook`. Read-only commands are fine: `kubectl get/describe/logs`,
  `flux get`, `helm template`, `ansible-lint`.
- Never read, print or commit secrets, including the age key and decrypted
  `*.sops.yaml` content.
- Versions of images, charts and GitHub Actions are managed by Renovate. Do not
  bump them by hand unless asked.

## Layout

- `kubernetes/apps/<namespace>/<app>/`: apps reconciled by Flux. Follow an
  existing app such as `kubernetes/apps/default/habitica/`.
- `ansible/`: host configuration, k3s, kube-vip, drivers.
- `armbian/`: custom OS image build for the RK1 nodes.

## Commits

Conventional commits with a scope, matching the history, for example
`fix(habitica-mongodb): use double quotes`.
