# Homelab

This repo contains my IaC homelab, which is managed with Flux and Renovate.

## Cloud Dependencies

| Service | Use | Cost |
| ------- | --- | ---- |
| [Cloudflare](https://www.cloudflare.com/products/registrar/) | Domain | ~€17/yr |
| [Let's Encrypt](https://letsencrypt.org/) | Certificates | Free |
| [Doppler](https://www.doppler.com/) | External Secrets | Free |
| [Tailscale](https://tailscale.com/) | VPN without port forwarding | Free |
| [GitHub](https://github.com/) | Hosting of this repository + CI | Free |

## Hardware

The cluster is running [k3s](https://k3s.io/) on the following nodes:

|  Device | Description | CPU | RAM | Architecture | OS |
| ------- | ------------| --- | --- | ------------ | -- |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/Turing) | Master | 8 | 16GB | ARM64 | Ubuntu |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/Turing) | Master | 8 | 16GB | ARM64 | Ubuntu |
| [Raspberry Pi 4B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | Master | 4 | 8GB | ARM64 | Raspberry Pi OS |

## Awknowledgements

 - [cbirkenbeul/homelab](https://github.com/cbirkenbeul/homelab)
 - [bjw-s/home-ops](https://github.com/bjw-s/home-ops)
 - [mapanare-labs/home-ops](https://gitlab.com/mapanare-labs/mapanarenet/home-ops)
 - [tyriis/home-ops](https://github.com/tyriis/home-ops)
 - [drag0n141/home-ops](https://github.com/drag0n141/home-ops)
