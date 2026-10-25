"""apps.py - enumerate installed applications and launch their uninstallers."""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import winreg

UNINSTALL_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"

_REG_SOURCES = [
    (winreg.HKEY_LOCAL_MACHINE, UNINSTALL_ROOT, winreg.KEY_WOW64_64KEY, "64-bit"),
    (winreg.HKEY_LOCAL_MACHINE, UNINSTALL_ROOT, winreg.KEY_WOW64_32KEY, "32-bit"),
    (winreg.HKEY_CURRENT_USER, UNINSTALL_ROOT, 0, "User"),
]

_SKIP_RELEASE_TYPES = {"update", "hotfix", "security update", "servicepack", "service pack"}

# Store apps that come with Windows and should not be listed as "bloat".
_STORE_SKIP = {
    "Microsoft.WindowsStore",
    "Microsoft.DesktopAppInstaller",
    "Microsoft.StorePurchaseApp",
    "Microsoft.VCLibs",
    "Microsoft.UI.Xaml",
    "Microsoft.NET.Native",
    "Microsoft.Windows.ShellExperienceHost",
    "Microsoft.Windows.StartMenuExperienceHost",
    "Microsoft.Windows.Search",
    "Microsoft.Windows.Cortana",
    "Microsoft.Win32WebViewHost",
    "Microsoft.Windows.SecureAssessmentBrowser",
    "Microsoft.Services.Store.Engagement",
}

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Built-in packages whose manifest only exposes an "ms-resource:" id.
_STORE_NAMES = {
    "ms-notepad": "Notepad",
    "ms-paint": "Paint",
    "ms-photos": "Photos",
    "ms-clock": "Clock",
    "ms-settings": "Settings",
    "ms-quick-assist": "Quick Assist",
    "ms-devhome": "Developer Home",
    "WindowsCalculator": "Calculator",
    "WindowsCamera": "Camera",
    "WindowsTerminal": "Windows Terminal",
    "WindowsAlarms": "Alarms & Clock",
    "WindowsSoundRecorder": "Sound Recorder",
    "WindowsFeedbackHub": "Feedback Hub",
    "Windows.Photos": "Photos",
    "Windows.Media.Player": "Media Player",
    "ZuneMusic": "Media Player",
    "YourPhone": "Phone Link",
    "BingNews": "News",
    "BingWeather": "Weather",
    "BingSearch": "Search",
    "MicrosoftStickyNotes": "Sticky Notes",
    "MicrosoftStartFeed": "Microsoft Start",
    "MicrosoftTeams": "Microsoft Teams",
    "GetHelp": "Get Help",
    "SecHealthUI": "Windows Security",
    "XBOX": "Xbox",
    "GamingServices": "Gaming Services",
    "MinecraftUWP": "Minecraft",
    "WidgetsPlatformRuntime": "Widgets Platform Runtime",
}

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class App:
    name: str
    version: str = ""
    publisher: str = ""
    size_kb: int = 0
    install_date: str = ""
    location: str = ""
    uninstall: str = ""
    quiet_uninstall: str = ""
    source: str = ""
    store_id: str = ""
    system: bool = False

    @property
    def is_store(self) -> bool:
        return bool(self.store_id)


def _read(key, name):
    try:
        value, _kind = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_date(raw) -> str:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _registry_apps() -> list[App]:
    found: list[App] = []
    for hive, subkey, view, source in _REG_SOURCES:
        try:
            root = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    child_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    child = winreg.OpenKey(root, child_name, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with child:
                    name = _read(child, "DisplayName")
                    if not name or not str(name).strip():
                        continue
                    if _as_int(_read(child, "SystemComponent")) == 1:
                        continue
                    if _read(child, "ParentKeyName"):
                        continue
                    release = str(_read(child, "ReleaseType") or "").strip().lower()
                    if release in _SKIP_RELEASE_TYPES:
                        continue

                    uninstall = str(_read(child, "UninstallString") or "").strip()
                    quiet = str(_read(child, "QuietUninstallString") or "").strip()
                    if not uninstall and not quiet:
                        continue

                    found.append(
                        App(
                            name=str(name).strip(),
                            version=str(_read(child, "DisplayVersion") or "").strip(),
                            publisher=str(_read(child, "Publisher") or "").strip(),
                            size_kb=_as_int(_read(child, "EstimatedSize")),
                            install_date=_format_date(_read(child, "InstallDate")),
                            location=str(_read(child, "InstallLocation") or "").strip(),
                            uninstall=uninstall,
                            quiet_uninstall=quiet,
                            source=source,
                        )
                    )
    return found


_PUBLISHER_CN = re.compile(r"CN=([^,]+)", re.IGNORECASE)


def _manifest_info(install_location: str) -> tuple[str, bool]:
    """Read (DisplayName, is_real_application) from AppxManifest.xml.

    is_real_application is False for runtime/framework packages that have no
    launchable app inside them, so they never show up in the list.
    """
    if not install_location:
        return "", True
    manifest = os.path.join(install_location, "AppxManifest.xml")
    if not os.path.isfile(manifest):
        return "", True
    try:
        root = ET.parse(manifest).getroot()
    except (ET.ParseError, OSError):
        return "", True

    display = ""
    is_application = False
    for element in root.iter():
        # Tags carry a per-version namespace; compare the local name only.
        local = element.tag.rsplit("}", 1)[-1]
        if local == "Application":
            is_application = True
        elif local == "DisplayName" and not display:
            text = (element.text or "").strip()
            if text and not text.lower().startswith("ms-resource:"):
                display = text
    return display, is_application


def _friendly_package_name(package_name: str, install_location: str) -> str:
    """Best-effort human readable name. Returns "" to hide system plumbing."""
    if _GUID.match(package_name):
        return ""
    display, _is_app = _manifest_info(install_location)
    if display:
        return display
    if package_name in _STORE_NAMES:
        return _STORE_NAMES[package_name]
    fallback = package_name
    if fallback.startswith("Microsoft."):
        fallback = fallback[len("Microsoft."):]
    return fallback


def _store_apps() -> list[App]:
    command = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "@(Get-AppxPackage | Where-Object { -not $_.IsFramework -and -not $_.IsResourcePackage } | "
        "Select-Object Name,Version,PackageFullName,Publisher,InstallLocation,SignatureKind) | "
        "ConvertTo-Json -Compress -Depth 3"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]

    result: list[App] = []
    for item in data:
        full_name = str(item.get("PackageFullName") or "")
        name = str(item.get("Name") or "")
        if not full_name or not name:
            continue
        if any(name.startswith(prefix) for prefix in _STORE_SKIP):
            continue
        location = str(item.get("InstallLocation") or "")
        display, is_application = _manifest_info(location)
        if not is_application:
            continue  # runtime / framework package, nothing to uninstall
        if not display:
            display = _friendly_package_name(name, location)
        if not display:
            continue  # system infrastructure package, not a user-facing app
        publisher = str(item.get("Publisher") or "")
        match = _PUBLISHER_CN.search(publisher)
        result.append(
            App(
                name=display,
                version=str(item.get("Version") or ""),
                publisher=(match.group(1) if match else publisher),
                location=location,
                source="Store",
                store_id=full_name,
                system=_as_int(item.get("SignatureKind")) == 4,
            )
        )
    return result


def list_apps(include_store: bool = True) -> list[App]:
    """All installed applications, de-duplicated across registry views."""
    apps = _registry_apps()
    if include_store:
        try:
            apps.extend(_store_apps())
        except Exception:
            pass

    # De-duplicate identical entries (32/64-bit views often overlap).
    best: dict[tuple[str, str], App] = {}
    for app in apps:
        key = (app.name.lower(), app.version.lower())
        current = best.get(key)
        if current is None:
            best[key] = app
        elif current.size_kb == 0 and app.size_kb:
            best[key] = app

    return sorted(best.values(), key=lambda a: a.name.lower())


# --------------------------------------------------------------------------- #
# launching uninstallers
# --------------------------------------------------------------------------- #

def _split_command(command: str) -> tuple[str, str]:
    """Split a registry command line into (executable, arguments).

    Handles the three shapes found in the wild:
      "C:\\path\\setup.exe" /uninstall
      MsiExec.exe /X{GUID}
      C:\\ProgramData\\Package Cache\\{guid}\\setup.exe  /uninstall
    """
    command = os.path.expandvars(command.strip())
    if not command:
        return "", ""

    if command.startswith('"'):
        end = command.find('"', 1)
        if end > 0:
            return command[1:end], command[end + 1:].strip()

    if os.path.isfile(command):
        return command, ""

    # Mimic CreateProcess: with an unquoted path containing spaces, try the
    # longest prefix that exists on disk.
    parts = command.split()
    for index in range(len(parts), 0, -1):
        candidate = " ".join(parts[:index])
        if os.path.isfile(candidate):
            return candidate, " ".join(parts[index:])

    executable, _, arguments = command.partition(" ")
    return executable, arguments.strip()


def _shell_execute(executable: str, arguments: str, *, elevate: bool) -> tuple[bool, str]:
    if not executable:
        return False, "Empty command"
    verb = "runas" if elevate else "open"
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, verb, executable, arguments or None, None, 1)
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)
    if rc > 32:
        return True, ""
    return False, f"ShellExecute error {rc} for {executable}"


def uninstall_command(app: App, silent: bool) -> str:
    if app.is_store:
        safe = app.store_id.replace("'", "''")
        return (
            'powershell -NoProfile -ExecutionPolicy Bypass -Command '
            f"\"Remove-AppxPackage -Package '{safe}'\""
        )
    if silent and app.quiet_uninstall:
        return app.quiet_uninstall
    return app.uninstall or app.quiet_uninstall


def launch_uninstall(app: App, silent: bool = False) -> tuple[bool, str]:
    """Start an app's uninstaller. Returns (ok, message)."""
    command = uninstall_command(app, silent)
    if not command:
        return False, "This entry has no uninstall command."

    executable, arguments = _split_command(command)
    ok, err = _shell_execute(executable, arguments, elevate=False)
    if not ok:
        # Most uninstallers need administrator rights; retry through UAC.
        ok, err2 = _shell_execute(executable, arguments, elevate=True)
        if not ok:
            return False, err2 or err
    return True, command


def open_install_location(app: App) -> tuple[bool, str]:
    path = (app.location or "").strip().strip('"')
    if not path:
        return False, "No install location recorded for this app."
    path = os.path.expandvars(path)
    if not os.path.isdir(path):
        return False, f"Folder not found:\n{path}"
    try:
        os.startfile(path)  # noqa: S606 - intentional
        return True, path
    except OSError as exc:
        return False, str(exc)


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
