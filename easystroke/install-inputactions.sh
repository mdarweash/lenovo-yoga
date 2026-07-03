#!/usr/bin/env bash
set -e

# Fix anaconda pkg-config shadowing system libraries
export PATH=/usr/bin:$PATH

bash /home/mdarweash/apps/inputactions/inputactions-installer.sh --kwin
