#!/bin/bash

# Armbian runs this hook inside the image chroot and bind-mounts userpatches/overlay at /tmp/overlay.

RELEASE="$1"
LINUXFAMILY="$2"
BOARD="$3"
BUILD_DESKTOP="$4"

if [ -d /tmp/overlay ]; then
    cp -R /tmp/overlay/* /
fi
