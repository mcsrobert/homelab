# Armbian

This contains my [userpatches/](./userpatches/) folder to build Armbian for my
RK1 nodes. The committed
[.not_logged_in_yet](./userpatches/overlay/root/.not_logged_in_yet) only
holds placeholders. [generate_presets.sh](./generate_presets.sh) fills in the
passwords from my keyring and tells git to ignore the local changes. This can
probably be done with a hook, but I couldn't figure out how to get passwords
from my keyring into the build environment.

I also do minimal provisioning in
[provisioning.sh](./userpatches/overlay/root/provisioning.sh). The rest is
done via Ansible.
