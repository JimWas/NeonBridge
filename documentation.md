# NeonBridge developer documentation

This document describes the current implementation, not a promised roadmap. Entry point: `run.py`. Runtime code lives in `neonbridge/app.py` and `neonbridge/backend.py`.

## Repository map

- `run.py`: calls the desktop application's `main()`.
- `neonbridge/app.py`: Qt interface, workers, navigation, search, queue/history, context menus, and operation orchestration.
- `neonbridge/backend.py`: proxy/SSH lifecycle, settings storage, resumable downloads, verified deletion, and remote filesystem helpers.
- `tests/test_transfer.py`: download and verified deletion tests.
- `tests/test_remote_files.py`: remote copy/delete tests using a local filesystem adapter.
- `requirements.txt`: bounded dependency ranges; not an exact lockfile.
- `Launch NeonBridge.command`: source launcher using `.venv`.
- `NeonBridge.spec`, `build/`, `dist/`: generated packaging files, ignored by version control.
- `preview*.png`: rendered development previews; not app resources.

## Architecture and threading

`Window` owns `Store`, `Connection`, a `QThreadPool`, cancellation events, and retained `Job` objects. `Job` is a `QRunnable` with `Signals.done`, `Signals.error`, and `Signals.progress`. `Window.run()` retains workers until completion and connects callbacks that update the interface on the GUI thread.

Network and large filesystem operations run in workers. User dialogs run on the GUI thread. SSH first-use trust uses `Window.trust_request`: the connection worker waits on an event while the GUI prompts for the fingerprint.

`Window.busy` prevents concurrent transfer/remote mutation batches and disables connect/download controls during those operations. Read-only browsing/search can still run concurrently. Each operation opens its own SFTP channel on the shared SSH client. The `Connection` lock protects its connect sequence; it is not a global lock around every SFTP operation.

The shared transfer cancellation event is also used by context-menu operations. Search has a separate event. Closing while jobs exist requests cancellation and refuses to exit until workers finish. Operations blocked inside network calls may not immediately observe cancellation; there is no comprehensive per-operation timeout policy yet.

## USB and SSH lifecycle

`Connection.connect(user, password, trust)`:

1. Closes any prior client and owned proxy.
2. Probes `127.0.0.1:2222`.
3. If unreachable, finds iproxy in PATH or the two supported Homebrew locations and starts `iproxy 2222 22`.
4. Loads saved SSH host keys and connects with password authentication. Agent/key lookup is disabled.
5. Calls the trust callback for unknown keys and saves accepted keys.
6. Opens an SFTP channel as a capability check.

SSH connect, banner, and authentication timeouts are eight seconds. Proxy startup currently uses a fixed 0.7-second delay rather than readiness polling. Proxy output is discarded. A occupied port is tested by attempting SSH; process ownership is not inspected.

`Connection.sftp()` checks transport activity before opening a new channel. `close()` closes the client and terminates only the proxy process owned by the app, escalating to kill after a two-second wait.

Diagnostics explicitly cover missing iproxy, incorrect credentials, changed host keys, and an unavailable SFTP subsystem. USB absence, an unavailable SSH server, and a conflicting listener share a combined connection-error message; device detection is not implemented.

## Persistence

Default state directory:

```text
~/Library/Application Support/NeonBridge/
  settings.json
  known_hosts
```

`Store` creates the directory, loads JSON, and falls back to an empty dictionary on read/parse errors. Save writes a sibling temporary file and replaces `settings.json`. There is no migration/version field, schema validation, multi-process lock, retention limit, or settings backup.

Settings keys currently used:

- `destination`: last successfully browsed Mac directory.
- `remote_favorites`, `local_favorites`: lists of path strings.
- `browser_sizes`: two vertical splitter sizes, saved on exit.
- `history`: completed download records.

Example download record:

```json
{
  "remote": "/private/var/mobile/Media/DCIM/100APPLE/IMG_0961.MOV",
  "local": "/Users/example/Downloads/IMG_0961.MOV",
  "size": 1048576,
  "sha256": "<64-character hex digest, or null when not verified>",
  "date": "2026-10-07 21:00:00",
  "deleted": true
}
```

`deleted` is optional and currently updated by automatic post-transfer deletion. The separate history-based delete action does not update this flag. Date strings use the Mac's local time without an offset. History has no stable ID or device identity.

Optional passwords use macOS Keychain through keyring, service `NeonBridge`, account equal to `root` or `mobile`. Passwords are not saved in settings. Device-specific credentials and a forget-password interface are not implemented.

## Browsing, layout, and search

Both panes use `QTreeWidget` and `FileItem`. Column sort values are stored in `Qt.UserRole + 1`: casefolded name, byte size (directories use -1), and numeric modification timestamp. Equal values use the name as a tiebreaker. `Qt.UserRole` on browser rows contains `(path, directory)`; history rows contain a record dictionary.

Population temporarily disables sorting to build rows, then restores sorting. Folder-first ordering is only the initial input ordering; sorting by a column can intermix folders and files. The selected sort survives refresh within the session but is not persisted on exit.

A vertical splitter divides the browsers/options/search from history/progress/log. Expand hides the lower panel and Restore restores its previous sizes. A separate horizontal splitter divides phone and Mac panes. The log retains up to 500 blocks.

Search starts from the entered phone path and recursively visits directories using `listdir_attr`. It filters regular files using `VIDEO`, minimum bytes, and an optional modification-date lower bound. Inaccessible directories are counted and skipped. Symlinks are not traversed. Search results contain full paths and replace the phone list; the current browsing directory remains the paste destination. Searches are not indexed and can be expensive on large directory trees.

## Download contract

`download(sftp, remote, destination, verify, cancel, progress)` returns a completed history record or raises. It does not remove originals.

1. Creates destination parent directories.
2. Reads source size and modification time.
3. Checks a pre-existing partial file against its metadata and rejects an oversized or mismatched partial.
4. Checks available Mac space for remaining bytes.
5. Appends source data from the partial-file offset using 256 KiB chunks.
6. Rechecks source size/mtime and local size.
7. Optionally hashes the local partial and rereads the full phone file to compare SHA-256, then rechecks source attributes.
8. Replaces the final destination with the completed partial and removes metadata.

Partial files are named `<destination>.partial` and `<destination>.partial.json`. Metadata contains `remote`, `size`, and `mtime`. It does not include device identity or a hash of the already-downloaded prefix. Without verification, resume relies on those attributes and final size. Preserve SHA-256 verification for high-confidence transfers.

Progress is `(current_bytes, total_bytes, bytes_per_second)`. Speed -1 means verification. The UI displays per-file progress and batch position, not a byte-weighted overall progress bar.

`Window.transfer()` collects regular files recursively, skips symlinks, and handles existing completed files with Skip/Rename/Overwrite. Files transfer sequentially. Retry replays the last in-memory batch; pending work is not persisted. History records each completed download even if a later delete fails. Resuming a renamed download may select another filename if collision conditions changed.

## Deletion contracts

### Verified post-transfer deletion

`delete_verified_original(sftp, record, cancel)` requires a recorded SHA-256 digest. It freshly hashes the local copy, hashes the remote original, compares both to the digest, rechecks remote size/mtime, observes cancellation, and removes the phone file.

Automatic cleanup forces verification and asks for batch confirmation. It runs only after `download()` returns successfully. Delete failure retains the completed local copy and reports an error. The standalone history deletion action calls the same helper after confirmation.

These checks reduce accidental data loss but do not make deletion transactional. A remote file can change between the final check and remove. There is no recycle bin, remote snapshot, filesystem locking, or fsync-based durability guarantee for the local copy.

### Direct right-click deletion

`remove_remote()` deletes selected items recursively after UI confirmation. It uses `lstat`; a symbolic link is unlinked without visiting its target. Relative paths and the normalized filesystem root are rejected. Cancellation stops subsequent work but cannot roll back completed deletions. This action intentionally does not require a transferred copy.

## Remote copy, cut, paste, and rename

The clipboard holds `(paths, cut_flag, SSH_client_instance)` and is valid only for that connection. `selected_remote()` removes duplicates and descendants when a parent is selected. Paste targets the currently browsed directory, not a selected folder row. Name conflicts receive a `(copy N)` suffix; cut into the same existing path is a no-op.

- **Copy:** `copy_remote()` uses SFTP streams through the Mac with no local disk staging. Regular files use uniquely named remote temporary files, source size/mtime checks, and standard SFTP rename to commit. Copy does not hash content or preserve ownership/mode/timestamps.
- **Folders:** created recursively. Cancellation/failure can leave completed descendants. Single-file temporary cleanup is attempted, but can itself fail after a disconnect.
- **Symlinks:** copy recreates the link instead of traversing it. Normalization can reject dangling links or links whose targets are inaccessible.
- **Cut:** standard SFTP rename. There is no cross-filesystem copy/delete fallback. Paste can partially complete before an error; the clipboard is retained on failure.
- **Rename:** validates a single basename and rejects an existing destination.
- **New Folder:** validates a single name and uses SFTP mkdir.

SFTP helpers receive paths directly. Do not introduce shell-interpolated path commands. Never replace standard rename with overwrite-capable `posix_rename` without an explicit overwrite policy.

## Validation and release workflow

Run the commands in [readme.md](readme.md). Automated tests use doubles; no real SSH or device is required. GUI checks performed during development covered construction/rendering, GUI-thread callbacks, browser expansion, checksum-checkbox coupling, numeric/date sorting, refresh ordering, context-menu opening, and nested selection. These GUI checks were one-off scripts, not committed automated tests.

For repeatable GUI tests, use `QT_QPA_PLATFORM=offscreen` and temporarily redirect `neonbridge.backend.STATE` to a test directory **before** constructing `Window`. Do not write developer tests into the user's real settings or Keychain.

Before a release, validate against a phone:

- Owned and pre-existing iproxy connections; disconnect and reconnect.
- Authentication failure, changed fingerprint, unavailable SFTP, port conflict.
- Large downloads, low disk space, duplicates, unplug/resume, checksum mismatch.
- Explicit and automatic deletion, cancellation, and permission-denied cases.
- Copy/cut/rename/delete for folders, symlinks, Unicode names, and cross-filesystem moves.
- Packaged launch from Finder, separate iproxy discovery, and target architecture.

The user reported initial connection/transfer behavior working. This is not comprehensive hardware validation of later filesystem actions.

Rebuild the bundle using the command in the readme. No rebuild is needed for Markdown-only edits. Distributed releases will need signing/notarization and a deliberate iproxy dependency strategy.

## Suggested next development work

Prioritize committed GUI/integration tests, explicit device profiles, readiness polling, bounded operation timeouts, and persistent transfer-job IDs. Then consider uploads, Finder integration, transfer-queue persistence, richer collision handling, metadata-preserving copy, thumbnails, and drag-and-drop. Keep new work separate from the documented current behavior.
