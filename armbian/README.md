# Armbian

This contains my [userpatches/](./userpatches/) folder to build Armbian for my
RK1 nodes. To keep the $ROOT_PASSWORD out of the repo, I'm templating the
presets used by `.not_yet_logged_in`.

This is done with [generate_presets.sh](./generate_presets.sh). This can
probably be done with a hook, but I couldn't figure out how to get passwords
from my keyring into the build environment.

I also do minimal provisioning in
[provisioning.sh](./userpatches/overlay/root/provisioning.sh). The rest is
done via Ansible.
