# Ansible

A collection a playbooks to manage the cluster. Wraps around the amazing
[k3s_ansible](https://github.com/k3s-io/k3s-ansible/) for most of work.

## How to install

Ansible is installed by [mise](https://mise.jdx.dev) (see the root
[mise.toml](../mise.toml)). Before you can run the playbooks, install the
required controller-side Python dependencies and collections:

```bash
mise run install-ansible-collections
```

This also runs as part of `mise run setup`. Run it again after upgrading Ansible
to install the dependencies into the new Ansible environment.

## How to run

The [playbooks/](./playbooks/) directory contains the playbooks. Run them
through the mise tasks in the root [mise.toml](../mise.toml), which pass the
options they need:

| Task                            | Runs                                                         |
| ------------------------------- | ------------------------------------------------------------ |
| `mise run ansible-site`         | `playbooks/site.yaml --forks=1`                              |
| `mise run ansible-upgrade-os`   | `playbooks/apt_upgrade.yaml`                                 |
| `mise run ansible-upgrade-k3s`  | `playbooks/upgrade.yaml`                                     |
| `mise run ansible-rotate-token` | `playbooks/rotate-token.yaml`, then `k3s.orchestration.site` |

Arguments after `--` are passed on to `ansible-playbook`, for example:

```bash
mise run ansible-site -- --check --diff
mise run ansible-upgrade-os -- --limit raspberrypi
```

| Upgrade     | How                                                                                                                                                                                                                                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| k3s         | Bump `k3s_version` in [group_vars/k3s_cluster.yaml](./inventory/group_vars/k3s_cluster.yaml) (Renovate does this), then run `mise run ansible-upgrade-k3s`. It upgrades the servers one at a time, then the agents.                                                                                          |
| OS packages | `mise run ansible-upgrade-os` upgrades one node at a time and drains and reboots a node when a new kernel was installed.                                                                                                                                                                                     |
| kube-vip    | The image is pinned in [kube-vip.yaml.j2](./playbooks/templates/kube-vip.yaml.j2) (Renovate bumps it). Run `mise run ansible-site`, or `ansible-playbook playbooks/kube-vip.yaml` from this directory. It updates one server at a time and waits for the API server to answer on the VIP before it moves on. |

## Inventory and fixed cluster token

[Inventory](./inventory/hosts.yaml) retains the `k3s_cluster`, `server` and
`agent` groups. Hardware groups `turingpi_rk1` and `raspberry_pi` set SSH users.
Variables live in adjacent `inventory/group_vars/` so collection playbooks also
load them. `server.yaml` holds server configuration; `k3s_cluster.yaml` holds
shared settings and the Renovate-managed version.

Before running any local playbook, preserve the existing cluster token:

1. On turingpi-rk1-1, run `sudo cat /var/lib/rancher/k3s/server/token`
   privately. Use the **full** `K10...::server:...` string, including the
   cluster CA hash. K3s accepts this secure format for joining the existing
   cluster; do not replace it with a new secret or strip the prefix.
2. From the repository root, run
   `mise exec -- sops ansible/inventory/group_vars/k3s_cluster.sops.yaml`.
   In the editor replace the sample content with a single YAML `token` key whose
   quoted value is the full token. Save and exit; commit only the encrypted
   file. The dedicated creation rule encrypts every value in `*.sops.yaml`
   files under `inventory/group_vars/` and `inventory/host_vars/`, preserving
   Kubernetes rules.
3. Test with `mise run ansible-site -- --check --diff`, then
   `mise run ansible-site -- --check --diff --limit raspberrypi`.

The `community.sops.sops` vars plugin reads `*.sops.yaml` alongside ordinary
inventory vars, including for collection playbooks. It needs `sops` on PATH;
mise provides it and sets `SOPS_AGE_KEY_FILE` to the controller's age key file.
The playbooks that install, upgrade or rotate k3s (site, upgrade,
rotate-token) fail before contacting nodes when the token is missing or invalid.
Calling `k3s.orchestration.site` or `k3s.orchestration.upgrade` directly
bypasses that assertion: use the mise tasks. Never publish
`ansible-inventory --list` output after adding the encrypted token: it can
include decrypted values.

[Token formats](https://docs.k3s.io/cli/token) distinguish secure tokens from
short passwords: a brand-new cluster with self-signed CAs must initially use a
short token because its CA hash does not yet exist. This runbook preserves an
existing cluster, rather than bootstrapping a new one.

## Rotate the cluster token

```bash
mise run ansible-rotate-token
```

The task keeps the CA hash of the current token and generates a new passphrase.
It writes the new token to `k3s_cluster.sops.yaml` before anything touches the
nodes, runs [`k3s token rotate`](https://docs.k3s.io/cli/token#rotate) on the
first server and then `k3s.orchestration.site --forks=1`, which writes the new
token to every node and restarts the servers one at a time, then the agents.
Commit the updated SOPS file afterwards.

If the rotation itself fails, the file holds the new token while the servers
may still use the old one; the task prints how to restore it. Etcd snapshots
taken before a rotation need the old token to restore. It stays in the git
history of the encrypted file.

## Rebuild a node

Reinstall the OS, preserving a healthy server quorum and the encrypted token.
Before rebuilding the first server, move a healthy server first in
`inventory/hosts.yaml`. The first server gets `cluster-init` without `server:`;
a fixed token alone does not make rebuilding it safe. Run
`mise run ansible-site -- --limit <node>`; non-first servers join via `server:`
and the fixed token. Existing etcd data ignores datastore flags; keep the
healthy server first for subsequent runs. A genuinely new cluster needs a
separate bootstrap plan and token handling, not this rebuild procedure.
