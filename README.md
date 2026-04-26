# Homelab

My GitOps-driven homelab running ~20 self-hosted applications on a low-power ARM
Kubernetes cluster. Infrastructure and application configuration are fully
declarative and managed through [FluxCD](https://fluxcd.io),
[Ansible](https://docs.ansible.com), [GitHub
Actions](https://docs.github.com/en/actions) and
[Renovate](https://docs.github.com/en/actions); covering everything from initial
OS image builds to automated dependency updates.

## Repository

This repository currently consists of 3 main directories:

- [Armbian](./armbian): builds a custom image for the RK1 compute modules,
  based on the 6.1 BSP kernel for NPU support. Also pre-configures various
  settings such as disabling swap.

- [Ansible](./ansible/): playbooks to configure hosts, install/upgrade packages,
  k3s, GPU/NPU drivers and kube-vip and more.

- [Kubernetes](./kubernetes/): cluster bootstrap and manifests for FluxCD.
  Manifests are structured following:
  `apps/<namespace>/<app>/<kustomization>/<name>.yaml`, where each
  app has one or more FluxCD Kustomizations, which can depend on each other.
  For example: `apps/default/immich/cnpg/database.yaml`.

## Hardware

My homelab consists of a small k3s cluster and a NAS.

| Device | Role | SoC | CPU | RAM | Storage | OS |
| ------ | ---- | --- | --- | --- | ------- | -- |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s server | RK3588 | 4x Cortex-A76 + 4x Cortex-A55 | 32GB LPDDR4 | 512GB NVMe | Armbian |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s server | RK3588 | 4x Cortex-A76 + 4x Cortex-A55 | 16GB LPDDR4 | 512GB NVMe | Armbian |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s server | RK3588 | 4x Cortex-A76 + 4x Cortex-A55 | 16GB LPDDR4 | 512GB NVMe | Armbian |
| [Raspberry Pi 4B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | k3s agent | BCM2711 | 4x Cortex A72 | 8GB LPDDR4 | 128GB USB | Raspberry Pi OS |
| Synology DS923+ | NAS | n/a | 2x AMD Ryzen 1600 | 4GB DDR4 ECC | 4x 4TB HDD (RAID5) | DSM |

## Software

Quick overview of the core components of my stack and why they were picked
(over other options). Due to the limited hardware, I often prefer light-weight
options, as long as they offer enough features.

### Kubernetes

- [k3s](https://k3s.io) - Kubernetes distribution that is resource optimized and
  works well on ARM. Easier to play with the NPU than Talos.
- [FluxCD](https://fluxcd.io) - GitOps for Kubernetes with native
  [SOPS](https://getsops.io) support.
- [flux-operator](https://fluxoperator.dev) - Fully declarative FluxCD deployments.

### Storage

- [Longhorn](https://longhorn.io) - Kubernetes native block storage. Lighter
  alternative to Rook-Ceph.
- [Garage](https://garagehq.deuxfleurs.fr) - Lightweight S3 object storage. Used
  for [CNPG](https://cloudnative-pg.io) [Barman](https://pgbarman.org)
  [Cloud](https://docs.pgbarman.org/release/3.12.1/user_guide/barman_cloud.html)
  [Plugin](https://cloudnative-pg.io/plugin-barman-cloud/docs/usage/) backups.
- [NFS](https://en.wikipedia.org/wiki/Network_File_System) - Various large data
  sets that are not latency sensitive are mounted directly with NFS (e.g. Immich
  library, Garage data). Longhorn volumes are backed up via NFS too.
- [rclone](https://rclone.org) - Syncs an encrypted copy of the NAS for off-site
  disaster recovery. Used in combination with immutable snapshots to protect
  against ransomware.

### Network

- [Flannel](https://github.com/flannel-io/flannel) - CNI provided by k3s.
  Considering Cilium for more features.
- [MetalLB](https://metallb.io) - Bare-metal load balancer using Layer 2.
  Replaces ServiceLB offered by k3s, as it only works on host IPs.
- [Traefik](https://traefik.io) - Application proxy. I've replaced the one
  offered by k3s with my own install for more control and recently migrated from
  Ingress to the [Gateway API](https://gateway-api.sigs.k8s.io). Considering
  switching to Envoy.

### Observability

- [Victoria Metrics](https://victoriametrics.com/products/open-source/) - Scrape
  and store metrics. Lighter alternative to Prometheus.
- [Victoria Logs](https://victoriametrics.com/products/victorialogs/) - Collect
  and store logs. Lighter alternative to Loki.
- [Grafana](https://grafana.com) - UI to explore data and dashboards

### Security

- [External Secrets](https://external-secrets.io) - Syncs secrets from external
  providers, avoiding hardcoded credentials.
- [Kyverno](https://kyverno.io) - Kubernetes-native policies for security and
  automation.

### AuthN / AuthZ

- [Authelia](https://www.authelia.com) - Provides OIDC and can act as a
  ForwardAuth proxy. Lighter alternative to Authentik or Keycloak. Tried Pocket
  ID, but it doesn't offer ForwardAuth.
- [LLDAP](https://github.com/lldap/lldap) - Light LDAP backend for Authelia,
  with a UI for user management.

## Cloud Dependencies

Even though it's a homelab, I still have a bunch of cloud dependencies. I pay
for the critical ones, and I use a bunch of free services that are nice to
have. In the future I might self-host some of these. I have recently started to
prefer EU-based cloud services.

| Service | Use | Cost | Notes |
| ------- | --- | ---- | ----- |
| [Cloudflare](https://www.cloudflare.com/products/registrar/) | Domain | ~€17/yr | |
| [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/bx21/) | Remote backup (5TB) | ~€13/mo | |
| [Let's Encrypt](https://letsencrypt.org/) | Certificates | Free | |
| [Doppler](https://www.doppler.com/) | External Secrets | Free | See [#199](https://github.com/mcsrobert/homelab/issues/199) |
| [Tailscale](https://tailscale.com/) | VPN without port forwarding | Free | |
| [GitHub](https://github.com/) | Hosting of this repository + CI | Free | |
| [Simple Mail Service](https://simplemailservice.eu) | SMTP | Free | |
| [ntfy.sh](https://ntfy.sh) | Notifications | Free | |

## Acknowledgements

I have been inspired by many other homelab repositories along the way that I've
found with [kubesearch.dev](https://kubesearch.dev). If your repo has helped me,
I'll have starred it, thanks!
