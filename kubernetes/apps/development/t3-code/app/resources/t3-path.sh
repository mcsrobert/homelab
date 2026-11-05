# /etc/profile resets PATH for login shells, so restore the installed tools.
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
