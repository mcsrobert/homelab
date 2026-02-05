# Homelab

This repo contains my IaC homelab, which is managed with Flux and Renovate.

My goal is to run various self hosted apps I use (such as Home Assistant and
Immich) on a low power Kubernetes cluster. By following best practices, I aim
to deliver stable, fast and secure applications, while continuing to learn
about Kubernetes and GitOps.

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

## Hardware

My homelab consists of a small k3s cluster and a NAS for Longhorn backups
and media.

|  Device | Role | SoC | CPU | RAM | Storage | Architecture | OS |
| ------- | ---- | --- | --- | --- | ------- | ------------ | -- |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s Master | Rockchip RK3588 | 4x Cortex-A76 +<br>4x Cortex-A55 | 16GB LPDDR4-4224 | 512GB SSD | ARM64 | Armbian |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s Master | Rockchip RK3588 | 4x Cortex-A76 +<br>4x Cortex-A55 | 16GB LPDDR4-4224 | 512GB SSD | ARM64 | Armbian |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | k3s Master | Rockchip RK3588 | 4x Cortex-A76 +<br>4x Cortex-A55 | 32GB LPDDR4-4224 | 512GB SSD | ARM64 | Armbian |
| [Raspberry Pi 4B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | k3s Worker | Broadcom BCM2711 | 4x Cortex A72 | 8GB LPDDR4-2400 | 128GB USB | ARM64 | Raspberry Pi OS |
| Synology DS923+ | NAS | n/a | 2x AMD Ryzen 1600 | 4GB DDR4 ECC | 4x 4TB HDD in RAID5 | x86-64 | DSM |

## Awknowledgements

Using [kubesearch.dev](https://kubesearch.dev), I've found a lot of other
repo's that have helped me along the way. Big thanks to:

 - [cbirkenbeul/homelab](https://github.com/cbirkenbeul/homelab)
 - [bjw-s/home-ops](https://github.com/bjw-s/home-ops)
 - [mapanare-labs/home-ops](https://gitlab.com/mapanare-labs/mapanarenet/home-ops)
 - [tyriis/home-ops](https://github.com/tyriis/home-ops)
 - [drag0n141/home-ops](https://github.com/drag0n141/home-ops)
 - [budimanjojo/home-cluster](https://github.com/budimanjojo/home-cluster)
 - [nicolerenee/infra](https://github.com/nicolerenee/infra)
 - [h3mmy/bloopySphere](https://github.com/h3mmy/bloopySphere)
