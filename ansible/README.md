# Ansible

A collection a playbooks to manage the cluster. Wraps around the amazing
[k3s_ansible](https://github.com/k3s-io/k3s-ansible/) for most of work.

## How to install

Ansible is installed by [mise](https://mise.jdx.dev) (see the root
[README](../README.md#tooling)). Before you can run the playbooks, install the
required collections:

```bash
mise run install-ansible-collections
```

This also runs as part of `mise run setup`.

## How to run

Run everything from the `ansible/` directory, where `ansible.cfg` lives. The
[playbooks/](./playbooks/) directory contains the following playbooks:

- `site.yaml`: the full setup. Runs `configure.yaml`, `longhorn.yaml`,
  `rk3588_drivers.yaml`, k3s (`k3s.orchestration.site`) and `kube-vip.yaml`.
- `configure.yaml`: base host configuration of the cluster nodes.
- `longhorn.yaml`: installs the host prerequisites for Longhorn.
- `rk3588_drivers.yaml`: installs the RK3588 drivers (NPU, Mali, MPP, RGA) on
  the servers.
- `kube-vip.yaml`: installs kube-vip on the servers. The rolling upgrade is
  tagged `upgrade` and `never`, so it only runs with `--tags upgrade`.
- `apt_upgrade.yaml`: upgrades packages one node at a time, draining and
  rebooting a node when required.

A playbook can be run as follows:

```bash
ansible-playbook playbooks/site.yaml
```

To upgrade kube-vip:

```bash
ansible-playbook playbooks/kube-vip.yaml --tags upgrade
```

To just upgrade k3s, run the upgrade playbook shipped with the k3s-ansible
collection:

```bash
ansible-playbook k3s.orchestration.upgrade
```
