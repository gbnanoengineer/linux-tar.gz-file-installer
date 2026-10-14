#!/usr/bin/env bash
# One-step Ubuntu setup & Antigravity auto-repair script for Tar.gz Package Installer v2.0
# 1. Installs targz-installer v2.0 (with automatic ldd/apt dependency resolution, high-res icon extraction, and Ubuntu 24.04/26.04 Electron SIGTRAP fix)
# 2. Automatically repairs /opt/antigravity if already installed on your machine!

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SCRIPT="${SCRIPT_DIR}/targz_installer.py"

if [[ ! -f "${APP_SCRIPT}" ]]; then
    echo "Error: targz_installer.py must be in the same folder as this script (${SCRIPT_DIR})."
    exit 1
fi

chmod +x "${APP_SCRIPT}"

echo "==> Installing Tar.gz Package Installer v2.0..."
sudo python3 "${APP_SCRIPT}" --self-install

if [[ -d "/opt/antigravity" ]]; then
    echo ""
    echo "==> Detected existing /opt/antigravity installation!"
    echo "    Auto-repairing Antigravity icon, missing libraries, and Ubuntu 26.04 SIGTRAP sandbox..."
    sudo python3 "${APP_SCRIPT}" fix /opt/antigravity
fi

echo ""
echo "All done! You can now launch Antigravity from your Ubuntu Applications menu without crashing,"
echo "or open any .tar.gz file with 'Tar.gz Package Installer'."
