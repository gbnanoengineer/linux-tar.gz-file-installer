# Tar.gz Package Installer (`targz-installer`)

[![Platform: Ubuntu 22.04 | 24.04 | 26.04](https://img.shields.io/badge/Platform-Ubuntu%2022.04%20%7C%2024.04%20%7C%2026.04-orange?logo=ubuntu)](https://ubuntu.com)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)](https://python.org)
[![GUI: GTK 3 / Tkinter](https://img.shields.io/badge/GUI-GTK%203%20%2F%20Tkinter-informational)](https://www.gtk.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

**Tar.gz Package Installer (`targz-installer`)** is a native Ubuntu desktop utility and command-line tool for inspecting, installing, managing, and uninstalling `.tar.gz`, `.tgz`, and `.tar.xz` packages.

It inspects archives before extraction, resolves missing shared libraries through `apt`, extracts high-resolution application logos, fixes the Ubuntu 24.04 and 26.04 Electron `SIGTRAP` crash, and records installation metadata for clean removal.

---

## Key Features

- **Double-Click Installation (GDebi Experience)**: Associates with `.tar.gz` and `.tgz` files so opening an archive in Ubuntu Files (Nautilus) launches a graphical installer with package metadata, installation progress, and removal support.
- **Automatic Dependency Resolution (`ldd` + `apt`)**: Scans extracted ELF binaries and shared libraries, maps missing shared objects to Ubuntu 24.04 and 26.04 (`t64`-aware) packages, and installs the required dependencies.
- **Ubuntu 24.04 / 26.04 Electron and Chromium `SIGTRAP` Fix**: Resolves crashes caused by `kernel.apparmor_restrict_unprivileged_userns = 1` on modern Electron applications such as Antigravity, VS Code, and Cursor.
- **High-Resolution Logo Extractor**: Analyzes PNG `IHDR` headers to select genuine application logos from 128×128 to 1024×1024, parses embedded Electron `resources/app.asar` files, and sets appropriate world-readable permissions.
- **Security Path Traversal Guard**: Sanitizes archive entries and blocks Zip-Slip / Tar-Slip traversal vectors such as `../` and absolute paths to protect the system from malicious archives.
- **Clean One-Click Uninstallation**: Records extracted files, symlinks in `/usr/local/bin` or `~/.local/bin`, icons, and `.desktop` shortcuts in an installation manifest so they can be removed safely.
- **In-Place Repair Tool (`fix`)**: Repairs existing broken installations, such as an already-extracted `/opt/antigravity` folder, with a single command.

---

## Archive Support Matrix

| Archive Architecture | Detection Method | Automated Installation Behavior |
| :--- | :--- | :--- |
| **Pre-compiled Binary Apps** (Antigravity, Postman, Blender, Firefox, custom binaries) | Root or `bin/` executables, `chrome-sandbox`, or `.so` libraries | Extracts to `/opt/<app>` or `~/.local/opt/<app>`, creates launchers and desktop integration, and resolves dependencies |
| **Bundled Installer Scripts** | Root `install.sh` or `setup.sh` | Extracts the archive and executes the bundled installation script with streaming log output |
| **Autotools Source** | Root `configure` script | Runs `./configure --prefix=...`, `make -j$(nproc)`, and `make install`, then links built binaries |
| **CMake Source** | Root `CMakeLists.txt` | Runs `cmake -B build`, compiles in parallel, and installs to the selected prefix |
| **Meson Source** | Root `meson.build` | Runs `meson setup build`, `meson compile`, and `meson install` |
| **Python Packages** | Root `pyproject.toml` or `setup.py` | Installs the package in an isolated environment within the target prefix via `pip` |

---

## Quick Installation

Clone or download the repository, then run the installer script:

```bash
# Make the scripts executable
chmod +x install_targz_installer.sh targz_installer.py

# Install for the current user (~/.local) and register file associations
bash install_targz_installer.sh

# Or install system-wide (/usr/local/bin)
sudo bash install_targz_installer.sh --system
```
