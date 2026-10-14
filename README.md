# Tar.gz Package Installer (`targz-installer`)

[[https://img.shields.io/badge/Platform-Ubuntu%2022.04%20%7C%2024.04%20%7C%2026.04-orange?logo=ubuntu]](https://ubuntu.com)
[[https://img.shields.io/badge/Python-3.8%2B-blue?logo=python]](https://python.org)
[[https://img.shields.io/badge/GUI-GTK%203%20%2F%20Tkinter-informational]](https://www.gtk.org)
[[https://img.shields.io/badge/License-MIT-green]](LICENSE)

**Tar.gz Package Installer (`targz-installer`)** is a native Ubuntu desktop utility and command-line tool that lets you inspect, install, manage, and uninstall `.tar.gz`, `.tgz`, `.tar.xz`, and `.tar.bz2` software packages just like `.deb` files in GDebi or the Ubuntu App Center.

It automatically inspects archives before extraction, resolves missing shared libraries via `apt`, extracts high-resolution application logos, fixes the Ubuntu 24.04/26.04 Electron `SIGTRAP` crash, creates Ubuntu Application Menu (`.desktop`) launchers, and maintains a registry for clean one-click uninstallation.

---

## Key Features

- **Double-Click Installation (GDebi Experience)**: Associates with `.tar.gz` and `.tgz` files so opening an archive in Ubuntu Files (Nautilus) launches a graphical installer with package metadata, uncompressed size, and an **Install Package** button.
- **Automatic Dependency Resolution (`ldd` + `apt`)**: Scans extracted ELF binaries and `.so` libraries, maps missing shared objects to Ubuntu 24.04 and 26.04 (`t64`-aware) apt packages, and installs them automatically via `apt-get`.
- **Ubuntu 24.04 / 26.04 Electron & Chromium `SIGTRAP` Fix**: Resolves crashes caused by `kernel.apparmor_restrict_unprivileged_userns = 1` on modern Electron apps (such as Antigravity, VS Code, Cursor, Discord, and Postman) by configuring SUID permissions on `chrome-sandbox`, loading AppArmor user namespace profiles, and generating crash-safe launcher wrappers.
- **High-Resolution Logo Extractor**: Analyzes PNG `IHDR` headers to select genuine application logos (128x128 to 1024x1024), parses embedded Electron `resources/app.asar` files, sets world-readable `0644` permissions, and adds `StartupWMClass` so the Ubuntu Dock and App Grid show the true icon instead of a generic gear.
- **Security Path Traversal Guard**: Sanitizes and blocks Zip-Slip / Tar-Slip traversal vectors (`../` and absolute paths) to protect your system from malicious archives.
- **Clean One-Click Uninstallation**: Unlike manual `tar -xzf`, records all extracted files, symlinks in `/usr/local/bin` (or `~/.local/bin`), icons, and `.desktop` shortcuts in an installation manifest for clean removal.
- **In-Place Repair Tool (`fix`)**: Repairs existing broken installations (such as an already-extracted `/opt/antigravity` folder) in place with a single command.

---

## Archive Support Matrix

| Archive Architecture | Detection Method | Automated Installation Behavior |
| :--- | :--- | :--- |
| **Pre-compiled Binary Apps** (Antigravity, Postman, Blender, Firefox, custom binaries) | Root or `bin/` executables, `chrome-sandbox`, or `.so` libraries | Extracts to `/opt/<app>` or `~/.local/opt/<app>`, links binaries to `PATH`, installs high-res icons, and creates `.desktop` launchers |
| **Bundled Installer Scripts** | Root `install.sh` or `setup.sh` | Extracts archive and executes bundled installation script with streaming log output |
| **Autotools Source** | Root `./configure` | Runs `./configure --prefix=...`, `make -j$(nproc)`, and `make install`, then links built binaries |
| **CMake Source** | Root `CMakeLists.txt` | Runs `cmake -B build`, compiles in parallel, and installs to prefix |
| **Meson Source** | Root `meson.build` | Runs `meson setup build`, `meson compile`, and `meson install` |
| **Python Packages** | Root `pyproject.toml` or `setup.py` | Installs isolated package into target prefix via `pip` |

---

## Quick Installation

Clone or download the repository, then run the installer script:

```bash
# Make the scripts executable
chmod +x install_targz_installer.sh targz_installer.py

# Install for current user (~/.local) and register file associations
bash install_targz_installer.sh

# Or install system-wide (/usr/local/bin)
sudo bash install_targz_installer.sh --system
