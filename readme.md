# NeonBridge

A Python macOS file explorer for a jailbroken iPhone, using USB port forwarding with `iproxy`, SSH, and SFTP. The interface uses a dark, cyan sci-fi style.

## Documentation

- [Developer documentation](documentation.md): architecture, state formats, operation behavior, testing, and limitations.
- [Changelog](changelog.md): implementation milestones and changes.

## Requirements

- macOS and Python 3.10 or newer for development. Development and packaging have been checked on macOS 26.2, Apple Silicon, with Python 3.13.14; other combinations are not validated.
- `iproxy` installed separately and available in `PATH`, `/opt/homebrew/bin/iproxy`, or `/usr/local/bin/iproxy`.
- A connected, unlocked, trusted iPhone with an active jailbreak SSH server and working SFTP subsystem. The target device in this project runs iOS 18; compatibility depends on the installed SSH server and jailbreak.
- Valid SSH credentials. `root` and `mobile` are selectable. Enter the actual password; `alpine` is an example for unchanged installations, not a stored default.

Python dependencies are in [requirements.txt](requirements.txt). The app uses PySide6 Essentials, Paramiko, and keyring; PyInstaller provides packaging.

## Development setup

From the project directory:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

After setup, `Launch NeonBridge.command` also launches the source version from Finder. It requires the local virtual environment.

## Connect the iPhone

1. Plug the phone into the Mac and unlock/trust it as required.
2. Ensure the phone's jailbreak SSH server is running.
3. Launch NeonBridge, choose the SSH account, enter its password, and click **CONNECT**.
4. Verify and accept the displayed SSH fingerprint on first connection.

The app implements the existing workflow:

```sh
iproxy 2222 22
ssh -p 2222 root@localhost
```

It starts iproxy when the local port is free. If port 2222 is occupied, it attempts to use the existing listener. It does not assume every listener is iproxy or terminate processes it did not start. Disconnecting or exiting stops only its own proxy.

Optional **Remember in Keychain** stores credentials in macOS Keychain. Leaving the password field blank attempts to retrieve a saved credential for the selected account.

## Use the explorer

- Browse phone files on the left and choose a Mac destination on the right.
- Default phone shortcuts are `/var/mobile/Media` and `/var/mobile/Media/DCIM`; availability and access depend on the phone.
- Double-click folders, edit paths, use UP/REFRESH, and save favorite locations.
- Click Name, Size, or Modified headers to sort. Size uses raw bytes, not the formatted MB label.
- Drag the horizontal divider or click **EXPAND BROWSERS** for more browser space.
- Select phone files/folders and click **DOWNLOAD SELECTED**. Folder downloads are recursive; choose Skip, Rename, or Overwrite for existing destination files.
- **FIND VIDEOS** searches the current phone folder recursively by extension, minimum size, and optional modification date. STOP SEARCH retains results found so far.
- **RETRY / RESUME** repeats the last batch during the current session. After relaunch, reselect the same source and destination to resume compatible partial files.
- History records completed downloads. Select a history item and click **OPEN IN FINDER** to reveal its Mac copy.

### iPhone right-click actions

Download to Mac, Copy, Cut, Paste, Rename, Delete, New Folder, Copy Path, Properties, and Refresh are available. Copy/cut/paste operate between folders on the same iPhone connection, using an internal clipboard. They do not paste Mac Finder files into the phone.

With the phone list focused: Cmd+C copies, Cmd+X cuts, Cmd+V pastes, F2 renames, and Backspace requests deletion. Multi-selection works; nested selections are reduced to their selected parent for remote operations.

### Deletion after transfer

**Delete originals after verified transfer** forces checksum verification and asks for confirmation before starting or retrying a batch. Each completed Mac copy and phone original is rechecked before the phone file is permanently deleted. Skipped or failed downloads retain their originals. Cancellation prevents subsequent deletion when observed; it cannot undo a deletion already completed.

Alternatively, select a verified history entry and choose **DELETE VERIFIED ORIGINAL**. Right-click **Delete** is a separate, direct filesystem deletion action: it does not require a downloaded copy and permanently removes selected folders and their contents after confirmation.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q neonbridge run.py
```

The 15 automated tests use in-memory or local SFTP doubles, not a live phone. They cover download verification/resume, deletion guards, recursive copy, collisions, cancellation, invalid names, and symlink-safe deletion. See [documentation.md](documentation.md) for GUI checks and hardware validation.

## Build the macOS app

```sh
PYINSTALLER_CONFIG_DIR="$PWD/build/pyinstaller-cache" \
  .venv/bin/pyinstaller --noconfirm --windowed --name NeonBridge run.py
```

The result is `dist/NeonBridge.app`. This command replaces existing output; quit the previous app before rebuilding. Quit and reopen the app after an update to load the new code.

The current build is for Apple Silicon, requires a separately installed iproxy, and has no Developer ID signature or notarization. PyInstaller applies local ad-hoc signing. Build and validate on the target architecture before distributing.

## Troubleshooting

- **iproxy missing:** install it separately or ensure its executable is at a supported path.
- **Connection failure:** check cable, trust/unlock state, active jailbreak, phone SSH server, and whether another service owns port 2222.
- **Authentication failure:** check the account and current password.
- **SFTP unavailable:** SSH login succeeded, but the phone's SFTP subsystem could not be opened.
- **Host key changed:** verify the connected device. The current trust store is keyed by `[127.0.0.1]:2222`; switching phones can cause this error. The app will not silently replace trust.
- **Partial file mismatch:** the source identity changed or partial metadata is missing. Preserve or remove the `.partial` and `.partial.json` pair intentionally before restarting; do not blindly append to an unrelated file.
- **Permission denied:** remote operations use the permissions of the selected SSH account.

## Current boundaries

No Mac-to-phone uploads, Finder clipboard integration, drag-and-drop, thumbnails, automatic reconnect, persistent pending queue, or multi-phone profiles yet. Copying between phone folders streams through the Mac. Search identifies videos by extension. Remote copies do not preserve file ownership, permissions, or timestamps. Transfer history is for downloads, not a complete audit of remote mutations.
