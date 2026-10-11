#!/usr/bin/env sh
set -eu
dir="$HOME/.local/share/freddy-bridge"
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$dir" "$HOME/.config/systemd/user"
python3 -m venv "$dir/venv"
"$dir/venv/bin/pip" install -q -r "$here/requirements.txt"
cp "$here/freddy_bridge.py" "$dir/freddy_bridge.py"
cp "$here/freddy-bridge.service" "$HOME/.config/systemd/user/freddy-bridge.service"
if [ ! -f "$HOME/.config/freddy-bridge.env" ]; then
    cp "$here/freddy-bridge.env.example" "$HOME/.config/freddy-bridge.env"
    chmod 600 "$HOME/.config/freddy-bridge.env"
    echo "fill in $HOME/.config/freddy-bridge.env, then run:"
    echo "  systemctl --user daemon-reload && systemctl --user enable --now freddy-bridge"
    exit 0
fi
systemctl --user daemon-reload
systemctl --user enable --now freddy-bridge
echo "freddy-bridge is running: journalctl --user -u freddy-bridge -f"
