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

| Task                           | Runs                              |
| ------------------------------ | --------------------------------- |
| `mise run ansible-site`        | `playbooks/site.yaml --forks=1`   |
| `mise run ansible-upgrade-os`  | `playbooks/apt_upgrade.yaml`      |
| `mise run ansible-upgrade-k3s` | `k3s.orchestration.upgrade`       |

Arguments after `--` are passed on to `ansible-playbook`, for example:

```bash
mise run ansible-site -- --check --diff
mise run ansible-upgrade-os -- --limit raspberrypi
```

| Upgrade     | How                                                                                                                                                                                                                                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| k3s         | Bump `k3s_version` in [inventory.yaml](./inventory.yaml) (Renovate does this), then run `mise run ansible-upgrade-k3s`. It upgrades the servers one at a time, then the agents.                                                                                                                              |
| OS packages | `mise run ansible-upgrade-os` upgrades one node at a time and drains and reboots a node when a new kernel was installed.                                                                                                                                                                                     |
| kube-vip    | The image is pinned in [kube-vip.yaml.j2](./playbooks/templates/kube-vip.yaml.j2) (Renovate bumps it). Run `mise run ansible-site`, or `ansible-playbook playbooks/kube-vip.yaml` from this directory. It updates one server at a time and waits for the API server to answer on the VIP before it moves on. |
