# Homelab

My Kubernetes-based homelab, managed with FluxCD, Ansible, GitHub Actions and
Renovate.

The goal is to run various self hosted apps I use (smart-home, media,
password/document managers, etc.) on a low power Kubernetes cluster and follow
best practices. I aim to deliver stable, fast and secure services, while
continuing to learn about Kubernetes and GitOps.

## Repository

This repository currently consists of 3 main directories:

- [armbian](./armbian): build configuration for a custom Armbian image for my
  RK1 compute modules. This is based on the 6.1 BSP kernel for NPU support.
- [ansible](./ansible/): playbooks to install/upgrade packages, k3s, drivers
  and kube-vip.
- [kubernetes](./kubernetes/): cluster bootstrap and Kubernetes manifests for
  FluxCD.

## Hardware

My homelab consists of a small k3s cluster and a NAS.

| Device | Role | SoC | CPU | RAM | Storage | Architecture | OS |
| ------ | ---- | --- | --- | --- | ------- | ------------ | -- |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s Master | Rockchip RK3588 | 4x Cortex-A76 + 4x Cortex-A55 | 32GB LPDDR4 | 512GB NVMe | ARM64 | Armbian |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s Master | Rockchip RK3588 | 4x Cortex-A76 + 4x Cortex-A55 | 16GB LPDDR4 | 512GB NVMe | ARM64 | Armbian |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s Master | Rockchip RK3588 | 4x Cortex-A76 + 4x Cortex-A55 | 16GB LPDDR4 | 512GB NVMe | ARM64 | Armbian |
| [Raspberry Pi 4B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | k3s Worker | Broadcom BCM2711 | 4x Cortex A72 | 8GB LPDDR4 | 128GB USB | ARM64 | Raspberry Pi OS |
| Synology DS923+ | NAS | - | 2x AMD Ryzen 1600 | 4GB DDR4 ECC | 4x 4TB HDD (RAID5) | x86-64 | DSM |

## Cloud Dependencies

| Service | Use | Cost | Notes |
| ------- | --- | ---- | ----- |
| [Cloudflare](https://www.cloudflare.com/products/registrar/) | Domain | ~€17/yr | |
| [Let's Encrypt](https://letsencrypt.org/) | Certificates | Free | |
| [Doppler](https://www.doppler.com/) | External Secrets | Free | |
| [Tailscale](https://tailscale.com/) | VPN without port forwarding | Free | |
| [GitHub](https://github.com/) | Hosting of this repository + CI | Free | |
| [Simple Mail Service](https://simplemailservice.eu) | SMTP | Free | |
| [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/bx21/) | Remote backup (5TB) | ~€13/mo | rclone sync + snapshots |

## Acknowledgements

Using [kubesearch.dev](https://kubesearch.dev), I've found a lot of other
repositories that have helped me along the way. Big thanks to:

- [cbirkenbeul/homelab](https://github.com/cbirkenbeul/homelab)
- [bjw-s/home-ops](https://github.com/bjw-s/home-ops)
- [mapanare-labs/home-ops](https://gitlab.com/mapanare-labs/mapanarenet/home-ops)
- [tyriis/home-ops](https://github.com/tyriis/home-ops)
- [drag0n141/home-ops](https://github.com/drag0n141/home-ops)
- [budimanjojo/home-cluster](https://github.com/budimanjojo/home-cluster)
- [nicolerenee/infra](https://github.com/nicolerenee/infra)
- [h3mmy/bloopySphere](https://github.com/h3mmy/bloopySphere)
