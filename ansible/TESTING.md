# Ideas: testing Ansible changes safely

Status: **ideas only**, nothing here is implemented. Today the only automated
check is `ansible-lint` (pre-commit and the `Lint` workflow, profile
`production`, `strict: true`, see [.ansible-lint](./.ansible-lint)). Claude
cannot run `ansible-playbook` or `ansible` at all: both are in the deny list in
[.claude/settings.json](../.claude/settings.json). Changes such as the
`apt_upgrade` drain/uncordon logic reach the hosts untested.

Note: at the time of writing, `configure.yaml` still edits
`/etc/ssh/sshd_config` with `lineinfile` (guarded by `validate: sshd -t -f %s`)
and there is no k3s secrets-encryption setting in this tree. The ideas below
use what is actually in `ansible/playbooks/`.

## What we have to protect

- 3 RK1 servers (`turingpi-rk1-1..3`, user `armbian`) and 1 Raspberry Pi agent
  (`raspberrypi`, user `maurice`), all in `k3s_cluster`
  ([inventory.yaml](./inventory.yaml)). Vars live in the inventory, there is
  no `group_vars/`.
- k3s with embedded etcd: three servers means quorum is 2. Breaking a second
  server at the same time as one that is down loses the API.
- kube-vip (static pod on the servers) holds `192.168.178.40`, the API
  endpoint.
- Longhorn replicas live on the nodes. Rebooting the next node before volumes
  are healthy again risks data loss (hence the wait in `apt_upgrade.yaml`).
- sshd and `PasswordAuthentication no` can lock us out of every node.

## Ideas

### 1. ansible-lint, tighter

- Already on `profile: production`, `strict: true`. It also runs a syntax
  check internally, so a separate `--syntax-check` job adds almost nothing.
- Possible additions: `ansible-lint --offline` in CI to avoid galaxy flakiness,
  enabling opt-in rules (`ansible-lint --list-rules`), adding `yamllint`
  (already there).
- Catches style and some bugs, never behaviour. Effort: trivial. Risk: none.

### 2. `--syntax-check`

- `ansible-playbook --syntax-check playbooks/site.yaml` needs the collections
  from `requirements.yml` (k3s.orchestration comes from git).
- Redundant with item 1. Only worth it as a cheap CI step if lint is ever
  slimmed down. Effort: trivial. Risk: none (no host contact).

### 3. `--check --diff` against real hosts

Shows what would change, per host, with file diffs. Only useful if the tasks
are check-mode friendly. In this repo:

| Playbook / task | Under `--check` |
| --- | --- |
| `configure.yaml` localhost `infocmp` (`command`) | Skipped, so `ghostty_terminfo.stdout` is undefined later. Needs `check_mode: false` (safe, read-only) |
| `configure.yaml` `findmnt`, `infocmp` checks | Same: skipped, results missing. Add `check_mode: false` |
| `configure.yaml` `tic -x -`, handlers (`service`) | Skipped or simulated, fine |
| `configure.yaml` sshd `lineinfile` + `validate` | Diff works; `validate` is not run, and the sshd restart handler is not run |
| `longhorn.yaml` `modprobe`, `service`, `apt` | Supported. The `update-initramfs` handler is a `command`, skipped |
| `rk3588_drivers.yaml` `apt: deb:` from URL, then `file` on `libmali.so.1.9.0` | The file task fails when the deb is not yet installed, because check mode did not install it |
| `rk3588_drivers.yaml` `get_url` | Works, but downloads to compare checksums |
| `kube-vip.yaml` `template` | Good diff. The `wait_for` handler is not meaningful |
| `apt_upgrade.yaml` venv `pip` + `set_fact` interpreter | Venv may not exist, so later `kubernetes.core` tasks run with a missing interpreter and fail |
| `apt_upgrade.yaml` `reboot` | Skipped. The `failed_when: false` on drain also hides real errors |
| `k3s.orchestration.site` (via `site.yaml`) | Downloads, systemd, shell: do not trust check mode |

- Safest form: `ansible-playbook playbooks/configure.yaml --check --diff
  -l raspberrypi`. Read-only in intent, but modules with side effects when
  not check-aware (`command`/`shell` with `check_mode: false`) still run, so
  review each such task first.
- Pros: tests against reality (real versions, real files). Cons: partial
  coverage, needs the `check_mode: false` fixes and a few `when: not
  ansible_check_mode` guards. Diffs may print file content: add `diff: false`
  and `no_log: true` on anything secret-ish.
- Effort: low-medium (small playbook edits). Risk: low, but it is still
  SSH access to production; see enforcement below.

### 4. Molecule or plain containers

- A systemd-capable Debian arm64 image (for example a `debian:trixie` image
  with systemd, or `geerlingguy/docker-debian*-ansible`) run on the Mac
  (Docker Desktop is arm64-native) or on an arm64 GitHub runner.
- Good for: file edits, `template`, `lineinfile`, `apt` package installs,
  handlers firing, sshd config validation (`sshd -t`), idempotence.
- Cannot test: `reboot`, `modprobe dm_crypt`, `update-initramfs`, udev
  reload, NVMe root check (`findmnt`), hostname, real sshd restart, Armbian
  specifics (`armbian-ramlog`), RK3588 Mali/RKNN drivers (needs the Rockchip
  kernel and `/dev` nodes), k3s itself (kernel features, wireguard flannel).
  Needs `when` guards or skipped tags in the test scenario.
- Could set `inventory_hostname` to the real names for host-conditional logic.
- Effort: medium (new `molecule/` dir, a converge playbook, a CI job).
  Risk: none to the cluster.

### 5. Canary host (spare Pi, VM, or local Lima VM)

- A spare Pi, or a VM on the Mac (Lima/UTM with an arm64 Debian image: native
  speed, real systemd, real reboots, real sshd).
- Separate inventory file (for example `inventory-canary.yaml`, not in
  `k3s_cluster`), so a stray `site.yaml` run cannot reach it by accident, and
  the real hosts cannot be reached when it is the only `-i`. Because the vars
  live in `inventory.yaml`, the canary file must duplicate or import them
  (moving them to `group_vars/` would make that clean).
- Can run single-node k3s as a server to test install, config flags
  (secrets encryption, `server_config_yaml`) and kube-vip manifest loading.
  Cannot test etcd quorum behaviour, `serial: 1` rolling upgrades across 3
  nodes, Longhorn replica health, or RK3588 drivers (unless the spare is a
  real RK1, which is the only way to test drivers).
- Effort: medium (hardware or VM, inventory, snapshot/restore routine).
  Risk: low if isolated on the network and with its own SSH key.

### 6. Ephemeral QEMU arm64 VMs in CI

- Boot a Debian arm64 cloud image with QEMU on an arm64 runner and run the
  playbooks against it. Gives reboots and a full systemd boot.
- Cons: slow without KVM (nested virtualisation is often unavailable on
  hosted runners), cloud-init plumbing, still no Rockchip hardware. Probably
  overkill next to a local Lima VM. Effort: high. Risk: none.

### 7. Testing sshd changes safely

- `validate: sshd -t -f %s` is already there for `lineinfile`. Keep it for any
  drop-in as well (`sshd -t` before restart/reload).
- Before applying: `ansible.builtin.assert` that `~/.ssh/authorized_keys`
  exists and is non-empty for `ansible_user`, otherwise
  `PasswordAuthentication no` locks us out.
- Keep a second SSH session open during the first real run (the
  `ControlPersist=60s` from `ansible.cfg` does not count as an independent
  session).
- Timed rollback: before changing sshd, schedule a restore with `at` (or a
  `systemd-run --on-active=5m` timer) that copies back the backup config and
  restarts sshd. Cancel it with a follow-up task that proves a new connection
  works (`wait_for_connection` from a fresh connection, or a delegated
  `ssh -o ControlPath=none`). Add the backup with `backup: true` on the edit.
- Prefer `reload` over `restart` where valid, and apply to one node at a time
  (`serial: 1`).
- Effort: low-medium. Risk: the rollback mechanism itself must be tested on
  the canary first.

### 8. Idempotence (run twice)

- Second run must report `changed=0`. Several tasks are forced changed
  (`changed_when: true` on `tic`, `ldconfig`, `udevadm`, `update-initramfs`),
  so a naive check will fail; either make those conditional on real changes
  or filter them in the assertion.
- Works in Molecule/Lima/canary only, never against production for the
  mutating run. Effort: low once item 4 or 5 exists.

### 9. In-playbook pre-flight `assert`s

- Cheap and run for real everywhere, also under `--check`:
  `findmnt` shows NVMe (already present), `authorized_keys` present before
  sshd hardening, `kubectl get nodes` shows all other servers `Ready` before
  touching a server, etcd health before `serial: 1` steps, no cordoned nodes
  already, kube-vip VIP answering on 6443.
- Fail early instead of half-applying. Does not prove the change works.
  Effort: low. Risk: low (an assert that is too strict blocks a run).

### 10. Human-run only, Claude plans

- Claude writes the change and a runbook: exact command with `--check --diff
  -l <host>`, what to look for, expected `changed` counts, rollback steps.
  The owner runs it and pastes the output back; Claude reviews it.
- No new tooling, no access for the agent. Slower feedback loop, relies on
  the owner. Effort: none. Risk: lowest. Good baseline for every PR that
  touches `ansible/`.

## Enforcing "never run without `--check` and `-l canary`"

The deny list already blocks `Bash(ansible-playbook *)`, `Bash(ansible *)` and
`Bash(ansible-pull *)`. Options to open it up in a controlled way:

| Option | How | Risks |
| --- | --- | --- |
| A. Keep deny, humans run | Status quo | None, but no agent feedback |
| B. Allow a narrow pattern | `Bash(ansible-playbook --check --diff -l canary *)` | Prefix matching is easy to defeat: extra args later (`-e`, `-l '*'` repeated, `--extra-vars @file`), `mise x -- ansible-playbook`, `python -m ansible`, absolute path, `bash -c`. Deny rules do not cover those spellings |
| C. Wrapper script | `scripts/ansible-canary` hard-codes `--check --diff -i inventory-canary.yaml -l canary`, rejects any other flag, allow only that script | If the wrapper is in the repo, the agent can edit it (or the playbook, `ansible.cfg`, `check_mode: false`) on its branch. Keep it outside the repo or owner-controlled and read-only; `ANSIBLE_*` env vars and `ansible.cfg` also change behaviour |
| D. Credentials, not commands | Dedicated SSH key and user that exist only on the canary; real hosts never trust it; canary-only inventory | Strongest because it does not depend on parsing commands. The agent's own `ssh` and the user's default key/agent must also be unavailable (no `ssh-agent` forwarding, `Bash(ssh *)` is not in the deny list today) |
| E. Sandbox | Run the agent where the cluster network is unreachable, with only the canary reachable | Needs network setup; most robust together with D |

Recommendation: D plus C, and keep the existing deny entries as a second
layer. Do not rely on B alone.

## Secrets

- `inventory.yaml` has IPs, usernames and the k3s version, no secrets. Keep it
  that way: new secrets (a secrets-encryption key, the k3s token) must come
  from the environment or a SOPS-decrypted file read by the owner, never be
  committed, and never be readable by the agent
  ([AGENTS.md](../AGENTS.md) forbids it, `sops -d` is denied).
- The canary must use throwaway values for every secret, so test output and
  logs never contain production material.
- `--diff` output can contain file contents: set `no_log: true` and
  `diff: false` on tasks that handle secrets, and do not paste unredacted
  output into PRs or chat.
- `fact_caching` writes to `ansible/.ansible/cache/facts`; keep it untracked
  and off CI artifacts.

## Comparison

| Idea | Catches | Misses | Effort | Risk |
| --- | --- | --- | --- | --- |
| 1 ansible-lint | Style, many bugs | Behaviour | Trivial | None |
| 2 `--syntax-check` | Parse errors | Same as 1 | Trivial | None |
| 3 `--check --diff` on prod | Real drift and diffs | Commands, reboot, k3s, drain | Low-med | Low (prod access) |
| 4 Molecule/containers | Logic, idempotence, handlers | Kernel, hardware, reboot | Medium | None |
| 5 Canary host/VM | Reboot, sshd, k3s single node | Quorum, Longhorn, drivers | Medium | Low |
| 6 QEMU in CI | Same as 5, automated | Same as 5 | High | None |
| 7 sshd safeguards | Lockouts | Other services | Low-med | Low |
| 8 Idempotence | Non-idempotent tasks | First-run bugs | Low | None |
| 9 Pre-flight asserts | Bad preconditions | Bad logic | Low | Low |
| 10 Human-run, Claude plans | Everything the owner checks | Nothing automated | None | Lowest |

## Recommended order

1. **First step:** make `configure.yaml`, `longhorn.yaml` and `kube-vip.yaml`
   check-mode clean (`check_mode: false` on the read-only `command` tasks) and
   adopt idea 10 for every `ansible/` PR: Claude supplies the
   `--check --diff -l <host>` command, the owner runs it and pastes the
   output.
2. Add pre-flight asserts (idea 9) for sshd hardening and server-touching
   plays, plus the `sshd -t` and timed rollback pattern (idea 7).
3. Move vars to `group_vars/` and add a local Lima/VM canary with its own
   inventory and SSH key (idea 5), with a dedicated credential (enforcement D).
4. Run playbooks twice there for idempotence (idea 8).
5. Add the wrapper script (enforcement C) and only then allow the agent to run
   it against the canary.
6. Molecule/containers in CI (idea 4) for the pure config plays. Skip QEMU in
   CI (idea 6) unless the local VM proves too manual.
7. Keep ansible-lint as is (ideas 1 and 2).
