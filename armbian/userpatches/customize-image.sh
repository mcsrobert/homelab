#!/bin/bash

# Armbian runs this hook inside the image chroot and bind-mounts userpatches/overlay at /tmp/overlay.

if [ -d /tmp/overlay ]; then
    cp -R /tmp/overlay/* /
fi
