"""scanner.py - drive scanning and safe cleanup engine for Drive Cleaner.

Everything here is Windows specific (ctypes / recycle bin shell APIs).
Nothing in this module touches the user interface directly.
"""

from __future__ import annotations

import ctypes
import fnmatch
import os
import shutil
import stat
import string
import time
from ctypes import wintypes
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #

MAX_FILES = 400_000  # safety valve so a scan can never run away forever


def human_size(num) -> str:
    """Format a byte count the way Explorer does (1.4 GB, 812 KB ...)."""
    if num is None:
        return "—"
    value = float(num)
    if value < 1024:
        return f"{value:.0f} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
    return f"{value:.1f} TB"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _norm(path: str) -> str:
    return os.path.normpath(os.path.expandvars(path))


# --------------------------------------------------------------------------- #
# drive + recycle bin info
# --------------------------------------------------------------------------- #

def drive_stats() -> list[dict]:
    """Return fixed/removable drives with total/used/free byte counts."""
    drives: list[dict] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return drives
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        root = f"{letter}:\\"
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
        except Exception:
            continue
        # 2 = removable, 3 = fixed.  Skip network/optical/ramdisk.
        if drive_type not in (2, 3):
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        drives.append(
            {
                "root": root,
                "letter": letter,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "removable": drive_type == 2,
            }
        )
    return drives


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]


def recycle_bin_stats() -> tuple[int, int]:
    """(bytes, item count) currently sitting in the Recycle Bin."""
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    try:
        hr = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if hr != 0:
            return 0, 0
    except Exception:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin() -> bool:
    SHERB_NOCONFIRMATION = 0x1
    SHERB_NOPROGRESSUI = 0x2
    SHERB_NOSOUND = 0x4
    try:
        hr = ctypes.windll.shell32.SHEmptyRecycleBinW(
            None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        )
        return hr == 0
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# file system walking (reparse points are never followed)
# --------------------------------------------------------------------------- #

_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _entry_stat(entry: os.DirEntry):
    """stat() an entry without following links/junctions. None on failure."""
    try:
        return entry.stat(follow_symlinks=False)
    except OSError:
        return None


def _is_link_like(entry: os.DirEntry, st) -> bool:
    if st is None:
        return True
    if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    return False


def iter_files(
    root: str,
    *,
    recurse: bool = True,
    pattern: str = "*",
    min_age_days: float = 0.0,
    stop: callable | None = None,
):
    """Yield (path, size) for files under root.

    Junctions / symlinks are skipped so we can never walk into a loop.
    """
    cutoff = (time.time() - min_age_days * 86400.0) if min_age_days else 0.0
    pattern = pattern.lower()
    stack = [root]
    while stack:
        if stop and stop():
            return
        current = stack.pop()
        try:
            with os.scandir(current) as scan:
                entries = list(scan)
        except OSError:
            continue
        for entry in entries:
            st = _entry_stat(entry)
            if _is_link_like(entry, st):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recurse:
                        stack.append(entry.path)
                    continue
            except OSError:
                continue
            if pattern != "*" and not fnmatch.fnmatch(entry.name.lower(), pattern):
                continue
            if cutoff and st.st_mtime > cutoff:
                continue
            yield entry.path, st.st_size


def path_size(path: str, stop: callable | None = None) -> int:
    """Recursive size in bytes (0 if unreadable)."""
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    for _path, size in iter_files(path, stop=stop):
        total += size
    return total


# --------------------------------------------------------------------------- #
# targets + categories
# --------------------------------------------------------------------------- #

@dataclass
class Target:
    """One place on disk that a category wants to clean.

    mode:
        contents - delete the entries *inside* path (keep the folder itself)
        path     - delete path itself
        glob     - walk path, delete matching files older than min_age_days
        recycle  - empty the Recycle Bin
    """

    path: str = ""
    mode: str = "contents"
    pattern: str = "*"
    min_age_days: float = 0.0
    recurse: bool = True


@dataclass
class Category:
    key: str
    label: str
    description: str
    targets_factory: callable
    default: bool = True
    permanent: bool = False
    needs_admin: bool = False
    caution: bool = False

    def targets(self) -> list[Target]:
        try:
            return [t for t in self.targets_factory() if t.mode == "recycle" or os.path.exists(t.path)]
        except Exception:
            return []


@dataclass
class ScanResult:
    key: str
    label: str
    bytes: int = 0
    files: int = 0
    locations: int = 0
    truncated: bool = False
    errors: int = 0

    @property
    def size_text(self) -> str:
        return human_size(self.bytes)


def _browser_targets() -> list[Target]:
    """Cache folders of every Chromium/Firefox profile that exists."""
    local = _norm("%LOCALAPPDATA%")
    bases = [
        os.path.join(local, "Google", "Chrome", "User Data"),
        os.path.join(local, "Microsoft", "Edge", "User Data"),
        os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"),
        os.path.join(local, "Chromium", "User Data"),
    ]
    leaf_names = ["Cache", "Code Cache", "GPUCache", os.path.join("Service Worker", "CacheStorage")]
    targets: list[Target] = []
    for base in bases:
        if not os.path.isdir(base):
            continue
        try:
            profiles = [os.path.join(base, d) for d in os.listdir(base)]
        except OSError:
            continue
        for profile in profiles:
            if not os.path.isdir(profile):
                continue
            for leaf in leaf_names:
                candidate = os.path.join(profile, leaf)
                if os.path.isdir(candidate):
                    targets.append(Target(candidate, "contents"))
            # Chrome keeps the real cache one level deeper in recent versions.
            deep = os.path.join(profile, "Cache", "Cache_Data")
            if os.path.isdir(deep):
                targets.append(Target(deep, "contents"))

    firefox = os.path.join(local, "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(firefox):
        try:
            profiles = [os.path.join(firefox, d) for d in os.listdir(firefox)]
        except OSError:
            profiles = []
        for profile in profiles:
            for leaf in ("cache2", "startupCache", "shader-cache"):
                candidate = os.path.join(profile, leaf)
                if os.path.isdir(candidate):
                    targets.append(Target(candidate, "contents"))
    return targets


def _dev_cache_targets() -> list[Target]:
    local = _norm("%LOCALAPPDATA%")
    appdata = _norm("%APPDATA%")
    candidates = [
        os.path.join(local, "pip", "Cache"),
        os.path.join(local, "npm-cache"),
        os.path.join(appdata, "npm-cache"),
        os.path.join(local, "Yarn", "Cache"),
        os.path.join(local, "pnpm", "store"),
        os.path.join(local, "NuGet", "v3-cache"),
        os.path.join(local, "Temp", "chocolatey"),
    ]
    return [Target(p, "contents") for p in candidates]


def build_categories() -> list[Category]:
    local = _norm("%LOCALAPPDATA%")
    windir = _norm("%SystemRoot%")
    userprofile = _norm("%USERPROFILE%")

    cats = [
        Category(
            key="user_temp",
            label="Your temporary files",
            description="Leftovers from installers and apps. Files touched in the last day are kept.",
            targets_factory=lambda: [Target(_norm("%TEMP%"), "contents", min_age_days=1)],
        ),
        Category(
            key="windows_temp",
            label="Windows temporary files",
            description="System-wide temp folder. Needs administrator rights.",
            targets_factory=lambda: [Target(os.path.join(windir, "Temp"), "contents", min_age_days=1)],
            needs_admin=True,
        ),
        Category(
            key="browser_cache",
            label="Browser caches",
            description="Cached pages and images for Chrome, Edge, Brave, Firefox. Logins and history untouched.",
            targets_factory=_browser_targets,
        ),
        Category(
            key="dev_caches",
            label="Developer package caches",
            description="pip, npm, Yarn, pnpm and NuGet download caches. Safe, they re-download on demand.",
            targets_factory=_dev_cache_targets,
        ),
        Category(
            key="crash_reports",
            label="Crash dumps & error reports",
            description="Memory dumps from crashed applications and Windows Error Reporting queues.",
            targets_factory=lambda: [
                Target(os.path.join(local, "CrashDumps"), "contents"),
                Target(os.path.join(local, "Microsoft", "Windows", "WER"), "contents"),
                Target(os.path.join(windir, "Minidump"), "contents"),
                Target(os.path.join(windir, "LiveKernelReports"), "contents"),
            ],
            needs_admin=True,
        ),
        Category(
            key="update_cache",
            label="Windows Update download cache",
            description="Already-installed update payloads. Windows re-downloads anything it still needs.",
            targets_factory=lambda: [
                Target(os.path.join(windir, "SoftwareDistribution", "Download"), "contents"),
            ],
            needs_admin=True,
        ),
        Category(
            key="delivery_opt",
            label="Delivery Optimization cache",
            description="Windows update share cache. Rebuilds itself automatically.",
            targets_factory=lambda: [
                Target(
                    os.path.join(
                        windir, "ServiceProfiles", "NetworkService", "AppData", "Local",
                        "Microsoft", "Windows", "DeliveryOptimization", "Cache",
                    ),
                    "contents",
                ),
            ],
            needs_admin=True,
        ),
        Category(
            key="system_logs",
            label="Windows log files (CBS / DISM)",
            description="Component servicing logs. Only needed while troubleshooting.",
            targets_factory=lambda: [
                Target(os.path.join(windir, "Logs", "CBS"), "contents"),
                Target(os.path.join(windir, "Logs", "DISM"), "contents"),
            ],
            needs_admin=True,
            default=False,
            caution=True,
        ),
        Category(
            key="downloads_installers",
            label="Old installers in Downloads",
            description="Setup .exe/.msi/.zip files older than 30 days. Double check the preview.",
            targets_factory=lambda: [
                Target(os.path.join(userprofile, "Downloads"), "glob", pattern="*.exe", min_age_days=30),
                Target(os.path.join(userprofile, "Downloads"), "glob", pattern="*.msi", min_age_days=30),
                Target(os.path.join(userprofile, "Downloads"), "glob", pattern="*.msix", min_age_days=30),
            ],
            default=False,
            caution=True,
        ),
        Category(
            key="recycle_bin",
            label="Recycle Bin",
            description="Everything you deleted previously. Emptying it is permanent.",
            targets_factory=lambda: [Target(mode="recycle")],
            default=False,
            permanent=True,
            caution=True,
        ),
        Category(
            key="windows_old",
            label="Previous Windows installation",
            description="The old Windows.old folder. Deleting it is permanent and cannot be undone.",
            targets_factory=lambda: [Target(os.path.join(os.path.splitdrive(windir)[0] + "\\", "Windows.old"), "path")],
            default=False,
            permanent=True,
            needs_admin=True,
            caution=True,
        ),
    ]
    return cats


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #

def scan_targets(targets: list[Target], stop: callable | None = None, on_progress: callable | None = None):
    """Return (bytes, files, locations, errors, truncated) for a list of targets."""
    total_bytes = 0
    total_files = 0
    locations = 0
    errors = 0
    truncated = False

    for target in targets:
        if stop and stop():
            break
        if target.mode == "recycle":
            size, count = recycle_bin_stats()
            total_bytes += size
            total_files += count
            locations += 1
            continue

        if target.mode == "path":
            size = path_size(target.path, stop)
            if size:
                total_bytes += size
                locations += 1
                if os.path.isdir(target.path):
                    total_files += sum(1 for _ in iter_files(target.path, stop=stop))
                else:
                    total_files += 1
            continue

        # contents / glob both walk the tree
        count_here = 0
        for _path, size in iter_files(
            target.path,
            recurse=target.recurse,
            pattern=target.pattern,
            min_age_days=target.min_age_days,
            stop=stop,
        ):
            total_bytes += size
            count_here += 1
            if on_progress and count_here % 500 == 0:
                on_progress(_path)
            if total_files + count_here > MAX_FILES:
                truncated = True
                break
        if truncated:
            break
        total_files += count_here
        if count_here:
            locations += 1

    return total_bytes, total_files, locations, errors, truncated


# --------------------------------------------------------------------------- #
# deletion (through the Recycle Bin whenever possible)
# --------------------------------------------------------------------------- #

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMMKDIR = 0x0200
FOF_NOERRORUI = 0x0400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _shfileop_delete(paths: list[str], permanent: bool) -> bool:
    """Delete paths with one shell call. Returns True when the call succeeded."""
    if not paths:
        return True
    # SHFileOperation needs a double-null terminated list of absolute paths.
    payload = "\0".join(paths) + "\0\0"
    flags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOCONFIRMMKDIR | FOF_NOERRORUI
    if not permanent:
        flags |= FOF_ALLOWUNDO

    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = payload
    op.pTo = None
    op.fFlags = flags
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0
    except Exception:
        return False


def _collect_paths(target: Target, stop: callable | None = None) -> list[str]:
    """Turn a target into a concrete list of paths to delete."""
    if target.mode == "path":
        return [target.path] if os.path.exists(target.path) else []

    if target.mode == "glob":
        return [p for p, _size in iter_files(
            target.path,
            recurse=target.recurse,
            pattern=target.pattern,
            min_age_days=target.min_age_days,
            stop=stop,
        )]

    # mode == contents: delete the top level entries, keep the folder
    cutoff = (time.time() - target.min_age_days * 86400.0) if target.min_age_days else 0.0
    out: list[str] = []
    try:
        with os.scandir(target.path) as scan:
            for entry in scan:
                if stop and stop():
                    break
                st = _entry_stat(entry)
                if st is None:
                    continue
                if cutoff and st.st_mtime > cutoff:
                    continue
                out.append(entry.path)
    except OSError:
        pass
    return out


def clean(categories: list[Category], *, log: callable | None = None, stop: callable | None = None) -> dict:
    """Delete every target of the given categories.

    Returns {"freed", "recycled", "removed", "failed", "permanent"}.
    "freed" is space actually given back, "recycled" was moved to the
    Recycle Bin and still occupies disk space until it is emptied.
    """
    freed = 0
    recycled = 0
    removed = 0
    failed: list[tuple[str, str]] = []
    hit_permanent = False

    for category in categories:
        if stop and stop():
            break
        targets = category.targets()
        if log:
            log(f"Cleaning: {category.label}")
        for target in targets:
            if stop and stop():
                break

            if target.mode == "recycle":
                before, _ = recycle_bin_stats()
                ok = empty_recycle_bin()
                after, _ = recycle_bin_stats()
                freed += max(0, before - after)
                if ok:
                    removed += 1
                    hit_permanent = True
                    if log:
                        log(f"  emptied Recycle Bin ({human_size(before)})")
                else:
                    failed.append(("Recycle Bin", "could not empty"))
                continue

            paths = _collect_paths(target, stop)
            if not paths:
                continue

            # Measure first so the "freed" number is real.
            sizes: dict[str, int] = {}
            for path in paths:
                sizes[path] = path_size(path, stop)

            permanent = category.permanent
            hit_permanent = hit_permanent or permanent
            batch_size = 100
            for i in range(0, len(paths), batch_size):
                if stop and stop():
                    break
                batch = paths[i:i + batch_size]
                _shfileop_delete(batch, permanent)

            for path in paths:
                if os.path.exists(path):
                    failed.append((path, "in use or access denied"))
                else:
                    size = sizes.get(path, 0)
                    if permanent:
                        freed += size
                    else:
                        recycled += size
                    removed += 1
            if log:
                log(f"  {human_size(sum(sizes.values()))} in {len(paths)} entries")

    return {
        "freed": freed,
        "recycled": recycled,
        "removed": removed,
        "failed": failed,
        "permanent": hit_permanent,
    }
