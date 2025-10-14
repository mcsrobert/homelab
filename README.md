# Homelab

This repo contains my IaC homelab, which is managed with Flux and Renovate.

My goal is to run various applications I use, such as Home Assistant and
Immich, on a low power Kubernetes cluster. I want to deliver stable, fast and
secure applications, while continuing to learn about Kubernetes and GitOps.

## Cloud Dependencies

| Service | Use | Cost | Notes |
| ------- | --- | ---- | ----- |
| [Cloudflare](https://www.cloudflare.com/products/registrar/) | Domain | ~€17/yr | Had [issues](https://www.reddit.com/r/selfhosted/comments/164nc4x/duckdns_servers_are_having_issues_lately/) with DuckDNS |
| [Let's Encrypt](https://letsencrypt.org/) | Certificates | Free | |
| [Doppler](https://www.doppler.com/) | External Secrets | Free | |
| [Tailscale](https://tailscale.com/) | VPN without port forwarding | Free | |
| [GitHub](https://github.com/) | Hosting of this repository + CI | Free | |
| [Simple Mail Service](https://simplemailservice.eu) | SMTP | Free | |
| [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/bx21/) | Remote backup (5TB) | ~€13/mo | Running rclone from my NAS |

## Hardware

The cluster is running [k3s](https://k3s.io/) on the following nodes:

|  Device | Description | CPU | RAM | Architecture | OS |
| ------- | ------------| --- | --- | ------------ | -- |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | Master | 8 | 16GB | ARM64 | Ubuntu |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/) | Master | 8 | 16GB | ARM64 | Ubuntu |
| [Raspberry Pi 4B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | Master | 4 | 8GB | ARM64 | Raspberry Pi OS |

I also have a Synology NAS I use for backing up Longhorn volumes and storing media.

## Awknowledgements

 - [cbirkenbeul/homelab](https://github.com/cbirkenbeul/homelab)
 - [bjw-s/home-ops](https://github.com/bjw-s/home-ops)
 - [mapanare-labs/home-ops](https://gitlab.com/mapanare-labs/mapanarenet/home-ops)
 - [tyriis/home-ops](https://github.com/tyriis/home-ops)
 - [drag0n141/home-ops](https://github.com/drag0n141/home-ops)
 - [budimanjojo/home-cluster](https://github.com/budimanjojo/home-cluster)
