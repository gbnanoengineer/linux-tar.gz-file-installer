#!/usr/bin/env python3
"""
TarGz Package Installer (targz-installer) v2.0.0
Install, repair, manage, and uninstall .tar.gz / .tgz archives on Ubuntu just like .deb packages.

What's New in v2.0:
  - Automatic Shared Library & Build Dependency Installer:
      * Scans extracted ELF binaries & .so files with `ldd` (respecting bundled libraries)
      * Maps missing .so libraries to Ubuntu 24.04 / 26.04 (t64-aware) apt packages and auto-installs via `apt-get`
      * Auto-installs build tools (build-essential, cmake, meson, ninja-build, python3-pip) for source archives
  - Ubuntu 24.04 / 26.04 Electron & Chromium SIGTRAP Crash Fix (for Antigravity, VS Code, Cursor, Postman, etc.):
      * Detects Electron / Chromium apps (`chrome-sandbox`, `libffmpeg.so`, `icudtl.dat`, `v8_context_snapshot.bin`)
      * Sets `chown root:root` and `chmod 4755` on `chrome-sandbox`
      * Creates & loads an Ubuntu AppArmor profile (`/etc/apparmor.d/targz-<app>`) with `userns` and `flags=(unconfined)`
      * Generates a crash-proof launcher wrapper that sets `LD_LIBRARY_PATH` and passes `--no-sandbox` when needed
  - Smart High-Resolution Icon & Logo Extractor:
      * Inspects actual PNG pixel dimensions (IHDR header) to select square high-res icons (64x64 to 1024x1024)
      * Prioritizes `resources/app/resources/linux/*.png` (used by Antigravity / VS Code forks), `pixmaps/`, and `icons/`
      * Ignores tiny 16x16 UI icons and internal extension/webview CSS SVGs that cause the grey gear icon on GNOME
      * Extracts icons from inside Electron `resources/app.asar` archives if not loose on disk
      * Enforces world-readable `0644` permissions on installed icons and adds `StartupWMClass` for the Ubuntu Dock
  - Live In-Place Repair (`targz-installer fix <path-or-app>` or "Repair / Fix App" button in GUI):
      * Fixes icons, missing dependencies, and `SIGTRAP` crashes on already-installed apps like `/opt/antigravity`
"""

import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
import os
import pathlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
from typing import Callable, Dict, List, Optional, Set, Tuple

APP_VERSION = "2.0.0"
APP_TITLE = "Tar.gz Package Installer"
APP_ID = "org.ubuntu.targz_installer"

ELF_MAGIC = b"\x7fELF"
SHEBANG_MAGIC = b"#!"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ARCHIVE_EXTENSIONS = (
    ".tar.gz",
    ".tgz",
    ".tar.xz",
    ".txz",
    ".tar.bz2",
    ".tbz2",
    ".tar",
)

# Ubuntu 24.04 / 26.04 (t64-aware) shared library -> apt package candidates map
SO_TO_APT_PACKAGES: Dict[str, List[str]] = {
    "libnss3.so": ["libnss3"],
    "libnssutil3.so": ["libnss3"],
    "libsmime3.so": ["libnss3"],
    "libnspr4.so": ["libnspr4"],
    "libatk-1.0.so.0": ["libatk1.0-0t64", "libatk1.0-0"],
    "libatk-bridge-2.0.so.0": ["libatk-bridge2.0-0t64", "libatk-bridge2.0-0"],
    "libcups.so.2": ["libcups2t64", "libcups2"],
    "libdrm.so.2": ["libdrm2"],
    "libgtk-3.so.0": ["libgtk-3-0t64", "libgtk-3-0"],
    "libgdk-3.so.0": ["libgtk-3-0t64", "libgtk-3-0"],
    "libgtk-4.so.1": ["libgtk-4-1"],
    "libgbm.so.1": ["libgbm1"],
    "libasound.so.2": ["libasound2t64", "libasound2"],
    "libxkbcommon.so.0": ["libxkbcommon0"],
    "libxkbcommon-x11.so.0": ["libxkbcommon-x11-0"],
    "libxkbfile.so.1": ["libxkbfile1"],
    "libXcomposite.so.1": ["libxcomposite1"],
    "libXdamage.so.1": ["libxdamage1"],
    "libXfixes.so.3": ["libxfixes3"],
    "libXrandr.so.2": ["libxrandr2"],
    "libXss.so.1": ["libxss1"],
    "libXtst.so.6": ["libxtst6"],
    "libXScrnSaver.so.1": ["libxss1"],
    "libxshmfence.so.1": ["libxshmfence1"],
    "libsecret-1.so.0": ["libsecret-1-0"],
    "libnotify.so.4": ["libnotify4"],
    "libfuse.so.2": ["libfuse2t64", "libfuse2"],
    "libfuse3.so.3": ["libfuse3-3"],
    "libxcb-cursor.so.0": ["libxcb-cursor0"],
    "libxcb-icccm.so.4": ["libxcb-icccm4"],
    "libxcb-image.so.0": ["libxcb-image0"],
    "libxcb-keysyms.so.1": ["libxcb-keysyms1"],
    "libxcb-randr.so.0": ["libxcb-randr0"],
    "libxcb-render-util.so.0": ["libxcb-render-util0"],
    "libxcb-shape.so.0": ["libxcb-shape0"],
    "libxcb-xinerama.so.0": ["libxcb-xinerama0"],
    "libxcb-xkb.so.1": ["libxcb-xkb1"],
    "libatomic.so.1": ["libatomic1"],
    "libgconf-2.so.4": ["libgconf-2-4"],
    "libcanberra-gtk3.so.0": ["libcanberra-gtk3-0"],
    "libayatana-appindicator3.so.1": ["libayatana-appindicator3-1"],
    "libappindicator3.so.1": ["libayatana-appindicator3-1", "libappindicator3-1"],
    "libwebkit2gtk-4.0.so.37": ["libwebkit2gtk-4.0-37", "libwebkit2gtk-4.1-0"],
    "libwebkit2gtk-4.1.so.0": ["libwebkit2gtk-4.1-0"],
    "libGL.so.1": ["libgl1"],
    "libEGL.so.1": ["libegl1"],
    "libOpenGL.so.0": ["libopengl0"],
    "libGLU.so.1": ["libglu1-mesa"],
    "libpulse.so.0": ["libpulse0"],
    "libsndfile.so.1": ["libsndfile1"],
    "libopenal.so.1": ["libopenal1"],
    "libSDL2-2.0.so.0": ["libsdl2-2.0-0"],
    "libvulkan.so.1": ["libvulkan1"],
    "libssl.so.3": ["libssl3t64", "libssl3"],
    "libcrypto.so.3": ["libssl3t64", "libssl3"],
    "libcurl.so.4": ["libcurl4t64", "libcurl4"],
}


@dataclass
class ArchiveAnalysis:
    archive_path: str
    file_size_bytes: int
    uncompressed_size_bytes: int
    app_id: str
    display_name: str
    version: str
    strategy: str  # binary_bundle | install_script | autotools | cmake | meson | python_pkg | makefile
    strategy_description: str
    top_level_dir: Optional[str]
    is_electron_app: bool = False
    executables: List[str] = field(default_factory=list)
    primary_executable: Optional[str] = None
    desktop_files: List[str] = field(default_factory=list)
    icon_files: List[str] = field(default_factory=list)
    primary_icon_member: Optional[str] = None
    install_scripts: List[str] = field(default_factory=list)
    file_count: int = 0
    member_list: List[Tuple[str, int, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class InstalledPackage:
    app_id: str
    display_name: str
    version: str
    scope: str  # "user" | "system"
    strategy: str
    source_archive: str
    installed_at: str
    install_dir: str
    symlinks: List[str] = field(default_factory=list)
    desktop_file: Optional[str] = None
    icon_file: Optional[str] = None
    extra_files: List[str] = field(default_factory=list)


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} GB"


def safe_rmtree(path: pathlib.Path) -> None:
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        subprocess.run(["rm", "-rf", str(path)], check=False)


def strip_dot_slash(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def parse_name_and_version(filename: str) -> Tuple[str, str, str]:
    """Extract clean app_id, display_name, and version from an archive filename."""
    base = os.path.basename(filename)
    for ext in ARCHIVE_EXTENSIONS:
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break

    cleaned = re.sub(
        r"[-_.](linux|ubuntu|debian|x86_64|amd64|x64|arm64|aarch64|i386|i686|gnu|musl)+",
        "",
        base,
        flags=re.IGNORECASE,
    )

    match = re.match(r"^([A-Za-z0-9_.-]+?)[-_][vV]?(\d+(?:\.\d+)+(?:[-._][A-Za-z0-9]+)?)$", cleaned)
    if match:
        raw_name, version = match.group(1), match.group(2)
    else:
        raw_name, version = cleaned, "1.0.0"

    app_id = re.sub(r"[^a-z0-9]+", "-", raw_name.lower()).strip("-") or "custom-app"
    display_name = " ".join(part.capitalize() for part in re.split(r"[-_.]+", raw_name) if part) or "Custom App"
    return app_id, display_name, version


def create_link_or_wrapper(target: pathlib.Path, link_path: pathlib.Path) -> None:
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    try:
        os.symlink(target, link_path)
    except OSError:
        link_path.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
        os.chmod(link_path, 0o755)


def is_safe_tar_path(member_name: str) -> bool:
    """Prevent Zip-Slip / Tar-Slip directory traversal attacks."""
    if member_name.startswith("/") or member_name.startswith("\\"):
        return False
    norm = os.path.normpath(member_name)
    if norm == ".." or norm.startswith("../") or "/../" in norm:
        return False
    return True


def parse_png_dimensions(header_bytes: bytes) -> Optional[Tuple[int, int]]:
    """Read (width, height) from the first 24 bytes of a PNG file."""
    if len(header_bytes) >= 24 and header_bytes[:8] == PNG_MAGIC and header_bytes[12:16] == b"IHDR":
        w, h = struct.unpack(">II", header_bytes[16:24])
        if w > 0 and h > 0:
            return (w, h)
    return None


def score_icon_candidate(
    rel_path: str,
    app_id: str,
    file_size: int = 0,
    png_dims: Optional[Tuple[int, int]] = None,
    svg_head: Optional[str] = None,
) -> int:
    """
    Score an icon candidate so real high-res application logos (like
    `resources/app/resources/linux/code.png` or `antigravity.png`) always beat
    internal extension/webview SVGs or tiny 16x16 UI icons.
    """
    lower_path = rel_path.lower().replace("\\", "/")
    bname = os.path.basename(lower_path)
    stem, ext = os.path.splitext(bname)
    parts = [p for p in lower_path.split("/") if p]

    score = 0

    # 1. Strongly penalize internal code/extension/node_modules/webview asset paths
    bad_dirs = (
        "node_modules",
        "extensions",
        "webview",
        "out",
        "dist",
        "locales",
        "test",
        "tests",
        "docs",
        "smiley",
        "emoji",
        "cursors",
        "status",
        "actions",
        "mimetypes",
    )
    if any(bd in parts[:-1] for bd in bad_dirs):
        score -= 250

    # 2. Strongly boost standard Linux / Electron / VS Code / Antigravity icon directories
    if "resources/app/resources/linux" in lower_path or "resources/linux" in lower_path:
        score += 180
    elif any(gd in parts[:-1] for gd in ("pixmaps", "icons", "hicolor", "branding", "brand", "logo", "resources")):
        score += 90
    elif len(parts) <= 2:
        score += 70

    # 3. Boost known application icon filenames
    app_clean = app_id.replace("-", "")
    if stem in (app_id, app_clean, "code", "icon", "logo", "app", "antigravity", "product_logo", "default"):
        score += 100
    elif app_id in stem or app_clean in stem or "logo" in stem or "icon" in stem:
        score += 65

    # 4. Inspect PNG dimensions if available
    if ext == ".png":
        score += 40  # Prefer raster PNGs over potentially broken/CSS-styled internal SVGs
        if png_dims:
            w, h = png_dims
            aspect = w / float(h) if h else 0.0
            if 0.85 <= aspect <= 1.18:
                # Square icon!
                if 64 <= w <= 1024:
                    score += 140
                    if w in (128, 256, 512, 1024):
                        score += 40
                elif 32 <= w < 64:
                    score += 30
                elif w < 32:
                    score -= 120  # Tiny toolbar/tree icon
            else:
                score -= 150  # Non-square banner or screenshot
        elif file_size > 1500:
            score += 20
        elif file_size < 400:
            score -= 60

    elif ext == ".svg":
        # Check if the SVG uses CSS variables (like VS Code internal theme icons) or lacks viewBox
        if svg_head:
            if "var(--" in svg_head or "currentColor" in svg_head:
                score -= 180
            elif "<svg" in svg_head:
                score += 25
    elif ext in (".xpm", ".ico"):
        score -= 10

    # Slightly prefer shallower paths when all else is equal
    score -= len(parts) * 3
    return score


def extract_icon_from_asar(asar_path: pathlib.Path, app_id: str, dest_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """
    Pure-Python Electron `.asar` archive reader to extract the application logo
    if the app packed its resources inside `resources/app.asar`.
    """
    try:
        with open(asar_path, "rb") as f:
            header_pre = f.read(16)
            if len(header_pre) < 16:
                return None
            _, header_size, _, json_size = struct.unpack("<IIII", header_pre)
            json_bytes = f.read(json_size)
            header_obj = json.loads(json_bytes.decode("utf-8", errors="ignore"))
            data_offset = 8 + header_size

            candidates: List[Tuple[int, str, int, int]] = []  # (score, rel_path, offset, size)

            def walk_node(node: dict, prefix: str = ""):
                files_dict = node.get("files")
                if not isinstance(files_dict, dict):
                    return
                for name, meta in files_dict.items():
                    rel = f"{prefix}/{name}" if prefix else name
                    if "files" in meta:
                        walk_node(meta, rel)
                    elif "offset" in meta and "size" in meta:
                        low = name.lower()
                        if low.endswith((".png", ".svg")):
                            offset = int(meta["offset"])
                            size = int(meta["size"])
                            png_dims = None
                            if low.endswith(".png") and size >= 24:
                                f.seek(data_offset + offset)
                                png_dims = parse_png_dimensions(f.read(24))
                            sc = score_icon_candidate(rel, app_id, size, png_dims=png_dims)
                            candidates.append((sc, rel, offset, size))

            walk_node(header_obj)
            if not candidates:
                return None
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_rel, best_off, best_sz = candidates[0]
            if best_score < 0:
                return None

            ext = os.path.splitext(best_rel)[1].lower() or ".png"
            out_file = dest_dir / f"{app_id}-asar-icon{ext}"
            f.seek(data_offset + best_off)
            out_file.write_bytes(f.read(best_sz))
            os.chmod(out_file, 0o644)
            return out_file
    except Exception:
        return None


def find_best_icon_in_directory(install_dir: pathlib.Path, app_id: str) -> Optional[pathlib.Path]:
    """Scan an extracted application directory (like /opt/antigravity) for its true high-res app logo."""
    candidates: List[Tuple[int, pathlib.Path]] = []

    # 1. Check if resources/app/product.json or package.json mentions an icon or applicationName
    for root, dirs, files in os.walk(install_dir):
        rel_root = os.path.relpath(root, install_dir)
        for fname in files:
            low = fname.lower()
            if not low.endswith((".png", ".svg", ".xpm", ".ico")):
                continue
            fpath = pathlib.Path(root) / fname
            rel_path = f"{rel_root}/{fname}" if rel_root != "." else fname
            try:
                fsize = fpath.stat().st_size
                png_dims = None
                svg_head = None
                if low.endswith(".png") and fsize >= 24:
                    with open(fpath, "rb") as pf:
                        png_dims = parse_png_dimensions(pf.read(24))
                elif low.endswith(".svg") and fsize > 0:
                    with open(fpath, "rb") as sf:
                        svg_head = sf.read(512).decode("utf-8", errors="ignore")
                sc = score_icon_candidate(rel_path, app_id, fsize, png_dims=png_dims, svg_head=svg_head)
                candidates.append((sc, fpath))
            except Exception:
                continue

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_path = candidates[0]
        if best_score >= 20:
            return best_path

    # 2. Fallback: Check inside resources/app.asar if present
    asar_file = install_dir / "resources" / "app.asar"
    if asar_file.is_file():
        extracted = extract_icon_from_asar(asar_file, app_id, install_dir)
        if extracted:
            return extracted

    return candidates[0][1] if candidates else None


class TarGzAnalyzer:
    """Inspects a .tar.gz archive without leaving files on disk and determines how to install it."""

    @staticmethod
    def analyze(archive_path: str) -> ArchiveAnalysis:
        path_obj = pathlib.Path(archive_path).expanduser().resolve()
        if not path_obj.is_file():
            raise FileNotFoundError(f"Archive not found: {archive_path}")

        file_size = path_obj.stat().st_size
        app_id, display_name, version = parse_name_and_version(path_obj.name)

        uncompressed_size = 0
        executables: List[str] = []
        desktop_files: List[str] = []
        icon_scored: List[Tuple[int, str]] = []
        install_scripts: List[str] = []
        member_list: List[Tuple[str, int, str]] = []
        warnings: List[str] = []
        top_dirs: Set[str] = set()

        has_configure = False
        has_cmake = False
        has_meson = False
        has_python_setup = False
        has_makefile = False
        is_electron_app = False

        with tarfile.open(path_obj, "r:*") as tf:
            members = tf.getmembers()
            for m in members:
                if not is_safe_tar_path(m.name):
                    warnings.append(f"Unsafe path blocked: {m.name}")
                    continue

                clean_name = strip_dot_slash(m.name)
                if not clean_name:
                    continue

                parts = clean_name.split("/")
                if len(parts) > 1 or m.isdir():
                    top_dirs.add(parts[0])

                uncompressed_size += m.size
                mode_str = stat.filemode(m.mode) if m.mode else ("drwxr-xr-x" if m.isdir() else "-rw-r--r--")
                member_list.append((clean_name, m.size, mode_str))

                if not m.isfile():
                    continue

                rel_depth = len(parts)
                basename = parts[-1]
                lower_base = basename.lower()

                # Detect Electron / Chromium indicators
                if basename in ("chrome-sandbox", "chrome_crashpad_handler", "v8_context_snapshot.bin", "icudtl.dat", "libffmpeg.so") or clean_name.endswith("resources/app.asar"):
                    is_electron_app = True

                # Try reading product.json or package.json inside resources/app/ to get exact product name & version
                if clean_name.endswith(("resources/app/product.json", "resources/app/package.json")) and m.size < 200_000:
                    try:
                        fobj = tf.extractfile(m)
                        if fobj:
                            meta = json.loads(fobj.read().decode("utf-8", errors="ignore"))
                            prod_name = meta.get("nameLong") or meta.get("productName") or meta.get("applicationName")
                            prod_ver = meta.get("version")
                            if prod_name and isinstance(prod_name, str) and len(prod_name) < 50:
                                display_name = prod_name
                            if prod_ver and isinstance(prod_ver, str) and version == "1.0.0":
                                version = prod_ver
                    except Exception:
                        pass

                # Detect build system files near the root (depth 1 or 2)
                if rel_depth <= 2:
                    if basename == "configure":
                        has_configure = True
                    elif basename == "CMakeLists.txt":
                        has_cmake = True
                    elif basename == "meson.build":
                        has_meson = True
                    elif basename in ("setup.py", "pyproject.toml"):
                        has_python_setup = True
                    elif basename in ("Makefile", "makefile", "GNUmakefile"):
                        has_makefile = True
                    elif lower_base in ("install.sh", "setup.sh", "install"):
                        install_scripts.append(clean_name)

                # Detect .desktop files and score icons
                if lower_base.endswith(".desktop"):
                    desktop_files.append(clean_name)
                elif lower_base.endswith((".png", ".svg", ".xpm", ".ico")):
                    png_dims = None
                    svg_head = None
                    try:
                        if lower_base.endswith(".png") and m.size >= 24:
                            fobj = tf.extractfile(m)
                            if fobj:
                                png_dims = parse_png_dimensions(fobj.read(24))
                        elif lower_base.endswith(".svg") and 0 < m.size < 250_000:
                            fobj = tf.extractfile(m)
                            if fobj:
                                svg_head = fobj.read(512).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                    sc = score_icon_candidate(clean_name, app_id, m.size, png_dims=png_dims, svg_head=svg_head)
                    icon_scored.append((sc, clean_name))

                # Check if file is an executable binary or script
                is_exec_mode = bool(m.mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
                in_bin_dir = "bin" in parts[:-1]
                is_non_exec_asset = ".so" in lower_base or lower_base.endswith(
                    (".a", ".la", ".o", ".py", ".txt", ".md", ".json", ".xml", ".png", ".svg", ".css", ".js", ".html", ".pak", ".bin", ".dat", ".asar")
                )

                if (is_exec_mode or in_bin_dir or lower_base == app_id) and m.size > 0 and not is_non_exec_asset:
                    try:
                        fobj = tf.extractfile(m)
                        if fobj:
                            head = fobj.read(4)
                            if head == ELF_MAGIC or head.startswith(SHEBANG_MAGIC):
                                if not re.search(r"\.so(\.\d+)*$", lower_base):
                                    executables.append(clean_name)
                    except Exception:
                        if is_exec_mode:
                            executables.append(clean_name)

        top_level_dir = list(top_dirs)[0] if len(top_dirs) == 1 else None

        # Prioritize executables:
        # For Electron / VS Code / Antigravity apps, the root binary (`/opt/antigravity/antigravity`) or `bin/antigravity`
        # should beat internal helpers (`chrome-sandbox`, `chrome_crashpad_handler`, `rg`, `node`, `spawn-helper`).
        def exec_rank(path: str) -> Tuple[int, int, int, int]:
            bname = os.path.basename(path).lower()
            parts = path.split("/")
            rel_parts = parts[1:] if (top_level_dir and parts[0] == top_level_dir) else parts
            in_internal_dir = 1 if any(d in rel_parts[:-1] for d in ("resources", "node_modules", "extensions", "lib", "locales", "swiftshader")) else 0
            is_helper = 1 if any(
                h in bname
                for h in ("crash", "updater", "helper", "daemon", "sandbox", "uninstall", "configure", "node", "rg", "pty", "tunnel", "cli")
            ) else 0
            is_exact = 0 if (bname == app_id or bname == app_id.replace("-", "")) else 1
            return (in_internal_dir, is_helper, is_exact, len(rel_parts))

        executables.sort(key=exec_rank)
        primary_exec = executables[0] if executables else None

        icon_scored.sort(key=lambda x: x[0], reverse=True)
        icon_files = [p for _, p in icon_scored]
        primary_icon = icon_files[0] if icon_files else None

        non_build_execs = [
            e for e in executables
            if os.path.basename(e) not in ("configure", "install.sh", "setup.sh", "autogen.sh", "bootstrap")
        ]

        if non_build_execs:
            strategy = "binary_bundle"
            if is_electron_app:
                strategy_desc = "Pre-compiled Electron/Chromium Application (Auto-configures Ubuntu 24.04/26.04 sandbox, dependencies & icon)"
            else:
                strategy_desc = "Pre-compiled Application Bundle (Extracts app, installs missing libraries, links binary & creates desktop icon)"
            primary_exec = non_build_execs[0]
        elif install_scripts and not (has_configure or has_cmake or has_meson):
            strategy = "install_script"
            strategy_desc = f"Bundled Installer Script ({install_scripts[0]})"
        elif has_configure:
            strategy = "autotools"
            strategy_desc = "Autotools Source Package (./configure && make && make install)"
        elif has_cmake:
            strategy = "cmake"
            strategy_desc = "CMake Source Package (cmake -B build && cmake --build && cmake --install)"
        elif has_meson:
            strategy = "meson"
            strategy_desc = "Meson Source Package (meson setup build && meson compile && meson install)"
        elif has_python_setup:
            strategy = "python_pkg"
            strategy_desc = "Python Source Package (pip install / setup.py)"
        elif has_makefile:
            strategy = "makefile"
            strategy_desc = "Makefile Package (make && make install)"
        else:
            strategy = "binary_bundle"
            strategy_desc = "Application Directory Archive (Extracts to opt directory)"

        return ArchiveAnalysis(
            archive_path=str(path_obj),
            file_size_bytes=file_size,
            uncompressed_size_bytes=uncompressed_size,
            app_id=app_id,
            display_name=display_name,
            version=version,
            strategy=strategy,
            strategy_description=strategy_desc,
            top_level_dir=top_level_dir,
            is_electron_app=is_electron_app,
            executables=non_build_execs if non_build_execs else executables,
            primary_executable=primary_exec,
            desktop_files=desktop_files,
            icon_files=icon_files,
            primary_icon_member=primary_icon,
            install_scripts=install_scripts,
            file_count=len(member_list),
            member_list=member_list,
            warnings=warnings,
        )


class DependencyResolver:
    """
    Scans extracted ELF binaries with `ldd` and build scripts for missing dependencies,
    maps them to Ubuntu (24.04 / 26.04 t64-aware) apt packages, and installs them automatically.
    """

    @staticmethod
    def _package_exists_in_apt(pkg_name: str) -> bool:
        if not shutil.which("apt-cache"):
            return False
        res = subprocess.run(
            ["apt-cache", "show", pkg_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return res.returncode == 0

    @classmethod
    def resolve_so_to_apt_package(cls, so_name: str) -> Optional[str]:
        # 1. Check built-in map (handles Ubuntu 24.04/26.04 t64 transition packages)
        candidates = list(SO_TO_APT_PACKAGES.get(so_name, []))

        # 2. Heuristic derivation from libfoo.so.X -> libfoot64, libfooX, etc.
        m = re.match(r"^(lib[a-zA-Z0-9_+.-]+?)\.so(?:\.(\d+))?$", so_name)
        if m:
            base, major = m.group(1).lower().replace("_", "-"), m.group(2)
            if major:
                if base[-1].isdigit():
                    candidates.extend([f"{base}-{major}t64", f"{base}-{major}"])
                else:
                    candidates.extend([f"{base}{major}t64", f"{base}{major}", f"{base}-{major}t64", f"{base}-{major}"])
            else:
                candidates.append(base)

        for cand in candidates:
            if cls._package_exists_in_apt(cand):
                return cand

        # 3. Fallback to apt-file if installed
        if shutil.which("apt-file"):
            res = subprocess.run(
                ["apt-file", "search", "--package-only", so_name],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                first_pkg = res.stdout.strip().splitlines()[0].strip()
                if first_pkg:
                    return first_pkg

        return candidates[0] if candidates else None

    @classmethod
    def find_missing_shared_libraries(cls, install_dir: pathlib.Path, log_fn: Callable[[str], None] = print) -> Set[str]:
        if not shutil.which("ldd"):
            return set()

        # Collect all internal directories containing .so files so bundled libs are found
        lib_dirs: Set[str] = {str(install_dir)}
        elf_targets: List[pathlib.Path] = []

        for root, _, files in os.walk(install_dir):
            for fname in files:
                fpath = pathlib.Path(root) / fname
                if fname.endswith(".so") or ".so." in fname:
                    lib_dirs.add(root)
                if fpath.is_symlink() or not fpath.is_file():
                    continue
                try:
                    if fpath.stat().st_size > 4 and (os.access(fpath, os.X_OK) or fname.endswith(".so")):
                        with open(fpath, "rb") as f:
                            if f.read(4) == ELF_MAGIC:
                                elf_targets.append(fpath)
                except Exception:
                    continue

        # Scan up to 25 primary ELF files
        elf_targets.sort(key=lambda p: len(p.parts))
        elf_targets = elf_targets[:25]

        env = os.environ.copy()
        existing_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(sorted(lib_dirs)) + (f":{existing_ld}" if existing_ld else "")

        missing_so: Set[str] = set()
        for elf in elf_targets:
            res = subprocess.run(
                ["ldd", str(elf)],
                 env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                continue
            for line in res.stdout.splitlines():
                if "=> not found" in line:
                    so_name = line.split("=>")[0].strip()
                    if so_name:
                        missing_so.add(so_name)

        return missing_so

    @classmethod
    def find_missing_build_packages(cls, strategy: str) -> List[str]:
        needed: List[str] = []
        if strategy in ("autotools", "cmake", "meson", "makefile"):
            if not shutil.which("gcc") or not shutil.which("make") or not shutil.which("g++"):
                needed.append("build-essential")
            if not shutil.which("pkg-config"):
                needed.append("pkg-config")
        if strategy == "cmake" and not shutil.which("cmake"):
            needed.append("cmake")
        if strategy == "meson":
            if not shutil.which("meson"):
                needed.append("meson")
            if not shutil.which("ninja"):
                needed.append("ninja-build")
        if strategy == "python_pkg":
            res = subprocess.run([sys.executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if res.returncode != 0:
                needed.append("python3-pip")
        return needed

    @classmethod
    def auto_install_dependencies(
        cls,
        install_dir: pathlib.Path,
        strategy: str,
        log_fn: Callable[[str], None] = print,
    ) -> None:
        log_fn("--> Scanning binary & build dependencies (ldd + build tools)...")
        missing_so = cls.find_missing_shared_libraries(install_dir, log_fn=log_fn)
        apt_packages: Set[str] = set(cls.find_missing_build_packages(strategy))

        for so_name in sorted(missing_so):
            pkg = cls.resolve_so_to_apt_package(so_name)
            if pkg:
                log_fn(f"    [DEPENDENCY] Missing library '{so_name}' -> Ubuntu package '{pkg}'")
                apt_packages.add(pkg)
            else:
                log_fn(f"    [WARN] Could not map missing library '{so_name}' to an apt package automatically.")

        if not apt_packages:
            log_fn("--> All shared libraries and runtime dependencies are satisfied!")
            return

        pkg_list = sorted(apt_packages)
        log_fn(f"--> Auto-installing {len(pkg_list)} missing Ubuntu package(s): {', '.join(pkg_list)}")

        if not shutil.which("apt-get"):
            log_fn("[WARN] apt-get not available on this system; skipping package installation.")
            return

        install_shell_cmd = (
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -y && "
            f"apt-get install -y --no-install-recommends {' '.join(pkg_list)}"
        )

        if os.geteuid() == 0:
            cmd = ["/bin/sh", "-c", install_shell_cmd]
        elif shutil.which("pkexec"):
            log_fn("    Requesting administrator permission via Polkit (pkexec) to install missing apt dependencies...")
            cmd = ["pkexec", "/bin/sh", "-c", install_shell_cmd]
        else:
            log_fn("[WARN] Root privileges required to install apt packages. Run: sudo apt install " + " ".join(pkg_list))
            return

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.stdout:
            for line in proc.stdout:
                log_fn("    [apt] " + line.rstrip())
        proc.wait()
        if proc.returncode == 0:
            log_fn("--> Successfully installed all missing dependencies!")
        else:
            log_fn(f"[WARN] apt-get exited with code {proc.returncode}. You can manually run: sudo apt install {' '.join(pkg_list)}")


class PackageRegistry:
    """Manages records of installed .tar.gz packages for clean uninstallation."""

    @staticmethod
    def get_registry_dir(scope: str) -> pathlib.Path:
        if scope == "system":
            return pathlib.Path("/var/lib/targz-installer/packages")
        return pathlib.Path.home() / ".local" / "share" / "targz-installer" / "packages"

    @classmethod
    def save_package(cls, pkg: InstalledPackage) -> pathlib.Path:
        reg_dir = cls.get_registry_dir(pkg.scope)
        reg_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = reg_dir / f"{pkg.app_id}.json"
        manifest_path.write_text(json.dumps(asdict(pkg), indent=2), encoding="utf-8")
        idx_path = reg_dir / "_index.json"
        idx_list: List[str] = []
        if idx_path.is_file():
            try:
                idx_list = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                idx_list = []
        if pkg.app_id not in idx_list:
            idx_list.append(pkg.app_id)
            idx_path.write_text(json.dumps(sorted(idx_list)), encoding="utf-8")
        return manifest_path

    @classmethod
    def list_packages(cls) -> List[InstalledPackage]:
        packages: Dict[Tuple[str, str], InstalledPackage] = {}
        for scope in ("user", "system"):
            reg_dir = cls.get_registry_dir(scope)
            if not reg_dir.exists():
                continue
            cand_files = set(reg_dir.glob("*.json"))
            idx_path = reg_dir / "_index.json"
            if idx_path.is_file():
                try:
                    for aid in json.loads(idx_path.read_text(encoding="utf-8")):
                        cand_files.add(reg_dir / f"{aid}.json")
                except Exception:
                    pass
            for jf in sorted(cand_files):
                if jf.name == "_index.json" or not jf.is_file():
                    continue
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    pkg = InstalledPackage(**data)
                    packages[(scope, pkg.app_id)] = pkg
                except Exception:
                    continue
        return list(packages.values())

    @classmethod
    def get_package(cls, app_id: str) -> Optional[InstalledPackage]:
        for scope in ("user", "system"):
            jf = cls.get_registry_dir(scope) / f"{app_id}.json"
            if jf.is_file():
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    return InstalledPackage(**data)
                except Exception:
                    pass
        for pkg in cls.list_packages():
            if pkg.app_id == app_id:
                return pkg
        return None


class TarGzInstallerEngine:
    """Performs safe extraction, dependency resolution, Electron sandbox fixing, icon setup, and uninstallation."""

    @staticmethod
    def get_paths(scope: str, app_id: str) -> Dict[str, pathlib.Path]:
        if scope == "system":
            base_opt = pathlib.Path("/opt") / app_id
            bin_dir = pathlib.Path("/usr/local/bin")
            apps_dir = pathlib.Path("/usr/share/applications")
            icons_dir = pathlib.Path("/usr/share/pixmaps")
            hicolor_dir = pathlib.Path("/usr/share/icons/hicolor/256x256/apps")
            prefix_dir = pathlib.Path("/usr/local")
        else:
            home = pathlib.Path.home()
            base_opt = home / ".local" / "opt" / app_id
            bin_dir = home / ".local" / "bin"
            apps_dir = home / ".local" / "share" / "applications"
            icons_dir = home / ".local" / "share" / "icons"
            hicolor_dir = home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
            prefix_dir = home / ".local"

        return {
            "install_dir": base_opt,
            "bin_dir": bin_dir,
            "apps_dir": apps_dir,
            "icons_dir": icons_dir,
            "hicolor_dir": hicolor_dir,
            "prefix_dir": prefix_dir,
        }

    @staticmethod
    def configure_electron_sandbox(
        install_dir: pathlib.Path,
        app_id: str,
        target_exec_path: pathlib.Path,
        scope: str,
        log_fn: Callable[[str], None] = print,
    ) -> Tuple[pathlib.Path, List[str]]:
        """
        Fixes the Ubuntu 24.04 / 26.04 `SIGTRAP` crash on Electron/Chromium apps
        (such as Antigravity, VS Code, Cursor, Discord, Postman):
          1. Sets root:root 04755 SUID permissions on `chrome-sandbox` if root
          2. Installs & loads an Ubuntu AppArmor profile in `/etc/apparmor.d/targz-<app_id>` allowing `userns`
          3. Creates a resilient launcher wrapper `targz-launch.sh` that sets `LD_LIBRARY_PATH` and
             automatically passes `--no-sandbox` when Ubuntu's `apparmor_restrict_unprivileged_userns=1` blocks userns.
        """
        extra_files: List[str] = []

        # 1. Fix chrome-sandbox permissions if present
        for sb in install_dir.rglob("chrome-sandbox"):
            if sb.is_file():
                try:
                    if os.geteuid() == 0:
                        os.chown(sb, 0, 0)
                        os.chmod(sb, 0o4755)
                        log_fn(f"--> Configured SUID root sandbox permissions (4755) on {sb}")
                    else:
                        os.chmod(sb, 0o755)
                except Exception as e:
                    log_fn(f"[WARN] Could not set 4755 on {sb}: {e}")

        # 2. Install Ubuntu 24.04 / 26.04 AppArmor profile if running as root
        if scope == "system" and os.geteuid() == 0 and pathlib.Path("/etc/apparmor.d").is_dir():
            aa_profile_path = pathlib.Path("/etc/apparmor.d") / f"targz-{app_id}"
            aa_content = (
                "#include <tunables/global>\n\n"
                f"profile targz-{app_id} \"{install_dir}/**\" flags=(unconfined) {{\n"
                "  userns,\n"
                "}\n"
            )
            try:
                aa_profile_path.write_text(aa_content, encoding="utf-8")
                os.chmod(aa_profile_path, 0o644)
                extra_files.append(str(aa_profile_path))
                if shutil.which("apparmor_parser"):
                    subprocess.run(["apparmor_parser", "-r", "-W", str(aa_profile_path)], check=False)
                log_fn(f"--> Installed Ubuntu 24.04/26.04 AppArmor userns profile: {aa_profile_path}")
            except Exception as e:
                log_fn(f"[WARN] Could not load AppArmor profile: {e}")

        # 3. Create smart launcher wrapper inside install_dir so Electron never crashes with SIGTRAP
        wrapper_path = install_dir / f"targz-launch-{app_id}.sh"
        wrapper_content = (
            "#!/usr/bin/env bash\n"
            f'APP_DIR="{install_dir}"\n'
            f'TARGET_BIN="{target_exec_path}"\n'
            'export LD_LIBRARY_PATH="${APP_DIR}:${APP_DIR}/lib:${LD_LIBRARY_PATH:-}"\n'
            "\n"
            "# Detect Electron/Chromium and prevent SIGTRAP on Ubuntu 24.04 / 26.04\n"
            "EXTRA_FLAGS=()\n"
            'if [[ -f "${APP_DIR}/chrome-sandbox" ]] || [[ -f "${APP_DIR}/v8_context_snapshot.bin" ]] || [[ -f "${APP_DIR}/icudtl.dat" ]]; then\n'
            '    USERNS_RESTRICTED=$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)\n'
            '    if [[ "${USERNS_RESTRICTED}" == "1" ]] || [[ ! -u "${APP_DIR}/chrome-sandbox" ]]; then\n'
            '        EXTRA_FLAGS+=("--no-sandbox")\n'
            "    fi\n"
            "fi\n"
            '\n'
            'exec "${TARGET_BIN}" "${EXTRA_FLAGS[@]}" "$@"\n'
        )
        wrapper_path.write_text(wrapper_content, encoding="utf-8")
        os.chmod(wrapper_path, 0o755)
        log_fn(f"--> Created Ubuntu 24.04/26.04 crash-safe launcher wrapper: {wrapper_path}")
        return wrapper_path, extra_files

    @classmethod
    def install_app_icon_and_desktop(
        cls,
        install_dir: pathlib.Path,
        app_id: str,
        display_name: str,
        version: str,
        exec_target: pathlib.Path,
        scope: str,
        category: str = "Utility",
        terminal_app: bool = False,
        create_desktop_entry: bool = True,
        log_fn: Callable[[str], None] = print,
    ) -> Tuple[Optional[str], Optional[str], List[str]]:
        paths = cls.get_paths(scope, app_id)
        apps_dir = paths["apps_dir"]
        icons_dir = paths["icons_dir"]
        hicolor_dir = paths["hicolor_dir"]
        for d in (apps_dir, icons_dir, hicolor_dir):
            d.mkdir(parents=True, exist_ok=True)

        installed_icon: Optional[str] = None
        extra_files: List[str] = []

        best_icon_src = find_best_icon_in_directory(install_dir, app_id)
        if best_icon_src and best_icon_src.is_file():
            ext = best_icon_src.suffix.lower() or ".png"
            dest_icon = icons_dir / f"{app_id}{ext}"
            shutil.copyfile(best_icon_src, dest_icon)
            os.chmod(dest_icon, 0o644)  # Critical: ensure world-readable 0644 even when run via pkexec/root!
            installed_icon = str(dest_icon)
            log_fn(f"--> Selected & installed high-res application logo: {best_icon_src.relative_to(install_dir)} -> {dest_icon}")

            # Also copy into hicolor/256x256/apps so Ubuntu GNOME theme lookup by icon name works seamlessly
            if ext in (".png", ".svg"):
                hi_icon = hicolor_dir / f"{app_id}{ext}"
                shutil.copyfile(best_icon_src, hi_icon)
                os.chmod(hi_icon, 0o644)
                extra_files.append(str(hi_icon))
        else:
            log_fn("[WARN] No icon file found inside archive; using system default icon.")

        installed_desktop: Optional[str] = None
        if create_desktop_entry:
            desktop_path = apps_dir / f"{app_id}.desktop"
            icon_val = installed_icon if installed_icon else "application-x-executable"
            bool_term = "true" if terminal_app else "false"
            # Set StartupWMClass so Ubuntu Dock matches running Electron/X11/Wayland windows to this icon
            wm_class = display_name.replace(" ", "")
            desktop_content = (
                "[Desktop Entry]\n"
                "Version=1.0\n"
                "Type=Application\n"
                f"Name={display_name}\n"
                f"Comment={display_name} ({version}) installed via Tar.gz Package Installer\n"
                f"Exec=\"{exec_target}\" %U\n"
                f"Path={install_dir}\n"
                f"Icon={icon_val}\n"
                f"Terminal={bool_term}\n"
                f"Categories={category};\n"
                "StartupNotify=true\n"
                f"StartupWMClass={app_id}\n"
            )
            desktop_path.write_text(desktop_content, encoding="utf-8")
            os.chmod(desktop_path, 0o755)
            installed_desktop = str(desktop_path)
            log_fn(f"--> Created Ubuntu Desktop Launcher: {desktop_path} (StartupWMClass={app_id})")

        # Refresh Ubuntu desktop & icon caches
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(apps_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if shutil.which("gtk-update-icon-cache"):
            hicolor_root = hicolor_dir.parent.parent
            if hicolor_root.is_dir():
                subprocess.run(["gtk-update-icon-cache", "-q", "-t", "-f", str(hicolor_root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

        return installed_icon, installed_desktop, extra_files

    @classmethod
    def safe_extract(
        cls,
        archive_path: str,
        dest_dir: pathlib.Path,
        strip_top_dir: Optional[str] = None,
        log_fn: Callable[[str], None] = print,
        progress_fn: Callable[[float], None] = lambda p: None,
    ) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "r:*") as tf:
            members = tf.getmembers()
            total = max(len(members), 1)
            for idx, m in enumerate(members, start=1):
                if not is_safe_tar_path(m.name):
                    log_fn(f"[SECURITY] Skipping unsafe path: {m.name}")
                    continue

                rel_name = strip_dot_slash(m.name)
                if strip_top_dir and rel_name.startswith(strip_top_dir + "/"):
                    rel_name = rel_name[len(strip_top_dir) + 1 :]
                elif strip_top_dir and rel_name == strip_top_dir:
                    continue

                if not rel_name:
                    continue

                target_path = (dest_dir / rel_name).resolve()
                if not str(target_path).startswith(str(dest_dir.resolve())):
                    log_fn(f"[SECURITY] Blocked path escape attempt: {m.name}")
                    continue

                if m.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    os.chmod(target_path, 0o755)
                elif m.isfile():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    src_f = tf.extractfile(m)
                    if src_f:
                        with open(target_path, "wb") as out_f:
                            shutil.copyfileobj(src_f, out_f)
                    # Ensure world-readable permissions so root installs via pkexec are readable by desktop user
                    base_mode = m.mode if m.mode else 0o644
                    if base_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                        os.chmod(target_path, base_mode | 0o755)
                    else:
                        os.chmod(target_path, base_mode | 0o644)
                elif m.issym():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    if target_path.exists() or target_path.is_symlink():
                        target_path.unlink()
                    try:
                        os.symlink(m.linkname, target_path)
                    except OSError:
                        pass

                if idx % 15 == 0 or idx == total:
                    progress_fn(0.1 + 0.45 * (idx / total))

    @classmethod
    def install(
        cls,
        analysis: ArchiveAnalysis,
        scope: str = "user",
        custom_app_name: Optional[str] = None,
        selected_executable: Optional[str] = None,
        create_desktop_entry: bool = True,
        category: str = "Utility",
        terminal_app: bool = False,
        auto_deps: bool = True,
        log_fn: Callable[[str], None] = print,
        progress_fn: Callable[[float], None] = lambda p: None,
    ) -> InstalledPackage:
        if scope == "system" and os.geteuid() != 0:
            raise PermissionError(
                "System-wide installation requires root privileges. Use User Scope (~/.local) or run with pkexec/sudo."
            )

        app_id = analysis.app_id
        display_name = (custom_app_name or analysis.display_name).strip()
        paths = cls.get_paths(scope, app_id)
        install_dir = paths["install_dir"]
        bin_dir = paths["bin_dir"]
        apps_dir = paths["apps_dir"]
        icons_dir = paths["icons_dir"]

        for d in (bin_dir, apps_dir, icons_dir):
            d.mkdir(parents=True, exist_ok=True)

        log_fn(f"==> Starting installation of {display_name} ({analysis.version})")
        log_fn(f"    Archive: {analysis.archive_path}")
        log_fn(f"    Strategy: {analysis.strategy_description}")
        log_fn(f"    Scope: {scope.upper()} ({install_dir})")
        progress_fn(0.05)

        symlinks: List[str] = []
        installed_icon: Optional[str] = None
        installed_desktop: Optional[str] = None
        extra_files: List[str] = []

        if install_dir.exists():
            log_fn(f"--> Cleaning previous installation directory: {install_dir}")
            safe_rmtree(install_dir)

        strip_dir = analysis.top_level_dir

        if analysis.strategy in ("binary_bundle", "install_script"):
            log_fn(f"--> Extracting archive to {install_dir} ...")
            cls.safe_extract(
                analysis.archive_path,
                install_dir,
                strip_top_dir=strip_dir,
                log_fn=log_fn,
                progress_fn=progress_fn,
            )

            # Auto-detect and install missing shared libraries via ldd + apt-get
            if auto_deps:
                progress_fn(0.60)
                DependencyResolver.auto_install_dependencies(install_dir, analysis.strategy, log_fn=log_fn)

            if analysis.strategy == "install_script" and analysis.install_scripts:
                script_rel = analysis.install_scripts[0]
                if strip_dir and script_rel.startswith(strip_dir + "/"):
                    script_rel = script_rel[len(strip_dir) + 1 :]
                script_path = install_dir / script_rel
                if script_path.exists():
                    os.chmod(script_path, script_path.stat().st_mode | stat.S_IXUSR)
                    log_fn(f"--> Running bundled installer script: {script_path}")
                    proc = subprocess.Popen(
                        ["/bin/sh", str(script_path)],
                        cwd=str(install_dir),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    if proc.stdout:
                        for line in proc.stdout:
                            log_fn("    " + line.rstrip())
                    proc.wait()

            # Resolve chosen executable inside install_dir
            chosen_exec = selected_executable or analysis.primary_executable
            target_exec_path: Optional[pathlib.Path] = None
            if chosen_exec:
                rel_exec = chosen_exec
                if strip_dir and rel_exec.startswith(strip_dir + "/"):
                    rel_exec = rel_exec[len(strip_dir) + 1 :]
                candidate = install_dir / rel_exec
                if candidate.exists():
                    target_exec_path = candidate
                    os.chmod(target_exec_path, 0o755)

            launch_target = target_exec_path
            if target_exec_path:
                # Configure Electron/Chromium sandbox & crash-safe wrapper (prevents SIGTRAP on Ubuntu 24.04/26.04)
                launch_target, sb_extras = cls.configure_electron_sandbox(
                    install_dir, app_id, target_exec_path, scope, log_fn=log_fn
                )
                extra_files.extend(sb_extras)

                link_name = bin_dir / app_id
                create_link_or_wrapper(launch_target, link_name)
                symlinks.append(str(link_name))
                log_fn(f"--> Linked executable: {link_name} -> {launch_target}")

                orig_bin_name = target_exec_path.name
                if orig_bin_name != app_id and not orig_bin_name.endswith(".sh"):
                    second_link = bin_dir / orig_bin_name
                    if not second_link.exists() and not second_link.is_symlink():
                        create_link_or_wrapper(launch_target, second_link)
                        symlinks.append(str(second_link))
                        log_fn(f"--> Linked binary alias: {second_link} -> {launch_target}")

            if launch_target:
                installed_icon, installed_desktop, icon_extras = cls.install_app_icon_and_desktop(
                    install_dir=install_dir,
                    app_id=app_id,
                    display_name=display_name,
                    version=analysis.version,
                    exec_target=launch_target,
                    scope=scope,
                    category=category,
                    terminal_app=terminal_app,
                    create_desktop_entry=create_desktop_entry,
                    log_fn=log_fn,
                )
                extra_files.extend(icon_extras)

        else:
            # Source build strategies (autotools, cmake, meson, python_pkg, makefile)
            if auto_deps:
                DependencyResolver.auto_install_dependencies(install_dir, analysis.strategy, log_fn=log_fn)

            with tempfile.TemporaryDirectory(prefix=f"targz-build-{app_id}-") as tmp_build:
                build_root = pathlib.Path(tmp_build)
                log_fn("--> Extracting source archive to temporary build directory...")
                cls.safe_extract(
                    analysis.archive_path,
                    build_root,
                    strip_top_dir=strip_dir,
                    log_fn=log_fn,
                    progress_fn=progress_fn,
                )

                install_dir.mkdir(parents=True, exist_ok=True)
                build_cmds: List[List[str]] = []
                if analysis.strategy == "autotools":
                    build_cmds = [
                        ["./configure", f"--prefix={install_dir}"],
                        ["make", f"-j{os.cpu_count() or 2}"],
                        ["make", "install"],
                    ]
                elif analysis.strategy == "cmake":
                    build_cmds = [
                        ["cmake", "-B", "build", f"-DCMAKE_INSTALL_PREFIX={install_dir}"],
                        ["cmake", "--build", "build", "--parallel"],
                        ["cmake", "--install", "build"],
                    ]
                elif analysis.strategy == "meson":
                    build_cmds = [
                        ["meson", "setup", "build", f"--prefix={install_dir}"],
                        ["meson", "compile", "-C", "build"],
                        ["meson", "install", "-C", "build"],
                    ]
                elif analysis.strategy == "python_pkg":
                    build_cmds = [
                        [sys.executable, "-m", "pip", "install", "--prefix", str(install_dir), "--no-deps", "."]
                    ]
                elif analysis.strategy == "makefile":
                    build_cmds = [
                        ["make", f"-j{os.cpu_count() or 2}"],
                        ["make", f"PREFIX={install_dir}", "install"],
                    ]

                for step_idx, cmd in enumerate(build_cmds, start=1):
                    log_fn(f"--> [Step {step_idx}/{len(build_cmds)}] Running: {' '.join(cmd)}")
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(build_root),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    if proc.stdout:
                        for line in proc.stdout:
                            log_fn("    " + line.rstrip())
                    proc.wait()
                    if proc.returncode != 0:
                        raise RuntimeError(f"Build step failed ({' '.join(cmd)}) with exit code {proc.returncode}")
                    progress_fn(0.6 + 0.3 * (step_idx / len(build_cmds)))

                built_bin = install_dir / "bin"
                if built_bin.is_dir():
                    for bfile in built_bin.iterdir():
                        if bfile.is_file() and os.access(bfile, os.X_OK):
                            dest_link = bin_dir / bfile.name
                            create_link_or_wrapper(bfile, dest_link)
                            symlinks.append(str(dest_link))
                            log_fn(f"--> Linked built binary: {dest_link} -> {bfile}")

        progress_fn(0.95)
        pkg = InstalledPackage(
            app_id=app_id,
            display_name=display_name,
            version=analysis.version,
            scope=scope,
            strategy=analysis.strategy,
            source_archive=os.path.basename(analysis.archive_path),
            installed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            install_dir=str(install_dir),
            symlinks=symlinks,
            desktop_file=installed_desktop,
            icon_file=installed_icon,
            extra_files=extra_files,
        )
        manifest_file = PackageRegistry.save_package(pkg)
        log_fn(f"--> Saved installation manifest: {manifest_file}")
        log_fn(f"==> SUCCESS: {display_name} ({analysis.version}) is now installed and ready to launch!")
        progress_fn(1.0)
        return pkg

    @classmethod
    def repair_existing_installation(
        cls,
        target_app_or_dir: str,
        log_fn: Callable[[str], None] = print,
    ) -> bool:
        """
        Repairs an already-installed application directory (such as `/opt/antigravity`) in place:
          1. Scans and auto-installs any missing shared libraries (`ldd` + `apt-get`)
          2. Fixes the Ubuntu 24.04 / 26.04 Electron `SIGTRAP` sandbox crash (`chrome-sandbox` + AppArmor + `--no-sandbox` wrapper)
          3. Extracts the real high-res application icon (`resources/app/resources/linux/*.png` or `.asar`), sets 0644 permissions,
             updates the `.desktop` entry with `StartupWMClass`, and refreshes the GNOME icon cache.
        """
        pkg = PackageRegistry.get_package(target_app_or_dir)
        if pkg:
            install_dir = pathlib.Path(pkg.install_dir)
            app_id = pkg.app_id
            display_name = pkg.display_name
            version = pkg.version
            scope = pkg.scope
        else:
            cand_dir = pathlib.Path(target_app_or_dir).expanduser()
            if not cand_dir.is_dir():
                cand_dir = pathlib.Path("/opt") / target_app_or_dir.lower()
            if not cand_dir.is_dir():
                cand_dir = pathlib.Path.home() / ".local" / "opt" / target_app_or_dir.lower()
            if not cand_dir.is_dir():
                log_fn(f"[ERROR] Could not find installation directory for '{target_app_or_dir}'.")
                return False
            install_dir = cand_dir.resolve()
            app_id = install_dir.name.lower()
            display_name = app_id.replace("-", " ").title()
            version = "1.0.0"
            scope = "system" if str(install_dir).startswith("/opt") else "user"

        if scope == "system" and os.geteuid() != 0:
            raise PermissionError(f"Repairing {install_dir} requires root privileges. Run with sudo or pkexec.")

        log_fn(f"==> Repairing installed application: {display_name} in {install_dir}")

        # Ensure world-readable permissions across install_dir so non-root desktop user can read icons & resources
        for root, dirs, files in os.walk(install_dir):
            os.chmod(root, 0o755)
            for fn in files:
                fp = pathlib.Path(root) / fn
                if fp.is_symlink() or not fp.is_file():
                    continue
                mode = fp.stat().st_mode
                if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                    os.chmod(fp, mode | 0o755)
                else:
                    os.chmod(fp, mode | 0o644)

        # 1. Install missing shared libraries
        DependencyResolver.auto_install_dependencies(install_dir, "binary_bundle", log_fn=log_fn)

        # 2. Locate main executable inside install_dir
        target_exec: Optional[pathlib.Path] = None
        for candidate in [
            install_dir / app_id,
            install_dir / "bin" / app_id,
        ]:
            if candidate.is_file() and os.access(candidate, os.X_OK) and not candidate.name.startswith("targz-launch-"):
                target_exec = candidate
                break

        if not target_exec:
            for item in install_dir.iterdir():
                if item.is_file() and os.access(item, os.X_OK) and not item.name.startswith(("targz-launch-", "chrome-sandbox", "chrome_crashpad")):
                    target_exec = item
                    break

        if not target_exec:
            log_fn(f"[ERROR] Could not locate main executable inside {install_dir}")
            return False

        # 3. Configure Electron sandbox & crash-safe wrapper
        launch_target, sb_extras = cls.configure_electron_sandbox(
            install_dir, app_id, target_exec, scope, log_fn=log_fn
        )

        paths = cls.get_paths(scope, app_id)
        bin_link = paths["bin_dir"] / app_id
        create_link_or_wrapper(launch_target, bin_link)
        log_fn(f"--> Updated command-line launcher: {bin_link} -> {launch_target}")

        # 4. Re-extract high-res icon and update .desktop launcher
        installed_icon, installed_desktop, icon_extras = cls.install_app_icon_and_desktop(
            install_dir=install_dir,
            app_id=app_id,
            display_name=display_name,
            version=version,
            exec_target=launch_target,
            scope=scope,
            log_fn=log_fn,
        )

        updated_pkg = InstalledPackage(
            app_id=app_id,
            display_name=display_name,
            version=version,
            scope=scope,
            strategy="binary_bundle",
            source_archive=pkg.source_archive if pkg else f"{app_id}.tar.gz",
            installed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            install_dir=str(install_dir),
            symlinks=[str(bin_link)],
            desktop_file=installed_desktop,
            icon_file=installed_icon,
            extra_files=sb_extras + icon_extras,
        )
        PackageRegistry.save_package(updated_pkg)
        log_fn(f"==> Repair complete! {display_name} now has its official icon, dependencies, and Ubuntu 24.04/26.04 sandbox fix.")
        return True

    @classmethod
    def uninstall(cls, app_id: str, log_fn: Callable[[str], None] = print) -> bool:
        pkg = PackageRegistry.get_package(app_id)
        if not pkg:
            log_fn(f"[ERROR] Package '{app_id}' is not registered as installed.")
            return False

        if pkg.scope == "system" and os.geteuid() != 0:
            raise PermissionError("Uninstalling a system-wide package requires root privileges (pkexec/sudo).")

        log_fn(f"==> Uninstalling {pkg.display_name} ({pkg.version}) [Scope: {pkg.scope}] ...")

        for link in pkg.symlinks:
            p = pathlib.Path(link)
            if p.exists() or p.is_symlink():
                p.unlink()
                log_fn(f"--> Removed symlink: {link}")

        if pkg.desktop_file:
            dp = pathlib.Path(pkg.desktop_file)
            if dp.exists():
                dp.unlink()
                log_fn(f"--> Removed desktop launcher: {dp}")

        if pkg.icon_file:
            ip = pathlib.Path(pkg.icon_file)
            if ip.exists():
                ip.unlink()
                log_fn(f"--> Removed icon: {ip}")

        for ef in pkg.extra_files:
            ep = pathlib.Path(ef)
            if ep.exists():
                ep.unlink()

        inst_dir = pathlib.Path(pkg.install_dir)
        if inst_dir.exists():
            safe_rmtree(inst_dir)
            log_fn(f"--> Removed application directory: {inst_dir}")

        manifest = PackageRegistry.get_registry_dir(pkg.scope) / f"{pkg.app_id}.json"
        if manifest.exists():
            manifest.unlink()
            log_fn(f"--> Removed package manifest: {manifest}")

        log_fn(f"==> Successfully uninstalled {pkg.display_name}!")
        return True


def perform_self_install(system_wide: bool = False) -> None:
    """Installs targz-installer itself and registers .tar.gz MIME types for double-click opening."""
    script_src = pathlib.Path(__file__).resolve()
    if system_wide or os.geteuid() == 0:
        bin_dir = pathlib.Path("/usr/local/bin")
        apps_dir = pathlib.Path("/usr/share/applications")
        icons_dir = pathlib.Path("/usr/share/icons/hicolor/scalable/apps")
    else:
        home = pathlib.Path.home()
        bin_dir = home / ".local" / "bin"
        apps_dir = home / ".local" / "share" / "applications"
        icons_dir = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"

    for d in (bin_dir, apps_dir, icons_dir):
        d.mkdir(parents=True, exist_ok=True)

    target_bin = bin_dir / "targz-installer"
    shutil.copyfile(script_src, target_bin)
    os.chmod(target_bin, 0o755)

    svg_icon = icons_dir / "targz-installer.svg"
    svg_icon.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
        '  <rect x="6" y="10" width="52" height="46" rx="8" fill="#E95420"/>\n'
        '  <path d="M6 22h52v6H6z" fill="#AEA79F" opacity="0.35"/>\n'
        '  <rect x="28" y="10" width="8" height="22" fill="#F7F7F7" rx="2"/>\n'
        '  <path d="M32 36l-10 10h6v8h8v-8h6z" fill="#FFFFFF"/>\n'
        '</svg>\n',
        encoding="utf-8",
    )
    os.chmod(svg_icon, 0o644)

    desktop_file = apps_dir / "targz-installer.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        f"Name={APP_TITLE}\n"
        "GenericName=Archive Package Installer\n"
        "Comment=Install .tar.gz and .tgz archives like .deb packages\n"
        f"Exec={target_bin} %f\n"
        f"Icon={svg_icon}\n"
        "Terminal=false\n"
        "Categories=System;PackageManager;Utility;\n"
        "MimeType=application/x-compressed-tar;application/x-gtar;application/x-tar;application/gzip;application/x-bzip-compressed-tar;application/x-xz-compressed-tar;\n"
        "StartupNotify=true\n",
        encoding="utf-8",
    )
    os.chmod(desktop_file, 0o755)

    if shutil.which("xdg-mime"):
        for mime in (
            "application/x-compressed-tar",
            "application/x-gtar",
            "application/gzip",
        ):
            subprocess.run(["xdg-mime", "default", "targz-installer.desktop", mime], check=False)

    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    print(f"[OK] Installed {APP_TITLE} v{APP_VERSION} to {target_bin}")
    print(f"[OK] Created desktop launcher at {desktop_file}")
    print("[OK] Registered MIME types for .tar.gz / .tgz archives.")


# =====================================================================
# GUI IMPLEMENTATION (GTK 3 Native Ubuntu UI + Tkinter Fallback)
# =====================================================================

def launch_gtk_gui(initial_archive: Optional[str] = None) -> bool:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GLib, GdkPixbuf
    except Exception:
        return False

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False

    class TarGzGtkWindow(Gtk.Window):
        def __init__(self, archive_path: Optional[str] = None):
            super().__init__(title=f"{APP_TITLE} v{APP_VERSION}")
            self.set_default_size(820, 600)
            self.set_border_width(0)
            self.analysis: Optional[ArchiveAnalysis] = None

            header = Gtk.HeaderBar()
            header.set_show_close_button(True)
            header.props.title = APP_TITLE
            header.props.subtitle = "Auto-Dependencies • Electron Sandbox Fix • High-Res Logo Extractor"
            self.set_titlebar(header)

            open_btn = Gtk.Button(label="Open .tar.gz…")
            open_btn.connect("clicked", self.on_open_clicked)
            header.pack_start(open_btn)

            repair_antigravity_btn = Gtk.Button(label="Repair /opt App…")
            repair_antigravity_btn.set_tooltip_text("Fix icon, missing libraries, and SIGTRAP crash on an already-installed app in /opt (e.g., /opt/antigravity)")
            repair_antigravity_btn.connect("clicked", self.on_repair_folder_clicked)
            header.pack_start(repair_antigravity_btn)

            self.install_btn = Gtk.Button(label="Install Package")
            self.install_btn.get_style_context().add_class("suggested-action")
            self.install_btn.set_sensitive(False)
            self.install_btn.connect("clicked", self.on_install_clicked)
            header.pack_end(self.install_btn)

            self.uninstall_btn = Gtk.Button(label="Uninstall")
            self.uninstall_btn.get_style_context().add_class("destructive-action")
            self.uninstall_btn.set_sensitive(False)
            self.uninstall_btn.connect("clicked", self.on_uninstall_current_clicked)
            header.pack_end(self.uninstall_btn)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            vbox.set_margin_top(14)
            vbox.set_margin_bottom(14)
            vbox.set_margin_start(16)
            vbox.set_margin_end(16)
            self.add(vbox)

            # Top Package Banner Card with live Icon Preview
            banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            self.icon_img = Gtk.Image.new_from_icon_name("package-x-generic", Gtk.IconSize.DIALOG)
            banner_box.pack_start(self.icon_img, False, False, 4)

            title_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.pkg_title_lbl = Gtk.Label(label="<b>No .tar.gz Archive Loaded</b>", use_markup=True, xalign=0)
            self.pkg_sub_lbl = Gtk.Label(
                label="Click 'Open .tar.gz…' to install a package, or click 'Repair /opt App…' to fix an existing app like /opt/antigravity.",
                xalign=0,
            )
            self.pkg_sub_lbl.set_line_wrap(True)
            self.status_lbl = Gtk.Label(label="Status: Ready", xalign=0)
            title_vbox.pack_start(self.pkg_title_lbl, False, False, 0)
            title_vbox.pack_start(self.pkg_sub_lbl, False, False, 0)
            title_vbox.pack_start(self.status_lbl, False, False, 0)
            banner_box.pack_start(title_vbox, True, True, 0)
            vbox.pack_start(banner_box, False, False, 0)

            self.progress = Gtk.ProgressBar()
            self.progress.set_show_text(True)
            self.progress.set_text("Idle")
            vbox.pack_start(self.progress, False, False, 0)

            notebook = Gtk.Notebook()
            vbox.pack_start(notebook, True, True, 0)

            # Tab 1: Package Details & Configuration
            details_grid = Gtk.Grid(column_spacing=16, row_spacing=10)
            details_grid.set_margin_top(14)
            details_grid.set_margin_bottom(14)
            details_grid.set_margin_start(14)
            details_grid.set_margin_end(14)

            row = 0
            def add_row(label_text: str, widget: Gtk.Widget):
                nonlocal row
                lbl = Gtk.Label(label=f"<b>{label_text}</b>", use_markup=True, xalign=0)
                details_grid.attach(lbl, 0, row, 1, 1)
                details_grid.attach(widget, 1, row, 1, 1)
                row += 1

            self.name_entry = Gtk.Entry()
            self.name_entry.set_hexpand(True)
            add_row("Application Name:", self.name_entry)

            self.strategy_lbl = Gtk.Label(label="—", xalign=0)
            self.strategy_lbl.set_line_wrap(True)
            add_row("Detected Type:", self.strategy_lbl)

            self.size_lbl = Gtk.Label(label="—", xalign=0)
            add_row("Archive / Unpacked Size:", self.size_lbl)

            self.scope_combo = Gtk.ComboBoxText()
            self.scope_combo.append("system", "System-Wide (/opt & /usr/local/bin — Auto-installs apt libs & AppArmor profile)")
            self.scope_combo.append("user", "User Local (~/.local/opt & ~/.local/bin)")
            self.scope_combo.set_active_id("system")
            add_row("Installation Scope:", self.scope_combo)

            self.exec_combo = Gtk.ComboBoxText()
            add_row("Main Executable:", self.exec_combo)

            self.icon_lbl = Gtk.Label(label="—", xalign=0)
            add_row("Detected App Icon:", self.icon_lbl)

            self.cat_combo = Gtk.ComboBoxText()
            for cat in ("Development", "Utility", "Network", "Office", "Graphics", "AudioVideo", "Game", "System"):
                self.cat_combo.append(cat, cat)
            self.cat_combo.set_active_id("Development")
            add_row("Ubuntu Menu Category:", self.cat_combo)

            self.deps_chk = Gtk.CheckButton(label="Automatically scan (ldd) & install missing Ubuntu libraries and dependencies via apt")
            self.deps_chk.set_active(True)
            add_row("Auto-Dependencies:", self.deps_chk)

            self.desktop_chk = Gtk.CheckButton(label="Create Ubuntu Applications Menu (.desktop) shortcut with high-res icon")
            self.desktop_chk.set_active(True)
            add_row("Desktop Integration:", self.desktop_chk)

            self.term_chk = Gtk.CheckButton(label="Run inside a Terminal window (for CLI tools only)")
            self.term_chk.set_active(False)
            add_row("Terminal Mode:", self.term_chk)

            notebook.append_page(details_grid, Gtk.Label(label="Package Details"))

            # Tab 2: Included Files
            files_scroll = Gtk.ScrolledWindow()
            self.files_store = Gtk.ListStore(str, str, str)
            files_tree = Gtk.TreeView(model=self.files_store)
            for idx, col_title in enumerate(("Permissions", "Size", "File Path")):
                renderer = Gtk.CellRendererText()
                if idx == 0:
                    renderer.set_property("family", "Monospace")
                col = Gtk.TreeViewColumn(col_title, renderer, text=idx)
                col.set_resizable(True)
                files_tree.append_column(col)
            files_scroll.add(files_tree)
            notebook.append_page(files_scroll, Gtk.Label(label="Included Files"))

            # Tab 3: Installation Output Log
            log_scroll = Gtk.ScrolledWindow()
            self.log_view = Gtk.TextView()
            self.log_view.set_editable(False)
            self.log_view.set_monospace(True)
            log_scroll.add(self.log_view)
            self.notebook = notebook
            self.log_tab_idx = notebook.append_page(log_scroll, Gtk.Label(label="Install & Dependency Log"))

            # Tab 4: Installed .tar.gz Apps Manager
            mgr_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            mgr_vbox.set_margin_top(10)
            mgr_vbox.set_margin_bottom(10)
            mgr_vbox.set_margin_start(10)
            mgr_vbox.set_margin_end(10)

            mgr_scroll = Gtk.ScrolledWindow()
            self.installed_store = Gtk.ListStore(str, str, str, str, str, str)
            self.installed_tree = Gtk.TreeView(model=self.installed_store)
            for idx, col_title in enumerate(("App ID", "Name", "Version", "Scope", "Installed At", "Directory")):
                renderer = Gtk.CellRendererText()
                col = Gtk.TreeViewColumn(col_title, renderer, text=idx)
                col.set_resizable(True)
                self.installed_tree.append_column(col)
            mgr_scroll.add(self.installed_tree)
            mgr_vbox.pack_start(mgr_scroll, True, True, 0)

            mgr_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            refresh_btn = Gtk.Button(label="Refresh List")
            refresh_btn.connect("clicked", lambda *_: self.refresh_installed_list())
            repair_sel_btn = Gtk.Button(label="Repair Selected App (Fix Icon & Crash)")
            repair_sel_btn.connect("clicked", self.on_repair_selected_clicked)
            remove_sel_btn = Gtk.Button(label="Uninstall Selected Package")
            remove_sel_btn.get_style_context().add_class("destructive-action")
            remove_sel_btn.connect("clicked", self.on_remove_selected_clicked)
            mgr_btn_box.pack_start(refresh_btn, False, False, 0)
            mgr_btn_box.pack_start(repair_sel_btn, False, False, 0)
            mgr_btn_box.pack_end(remove_sel_btn, False, False, 0)
            mgr_vbox.pack_start(mgr_btn_box, False, False, 0)

            notebook.append_page(mgr_vbox, Gtk.Label(label="Installed Apps"))

            self.refresh_installed_list()
            if archive_path:
                self.load_archive(archive_path)

        def append_log(self, text: str):
            def _do():
                buf = self.log_view.get_buffer()
                end_iter = buf.get_end_iter()
                buf.insert(end_iter, text + "\n")
                mark = buf.create_mark(None, buf.get_end_iter(), False)
                self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
            GLib.idle_add(_do)

        def set_progress(self, fraction: float, text: Optional[str] = None):
            def _do():
                self.progress.set_fraction(max(0.0, min(1.0, fraction)))
                if text:
                    self.progress.set_text(text)
            GLib.idle_add(_do)

        def preview_icon_from_tar(self, archive_path: str, member_name: Optional[str]):
            if not member_name:
                self.icon_img.set_from_icon_name("package-x-generic", Gtk.IconSize.DIALOG)
                return
            try:
                with tarfile.open(archive_path, "r:*") as tf:
                    for m in tf.getmembers():
                        if strip_dot_slash(m.name) == member_name:
                            fobj = tf.extractfile(m)
                            if fobj:
                                loader = GdkPixbuf.PixbufLoader()
                                loader.write(fobj.read())
                                loader.close()
                                pixbuf = loader.get_pixbuf()
                                if pixbuf:
                                    scaled = pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR)
                                    self.icon_img.set_from_pixbuf(scaled)
                                    return
                            break
            except Exception:
                pass
            self.icon_img.set_from_icon_name("package-x-generic", Gtk.IconSize.DIALOG)

        def on_open_clicked(self, _btn):
            dlg = Gtk.FileChooserDialog(
                title="Select a .tar.gz or .tgz Package",
                parent=self,
                action=Gtk.FileChooserAction.OPEN,
            )
            dlg.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
            )
            flt = Gtk.FileFilter()
            flt.set_name("Tar Archives (*.tar.gz, *.tgz, *.tar.xz, *.tar.bz2)")
            for pat in ("*.tar.gz", "*.tgz", "*.tar.xz", "*.txz", "*.tar.bz2", "*.tar"):
                flt.add_pattern(pat)
            dlg.add_filter(flt)
            if dlg.run() == Gtk.ResponseType.OK:
                filename = dlg.get_filename()
                if filename:
                    self.load_archive(filename)
            dlg.destroy()

        def on_repair_folder_clicked(self, _btn):
            dlg = Gtk.FileChooserDialog(
                title="Select Installed Application Folder (e.g. /opt/antigravity)",
                parent=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            dlg.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
            )
            if pathlib.Path("/opt").is_dir():
                dlg.set_current_folder("/opt")
            if dlg.run() == Gtk.ResponseType.OK:
                folder = dlg.get_filename()
                if folder:
                    self._run_repair(folder)
            dlg.destroy()

        def load_archive(self, path: str):
            try:
                analysis = TarGzAnalyzer.analyze(path)
            except Exception as exc:
                self.append_log(f"[ERROR] Could not inspect archive: {exc}")
                return

            self.analysis = analysis
            self.pkg_title_lbl.set_markup(
                f"<span size='large' weight='bold'>{GLib.markup_escape_text(analysis.display_name)}</span> "
                f"<span foreground='#666666'>v{GLib.markup_escape_text(analysis.version)}</span>"
            )
            self.pkg_sub_lbl.set_text(analysis.archive_path)
            self.name_entry.set_text(analysis.display_name)
            self.strategy_lbl.set_text(analysis.strategy_description)
            self.size_lbl.set_text(
                f"{format_size(analysis.file_size_bytes)} compressed  /  "
                f"{format_size(analysis.uncompressed_size_bytes)} unpacked ({analysis.file_count} files)"
            )
            self.icon_lbl.set_text(analysis.primary_icon_member or "Auto-extract from bundle / .asar")
            self.preview_icon_from_tar(analysis.archive_path, analysis.primary_icon_member)

            self.exec_combo.remove_all()
            for ex in analysis.executables:
                self.exec_combo.append(ex, ex)
            if analysis.primary_executable:
                self.exec_combo.set_active_id(analysis.primary_executable)

            self.files_store.clear()
            for fpath, fsize, fmode in analysis.member_list[:1500]:
                self.files_store.append([fmode, format_size(fsize), fpath])

            existing = PackageRegistry.get_package(analysis.app_id)
            if existing or (pathlib.Path("/opt") / analysis.app_id).is_dir():
                inst_loc = existing.install_dir if existing else f"/opt/{analysis.app_id}"
                self.status_lbl.set_markup(
                    f"<b>Status:</b> <span foreground='#2E7D32'>Installed in {GLib.markup_escape_text(inst_loc)}</span>"
                )
                self.install_btn.set_label("Reinstall & Fix Package")
                self.uninstall_btn.set_sensitive(existing is not None)
            else:
                self.status_lbl.set_markup("<b>Status:</b> Ready to install")
                self.install_btn.set_label("Install Package")
                self.uninstall_btn.set_sensitive(False)

            self.install_btn.set_sensitive(True)
            self.set_progress(0.0, "Ready to install")

        def on_install_clicked(self, _btn):
            if not self.analysis:
                return
            scope = self.scope_combo.get_active_id() or "system"
            custom_name = self.name_entry.get_text().strip() or self.analysis.display_name
            selected_exec = self.exec_combo.get_active_id()
            create_desktop = self.desktop_chk.get_active()
            auto_deps = self.deps_chk.get_active()
            category = self.cat_combo.get_active_id() or "Development"
            term_mode = self.term_chk.get_active()

            self.install_btn.set_sensitive(False)
            self.notebook.set_current_page(self.log_tab_idx)
            self.set_progress(0.05, "Installing & resolving dependencies...")

            def worker():
                try:
                    if scope == "system" and os.geteuid() != 0:
                        cmd = [
                            "pkexec",
                            sys.executable,
                            os.path.abspath(__file__),
                            "install",
                            self.analysis.archive_path,
                            "--system",
                            "--name", custom_name,
                            "--category", category,
                        ]
                        if selected_exec:
                            cmd.extend(["--exec", selected_exec])
                        if not create_desktop:
                            cmd.append("--no-desktop")
                        if not auto_deps:
                            cmd.append("--no-deps")
                        if term_mode:
                            cmd.append("--terminal")
                        self.append_log("==> Elevating privileges with Polkit (pkexec) for system install & apt dependencies...")
                        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                        if proc.stdout:
                            for line in proc.stdout:
                                self.append_log(line.rstrip())
                        proc.wait()
                        if proc.returncode != 0:
                            raise RuntimeError(f"Elevated installation exited with code {proc.returncode}")
                    else:
                        TarGzInstallerEngine.install(
                            self.analysis,
                            scope=scope,
                            custom_app_name=custom_name,
                            selected_executable=selected_exec,
                            create_desktop_entry=create_desktop,
                            category=category,
                            terminal_app=term_mode,
                            auto_deps=auto_deps,
                            log_fn=self.append_log,
                            progress_fn=lambda p: self.set_progress(p, f"Installing ({int(p*100)}%)..."),
                        )
                    self.set_progress(1.0, "Installation Complete!")
                    GLib.idle_add(lambda: self.load_archive(self.analysis.archive_path))
                    GLib.idle_add(self.refresh_installed_list)
                except Exception as exc:
                    self.append_log(f"[ERROR] {exc}")
                    self.set_progress(0.0, "Installation Failed")
                finally:
                    GLib.idle_add(lambda: self.install_btn.set_sensitive(True))

            threading.Thread(target=worker, daemon=True).start()

        def on_uninstall_current_clicked(self, _btn):
            if not self.analysis:
                return
            self._run_uninstall(self.analysis.app_id)

        def on_remove_selected_clicked(self, _btn):
            sel = self.installed_tree.get_selection()
            model, treeiter = sel.get_selected()
            if treeiter is not None:
                app_id = model[treeiter][0]
                self._run_uninstall(app_id)

        def on_repair_selected_clicked(self, _btn):
            sel = self.installed_tree.get_selection()
            model, treeiter = sel.get_selected()
            if treeiter is not None:
                app_id = model[treeiter][0]
                self._run_repair(app_id)

        def _run_repair(self, target_app_or_dir: str):
            self.notebook.set_current_page(self.log_tab_idx)
            self.set_progress(0.1, f"Repairing {target_app_or_dir}...")

            def worker():
                try:
                    if os.geteuid() != 0 and shutil.which("pkexec"):
                        cmd = ["pkexec", sys.executable, os.path.abspath(__file__), "fix", target_app_or_dir]
                        self.append_log(f"==> Elevating via Polkit (pkexec) to repair {target_app_or_dir}...")
                        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                        if proc.stdout:
                            for line in proc.stdout:
                                self.append_log(line.rstrip())
                        proc.wait()
                        if proc.returncode != 0:
                            raise RuntimeError(f"Repair exited with code {proc.returncode}")
                    else:
                        TarGzInstallerEngine.repair_existing_installation(target_app_or_dir, log_fn=self.append_log)
                    self.set_progress(1.0, "Repair Complete!")
                    GLib.idle_add(self.refresh_installed_list)
                except Exception as exc:
                    self.append_log(f"[ERROR] Repair failed: {exc}")
                    self.set_progress(0.0, "Repair Failed")

            threading.Thread(target=worker, daemon=True).start()

        def _run_uninstall(self, app_id: str):
            pkg = PackageRegistry.get_package(app_id)
            if not pkg:
                return
            self.notebook.set_current_page(self.log_tab_idx)

            def worker():
                try:
                    if pkg.scope == "system" and os.geteuid() != 0:
                        cmd = ["pkexec", sys.executable, os.path.abspath(__file__), "remove", app_id]
                        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                        if proc.stdout:
                            for line in proc.stdout:
                                self.append_log(line.rstrip())
                        proc.wait()
                    else:
                        TarGzInstallerEngine.uninstall(app_id, log_fn=self.append_log)
                    self.set_progress(1.0, f"Uninstalled {pkg.display_name}")
                    GLib.idle_add(self.refresh_installed_list)
                    if self.analysis and self.analysis.app_id == app_id:
                        GLib.idle_add(lambda: self.load_archive(self.analysis.archive_path))
                except Exception as exc:
                    self.append_log(f"[ERROR] Uninstall failed: {exc}")

            threading.Thread(target=worker, daemon=True).start()

        def refresh_installed_list(self):
            self.installed_store.clear()
            for pkg in PackageRegistry.list_packages():
                self.installed_store.append([
                    pkg.app_id,
                    pkg.display_name,
                    pkg.version,
                    pkg.scope,
                    pkg.installed_at,
                    pkg.install_dir,
                ])

    win = TarGzGtkWindow(initial_archive)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return True


def launch_tk_gui(initial_archive: Optional[str] = None) -> bool:
    """Fallback Tkinter GUI if GTK3 is unavailable."""
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except Exception:
        return False

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False

    root = tk.Tk()
    root.title(f"{APP_TITLE} v{APP_VERSION}")
    root.geometry("760x520")

    state: Dict[str, Optional[ArchiveAnalysis]] = {"analysis": None}

    top_frame = ttk.Frame(root, padding=12)
    top_frame.pack(fill=tk.X)

    title_var = tk.StringVar(value="No .tar.gz Archive Loaded")
    sub_var = tk.StringVar(value="Open a .tar.gz or .tgz file to inspect and install.")
    ttk.Label(top_frame, textvariable=title_var, font=("Sans", 13, "bold")).pack(anchor=tk.W)
    ttk.Label(top_frame, textvariable=sub_var).pack(anchor=tk.W)

    btn_bar = ttk.Frame(root, padding=(12, 4))
    btn_bar.pack(fill=tk.X)

    log_text = tk.Text(root, height=14, font=("Monospace", 9))

    def log_fn(msg: str):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)

    def load_file(path: str):
        try:
            a = TarGzAnalyzer.analyze(path)
            state["analysis"] = a
            title_var.set(f"{a.display_name} v{a.version} ({a.strategy})")
            sub_var.set(f"{a.strategy_description} — {format_size(a.file_size_bytes)}")
            install_btn.config(state=tk.NORMAL)
            log_fn(f"Loaded: {a.archive_path} ({a.file_count} files), Icon: {a.primary_icon_member}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_open():
        p = filedialog.askopenfilename(
            title="Select .tar.gz Archive",
            filetypes=[("Tar Archives", "*.tar.gz *.tgz *.tar.xz *.tar.bz2 *.tar")],
        )
        if p:
            load_file(p)

    def on_install():
        a = state["analysis"]
        if not a:
            return
        threading.Thread(
            target=lambda: TarGzInstallerEngine.install(a, scope="user", log_fn=log_fn),
            daemon=True,
        ).start()

    ttk.Button(btn_bar, text="Open .tar.gz…", command=on_open).pack(side=tk.LEFT, padx=4)
    install_btn = ttk.Button(btn_bar, text="Install Package", command=on_install, state=tk.DISABLED)
    install_btn.pack(side=tk.RIGHT, padx=4)

    log_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

    if initial_archive:
        load_file(initial_archive)

    root.mainloop()
    return True


# =====================================================================
# CLI ENTRYPOINT
# =====================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="targz-installer",
        description="Install, repair, inspect, and uninstall .tar.gz / .tgz packages on Ubuntu like .deb files.",
    )
    parser.add_argument(
        "--self-install",
        action="store_true",
        help="Install targz-installer to your system and associate .tar.gz / .tgz files for double-click installation.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # install subcommand
    p_inst = sub.add_parser("install", help="Install a .tar.gz archive (with auto-dependency resolution & sandbox fix)")
    p_inst.add_argument("archive", help="Path to the .tar.gz / .tgz file")
    p_inst.add_argument("--system", action="store_true", help="Install system-wide to /opt and /usr/local/bin (requires root)")
    p_inst.add_argument("--user", action="store_true", help="Install for current user in ~/.local")
    p_inst.add_argument("--name", help="Override display name for the application")
    p_inst.add_argument("--exec", dest="selected_exec", help="Specify which executable inside the archive to link")
    p_inst.add_argument("--category", default="Development", help="Desktop menu category (default: Development)")
    p_inst.add_argument("--no-desktop", action="store_true", help="Do not create a .desktop launcher")
    p_inst.add_argument("--no-deps", action="store_true", help="Skip automatic ldd/apt dependency installation")
    p_inst.add_argument("--terminal", action="store_true", help="Mark desktop launcher to run in terminal")

    # fix / repair subcommand
    p_fix = sub.add_parser("fix", aliases=["repair"], help="Fix icon, missing libraries, and SIGTRAP sandbox crash for an installed app (e.g. /opt/antigravity)")
    p_fix.add_argument("target", help="App ID or path to installed directory (e.g. antigravity or /opt/antigravity)")

    # remove subcommand
    p_rem = sub.add_parser("remove", aliases=["uninstall"], help="Uninstall a previously installed .tar.gz package")
    p_rem.add_argument("app_id", help="Application ID (see 'targz-installer list')")

    # list subcommand
    sub.add_parser("list", help="List all installed .tar.gz packages")

    # info subcommand
    p_info = sub.add_parser("info", help="Inspect a .tar.gz archive without installing")
    p_info.add_argument("archive", help="Path to the .tar.gz / .tgz file")

    # gui subcommand
    p_gui = sub.add_parser("gui", help="Open the graphical package installer")
    p_gui.add_argument("archive", nargs="?", help="Optional .tar.gz file to open")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) == 1 and not argv[0].startswith("-") and argv[0] not in ("install", "remove", "uninstall", "list", "info", "gui", "fix", "repair"):
        archive_arg = argv[0]
        if launch_gtk_gui(archive_arg) or launch_tk_gui(archive_arg):
            return 0
        print("[INFO] No GUI display detected; falling back to CLI installation.")
        argv = ["install", archive_arg]

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.self_install:
        perform_self_install(system_wide=(os.geteuid() == 0))
        return 0

    if args.subcommand == "info":
        a = TarGzAnalyzer.analyze(args.archive)
        print(f"Package Name     : {a.display_name}")
        print(f"App ID           : {a.app_id}")
        print(f"Detected Version : {a.version}")
        print(f"Archive Size     : {format_size(a.file_size_bytes)} (Unpacked: {format_size(a.uncompressed_size_bytes)})")
        print(f"Files Count      : {a.file_count}")
        print(f"Electron App     : {'Yes' if a.is_electron_app else 'No'}")
        print(f"Install Strategy : {a.strategy} — {a.strategy_description}")
        print(f"Primary Binary   : {a.primary_executable or 'None'}")
        print(f"Best App Icon    : {a.primary_icon_member or 'None'}")
        if a.warnings:
            print(f"Security Warnings: {', '.join(a.warnings)}")
        return 0

    if args.subcommand in ("fix", "repair"):
        ok = TarGzInstallerEngine.repair_existing_installation(args.target)
        return 0 if ok else 1

    if args.subcommand == "list":
        pkgs = PackageRegistry.list_packages()
        if not pkgs:
            print("No .tar.gz packages currently installed via targz-installer.")
            return 0
        print(f"{'APP ID':<22} {'NAME':<24} {'VERSION':<12} {'SCOPE':<8} {'INSTALL DIR'}")
        print("-" * 90)
        for p in pkgs:
            print(f"{p.app_id:<22} {p.display_name:<24} {p.version:<12} {p.scope:<8} {p.install_dir}")
        return 0

    if args.subcommand in ("remove", "uninstall"):
        ok = TarGzInstallerEngine.uninstall(args.app_id)
        return 0 if ok else 1

    if args.subcommand == "install":
        scope = "system" if args.system else ("user" if args.user else ("system" if os.geteuid() == 0 else "user"))
        a = TarGzAnalyzer.analyze(args.archive)
        TarGzInstallerEngine.install(
            a,
            scope=scope,
            custom_app_name=args.name,
            selected_executable=args.selected_exec,
            create_desktop_entry=not args.no_desktop,
            category=args.category,
            terminal_app=args.terminal,
            auto_deps=not args.no_deps,
        )
        return 0

    initial_file = getattr(args, "archive", None)
    if launch_gtk_gui(initial_file) or launch_tk_gui(initial_file):
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
