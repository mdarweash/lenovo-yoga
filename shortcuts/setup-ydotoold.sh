#!/bin/bash
# Sets up and starts the ydotoold systemd service

REAL_USER=${SUDO_USER:-$USER}

sudo tee /etc/systemd/system/ydotoold.service > /dev/null << EOF
[Unit]
Description=ydotool daemon
After=local-fs.target

[Service]
Type=simple
User=$REAL_USER
Group=$REAL_USER
ExecStart=/usr/bin/ydotoold

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl restart ydotoold
