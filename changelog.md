# Changelog

Changes are grouped by implementation milestone. All milestones below were developed on 2026-10-07. No formal release version or Git tag has been assigned; this is a development history, not a claim of published releases.

## Unreleased

### Developer documentation

- Replaced the incremental readme with setup, usage, build, troubleshooting, and current-limit instructions.
- Added architecture and implementation documentation, including state formats, operation contracts, threading, validation, and known limitations.
- Added this changelog for future development.

### iPhone context menu — 2026-10-07

- Added right-click Download, Copy, Cut, Paste, Rename, Delete, New Folder, Copy Path, Properties, and Refresh.
- Added phone-browser keyboard shortcuts and multi-selection handling.
- Added an internal, connection-scoped clipboard for phone-to-phone file operations.
- Added recursive SFTP copy and deletion; symlinks are not traversed by deletion.
- Preserved existing paste destinations using generated copy names.
- Added temporary-file staging for individual remote copies and cancellation checks.
- Blocked copies/moves into a source folder's descendants and direct deletion of the filesystem root.
- Bounded the activity log and throttled remote-copy progress updates.
- Added seven remote-filesystem tests; the combined suite has 15 passing tests.
- Rebuilt the macOS app bundle.

### File sorting — 2026-10-07

- Enabled clickable Name, Size, and Modified sorting in both browser panes.
- Sorted sizes by byte counts and dates by numeric timestamps rather than display text.
- Preserved the active column sort across refreshes within a session.
- Checked ascending/descending sorting, rounded display-size ties, and refresh behavior.
- Rebuilt the macOS app bundle.

### Browser expansion and automatic cleanup — 2026-10-07

- Added a vertical splitter so the browser/transfer-panel space can be adjusted.
- Added Expand Browsers and Restore Transfer Panel controls.
- Saved browser splitter sizes on exit.
- Added optional deletion of phone originals after successful verified transfers.
- Forced checksum verification when automatic cleanup is selected.
- Added confirmation before cleanup batches and retries.
- Rechecked the local copy and phone original before deletion and marked automatic deletions in history.
- Preserved completed downloads when subsequent deletion fails.
- Added five verified-deletion tests; the combined suite at this milestone had eight passing tests.
- Checked browser expansion and verification-option coupling; rebuilt the app.

### Initial working app — 2026-10-07

- Created the Python/PySide6 macOS app and dark cyan interface.
- Integrated `iproxy 2222 22` and Paramiko SSH/SFTP with root/mobile account selection.
- Reused existing port listeners and managed only app-owned proxy processes.
- Added SSH host-fingerprint trust and optional Keychain credentials.
- Added phone/Mac file panes, directory navigation, favorites, and hidden/video filters.
- Added recursive video discovery with minimum size and modification-date filters.
- Added sequential downloads, disk-space checks, partial-file resume metadata, collision policies, cancellation, and retry.
- Added optional SHA-256 verification, transfer history, Finder reveal, and confirmed history-based deletion.
- Added three download tests and checked initial UI construction and worker callbacks.
- Packaged and launched the Apple Silicon macOS app.
- User reported that the initial app appeared to work; exhaustive device validation remains outstanding.
