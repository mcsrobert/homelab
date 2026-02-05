#!/bin/bash
set -e

echo "Provisioning started..."

echo "Installing packages for Ansible..."

apt update
apt install -y \
  fail2ban \
  python3 \
  python3-apt

echo "Setting hostname based on MAC address..."

INTERFACE=$(ip route | awk '/default/ {print $5}')

if [ -z "$INTERFACE" ]; then
    echo "Could not detect a network interface with a default route."
    exit 1
fi

MAC_ADDRESS=$(cat "/sys/class/net/${INTERFACE}/address" | tr '[:lower:]' '[:upper:]')

case "$MAC_ADDRESS" in
    "EE:E1:75:EC:A2:41")
        NEW_HOSTNAME="turingpi-rk1-1"
        ;;
    "2E:23:D1:91:03:98")
        NEW_HOSTNAME="turingpi-rk1-2"
        ;;
    "72:D8:E6:5C:E5:39")
        NEW_HOSTNAME="turingpi-rk1-3"
        ;;
    *)
        echo "MAC address $MAC_ADDRESS on interface $INTERFACE not recognized."
        exit 1
        ;;
esac

hostnamectl set-hostname "$NEW_HOSTNAME"


echo "Provisioning complete"
