# Ansible

A collection a playbooks to manage the cluster. Wraps around the amazing
[k3s_ansible](https://github.com/k3s-io/k3s-ansible/) for most of work.

## How to install

Before you can run the playbooks, you need to install the requirements.

```bash
ansible-galaxy install -r requirements.yml
```

## How to run

The [playbooks/](./playbooks/) directory contains several playbook to run.
A playbook can be run as follows:

```bash
ansible-playbook -i inventory.yaml playbooks/install.yaml
```

To just upgrade k3s, run:

```bash
ansible-playbook -i inventory.yaml k3s.orchestration.upgrade
```
