# Armbian

This contains my userpatches folder to build Armbian for my RK1 nodes. To keep
the $ROOT_PASSWORD out of the repo, I'm templating the presets used by
.not_yet_logged_in or firstboot.conf.

This can probably be done with a hook, but I couldn't figure out how to get
passwords from my keyring into the build environment.
