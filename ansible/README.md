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

The [playbooks/](./playbooks/) directory contains several playbook to run.
A playbook can be run as follows:

```bash
ansible-playbook playbooks/site.yaml
```

To just upgrade k3s, run:

```bash
ansible-playbook k3s.orchestration.upgrade
```
