#!/bin/bash

set -e

PRESETS_TEMPLATE=./presets.template
PRESETS_OUTPUT=./userpatches/overlay/root/.not_logged_in_yet

export ROOT_PASSWORD=$(security find-generic-password -s rk1-armbian-k3s -a root -w)
export USER_PASSWORD=$(security find-generic-password -s rk1-armbian-k3s -a user -w)

if [ ! -f "$PRESETS_TEMPLATE" ]; then
    echo "Error: Template not found at $PRESETS_TEMPLATE"
    exit 1
fi

envsubst < "$PRESETS_TEMPLATE" > "$PRESETS_OUTPUT"
echo "Successfully generated: $PRESETS_OUTPUT"
