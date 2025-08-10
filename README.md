# Homelab

This repo contains my IaC homelab, which is managed with Flux and Renovate.

## Hardware

The cluster is running [k3s](https://k3s.io/) on the following nodes:

| Device                                                                          | Description | CPU | RAM  | Architecture | OS             |
| ------------------------------------------------------------------------------- | ----------- | --- | ---- | ------------ | -------------- |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/Turing)                 | Master      | 8   | 16GB | ARM64        | Ubuntu         |
| [Turing Pi RK1](https://turingpi.com/product/turing-rk1/Turing)                 | Master      | 8   | 16GB | ARM64        | Ubuntu         |
| [Raspberry Pi 4B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | Master      | 4   | 8GB  | ARM64        | Raspberry Pi OS |

## Thanks

Heavily inspired by: https://github.com/cbirkenbeul/homelab
