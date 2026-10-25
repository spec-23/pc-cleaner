# Drive Cleaner

A small Windows desktop app that finds junk files on your drives and lists the
applications you can uninstall. Built with Python + Tkinter, no third-party
packages.

## Run it

Double-click **`Drive Cleaner.bat`**, or:

```
python app.py
```

Optionally, click **Restart as administrator** in the top-right corner. Without
it, folders such as `C:\Windows\Temp` and the Windows Update cache are skipped.

## How it works

### Clean up disks tab

| Category | What it removes | Default |
|---|---|---|
| Your temporary files | `%TEMP%`, files untouched for 24h | on |
| Windows temporary files | `C:\Windows\Temp` (admin) | on |
| Browser caches | Chrome / Edge / Brave / Firefox cache folders | on |
| Developer package caches | pip, npm, Yarn, pnpm, NuGet caches | on |
| Crash dumps & error reports | `CrashDumps`, WER queues, minidumps | on |
| Windows Update download cache | `SoftwareDistribution\Download` (admin) | on |
| Delivery Optimization cache | update share cache (admin) | on |
| Windows log files | `C:\Windows\Logs\CBS` / `DISM` (admin) | off |
| Old installers in Downloads | `.exe/.msi/.msix` older than 30 days | off |
| Recycle Bin | empties it (permanent) | off |
| Previous Windows installation | `C:\Windows.old` (permanent) | off |

Nothing happens until you press **Clean selected** and confirm the preview.
Regular deletions go through the Windows shell, so they land in the Recycle Bin
and can be restored. Categories marked *permanent* skip the Recycle Bin and get
a second confirmation.

Files sitting in the Recycle Bin still occupy disk space, so when a cleanup
finishes the app asks whether to empty it. Say yes and the space is actually
returned to the drive.

Files that are locked by a running program are skipped and reported in the
activity log. Close browsers before cleaning browser caches for best results.

### Installed apps tab

Reads the uninstall registry (64-bit, 32-bit and per-user) plus Microsoft Store
packages, and shows name, publisher, version, size, install date and source.

- **Search** filters by name or publisher, click a column header to sort.
- **Uninstall selected** launches each app's own uninstaller (UAC prompts appear
  where needed). Works on multi-selection.
- **Use silent uninstall when available** prefers the app's `QuietUninstallString`.
- **Open install folder** and **Copy command** help with stubborn apps that have
  no working uninstaller — you can find their folder or paste the command into a
  terminal.

Some uninstallers leave their entry in the list until you press **Refresh list**.

## Notes

- No admin rights are required to run, only to clean system locations.
- Nothing is uploaded anywhere; everything happens locally.
- `C:\Windows.old` is best removed with Windows Disk Cleanup if the built-in
  category fails.

## Files

```
app.py         GUI (Tkinter)
scanner.py     scanning + safe deletion engine
apps.py        installed-app discovery + uninstaller launching
Drive Cleaner.bat   double-click launcher (no console window)
```
