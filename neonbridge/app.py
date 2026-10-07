from __future__ import annotations
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import threading
import time
import subprocess
import keyring
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, Qt, QDate
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QMessageBox, QSplitter, QProgressBar, QPlainTextEdit, QInputDialog,
    QDateEdit, QSpinBox, QMenu)
from .backend import Connection, Store, VIDEO, download, Cancelled, delete_verified_original, remote_name, remote_exists, remove_remote, copy_remote

class FileItem(QTreeWidgetItem):
    """Sort display labels using their underlying bytes and timestamps."""
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        left = self.data(column, Qt.UserRole + 1)
        right = other.data(column, Qt.UserRole + 1)
        if left == right:
            return self.data(0, Qt.UserRole + 1) < other.data(0, Qt.UserRole + 1)
        return left < right

class Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(object)

class Job(QRunnable):
    def __init__(self, work):
        super().__init__()
        self.work = work
        self.signals = Signals()
    def run(self):
        try:
            self.signals.done.emit(self.work(self.signals.progress.emit))
        except Exception as exc:
            self.signals.error.emit(str(exc))

class Window(QMainWindow):
    trust_request = Signal(str, object)
    def __init__(self):
        super().__init__()
        self.store, self.connection = Store(), Connection()
        self.pool = QThreadPool(self)
        self.jobs = set()
        self.cancel = threading.Event()
        self.search_cancel = threading.Event()
        self.busy = False
        self.remote = '/var/mobile/Media'
        self.setWindowTitle('NEONBRIDGE // USB FILE EXPLORER')
        self.resize(1280, 850)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel('NEONBRIDGE   /   PERSONAL DATA UPLINK')
        title.setObjectName('title')
        layout.addWidget(title)
        self.status = QLabel('USB LINK  /  OFFLINE')
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.user = QComboBox(); self.user.addItems(['root', 'mobile'])
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password); self.password.setPlaceholderText('SSH password (e.g. alpine)')
        self.remember = QCheckBox('Remember in Keychain')
        self.connect_button = QPushButton('CONNECT'); self.connect_button.clicked.connect(self.connect_phone)
        row.addWidget(QLabel('127.0.0.1 : 2222 → iPhone : 22')); row.addWidget(self.user); row.addWidget(self.password); row.addWidget(self.remember); row.addWidget(self.connect_button)
        layout.addLayout(row)
        browser_controls = QHBoxLayout()
        browser_controls.addWidget(QLabel('Drag the divider below the browsers to resize them'))
        self.expand_button = QPushButton('EXPAND BROWSERS')
        self.expand_button.setCheckable(True)
        self.expand_button.toggled.connect(self.expand_browsers)
        browser_controls.addWidget(self.expand_button)
        layout.addLayout(browser_controls)
        self.browser_split = QSplitter(Qt.Vertical)
        self.browser_split.setHandleWidth(10)
        self.browser_split.setChildrenCollapsible(False)
        browser_area = QWidget()
        browser_layout = QVBoxLayout(browser_area)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter()
        self.remote_tree, self.local_tree = self.tree(), self.tree()
        self.remote_clipboard = None
        self.remote_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_tree.customContextMenuRequested.connect(self.remote_context_menu)
        self.remote_actions = {}
        for name, shortcut, callback in [
            ('Copy', QKeySequence.Copy, lambda: self.clipboard_remote(False)),
            ('Cut', QKeySequence.Cut, lambda: self.clipboard_remote(True)),
            ('Paste', QKeySequence.Paste, self.paste_remote),
            ('Rename…', 'F2', self.rename_remote),
            ('Delete…', 'Backspace', self.delete_remote),
        ]:
            action = QAction(name, self.remote_tree)
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            action.triggered.connect(callback)
            self.remote_tree.addAction(action)
            self.remote_actions[name] = action
        self.remote_path = QLineEdit(self.remote)
        self.local_path = QLineEdit(self.store.data.get('destination', str(Path.home() / 'Downloads')))
        for is_remote, tree, path in [(True, self.remote_tree, self.remote_path), (False, self.local_tree, self.local_path)]:
            panel = QWidget(); box = QVBoxLayout(panel)
            box.addWidget(QLabel('IPHONE / REMOTE FILESYSTEM' if is_remote else 'MAC / DOWNLOAD DESTINATION'))
            box.addWidget(path)
            controls = QHBoxLayout()
            for label, callback in [('↑ UP', lambda checked=False, r=is_remote: self.up(r)), ('REFRESH', lambda checked=False, r=is_remote: self.browse_remote() if r else self.browse_local()), ('SAVE LOCATION', lambda checked=False, r=is_remote: self.save_location(r))]:
                button = QPushButton(label); button.clicked.connect(callback); controls.addWidget(button)
            favorites = QComboBox(); favorites.addItem('Saved locations…', None)
            defaults = ['/var/mobile/Media', '/var/mobile/Media/DCIM'] if is_remote else []
            for value in dict.fromkeys(defaults + self.store.data.get('remote_favorites' if is_remote else 'local_favorites', [])):
                favorites.addItem(value, value)
            favorites.activated.connect(lambda index, r=is_remote, f=favorites: self.favorite(r, f.itemData(index)))
            self.remote_favorites = favorites if is_remote else getattr(self, 'remote_favorites', None)
            self.local_favorites = favorites if not is_remote else getattr(self, 'local_favorites', None)
            box.addLayout(controls); box.addWidget(favorites); box.addWidget(tree, 1)
            splitter.addWidget(panel)
            path.returnPressed.connect(self.browse_remote if is_remote else self.browse_local)
            tree.itemDoubleClicked.connect(lambda item, col, r=is_remote: self.enter(r, item))
        browser_layout.addWidget(splitter, 1)
        options = QHBoxLayout()
        self.hidden = QCheckBox('Show hidden'); self.hidden.toggled.connect(self.refresh)
        self.videos = QCheckBox('Videos only'); self.videos.toggled.connect(self.refresh)
        self.verify = QCheckBox('Verify SHA-256 (reads source twice)'); self.verify.setChecked(True)
        self.delete_after = QCheckBox('Delete originals after verified transfer')
        self.delete_after.toggled.connect(self.require_verification)
        self.verify.toggled.connect(lambda checked: self.delete_after.setChecked(False) if not checked else None)
        choose = QPushButton('CHOOSE MAC FOLDER'); choose.clicked.connect(self.choose_folder)
        self.download_button = QPushButton('↓ DOWNLOAD SELECTED'); self.download_button.clicked.connect(self.start_download)
        for control in [self.hidden, self.videos, self.verify, choose, self.download_button]: options.addWidget(control)
        browser_layout.addLayout(options)
        browser_layout.addWidget(self.delete_after)
        search = QHBoxLayout()
        self.min_size = QSpinBox(); self.min_size.setRange(0, 1000000); self.min_size.setSuffix(' MB minimum')
        self.date_enabled = QCheckBox('Modified since')
        self.since = QDateEdit(QDate.currentDate().addMonths(-1)); self.since.setCalendarPopup(True)
        find = QPushButton('FIND VIDEOS IN CURRENT IPHONE FOLDER'); find.clicked.connect(self.find_videos)
        stop_search = QPushButton('STOP SEARCH'); stop_search.clicked.connect(self.search_cancel.set)
        for control in [self.min_size, self.date_enabled, self.since, find, stop_search]: search.addWidget(control)
        browser_layout.addLayout(search)
        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.queue = QTreeWidget(); self.queue.setHeaderLabels(['Transfer / history', 'State', 'Destination'])
        details_layout.addWidget(self.queue, 1)
        progress_row = QHBoxLayout()
        self.progress = QProgressBar(); self.progress.setRange(0, 1000)
        self.telemetry = QLabel('READY')
        cancel = QPushButton('CANCEL TRANSFER'); cancel.clicked.connect(self.cancel.set)
        retry = QPushButton('RETRY / RESUME'); retry.clicked.connect(self.retry)
        reveal = QPushButton('OPEN IN FINDER'); reveal.clicked.connect(self.reveal)
        delete = QPushButton('DELETE VERIFIED ORIGINAL'); delete.clicked.connect(self.delete_original)
        for control in [self.progress, self.telemetry, cancel, retry, reveal, delete]: progress_row.addWidget(control)
        details_layout.addLayout(progress_row)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500); self.log.setMaximumHeight(100)
        details_layout.addWidget(self.log)
        self.browser_split.addWidget(browser_area)
        self.browser_split.addWidget(self.details)
        self.browser_split.setStretchFactor(0, 4)
        self.browser_split.setStretchFactor(1, 1)
        self.browser_split.setSizes(self.store.data.get('browser_sizes', [650, 150]))
        layout.addWidget(self.browser_split, 1)
        self.trust_request.connect(self.confirm_trust)
        for record in self.store.data.get('history', []): self.history_item(record)
        self.browse_local()
        self.setStyleSheet('''
            QWidget { background:#0b1018; color:#cee1e8; font-family:"Menlo"; font-size:12px; }
            QMainWindow { background:#080d14; }
            QLabel#title { font-size:25px; font-weight:bold; color:#52ead7; padding:15px 0; }
            QPushButton { background:#132330; border:1px solid #285160; padding:9px; color:#68e9ef; border-radius:4px; }
            QPushButton:hover { background:#204453; border-color:#64efff; }
            QPushButton:disabled { color:#586b73; }
            QLineEdit,QComboBox,QSpinBox,QDateEdit { background:#111d29; border:1px solid #29414d; padding:7px; }
            QTreeWidget,QPlainTextEdit { background:#0c1620; border:1px solid #24414c; alternate-background-color:#101e28; }
            QTreeWidget::item { padding:6px; } QTreeWidget::item:selected { background:#20515d; }
            QHeaderView::section { background:#172935; color:#57d7d5; padding:8px; border:0; }
            QProgressBar { border:1px solid #285160; text-align:center; min-width:100px; }
            QProgressBar::chunk { background:#3bbaa0; } QSplitter::handle { background:#25434a; }
        ''')
    def expand_browsers(self, expanded):
        if expanded:
            self.normal_browser_sizes = self.browser_split.sizes()
        self.details.setVisible(not expanded)
        if not expanded:
            self.browser_split.setSizes(getattr(self, 'normal_browser_sizes', [650, 150]))
        self.expand_button.setText('RESTORE TRANSFER PANEL' if expanded else 'EXPAND BROWSERS')

    def require_verification(self, checked):
        if checked:
            self.verify.setChecked(True)

    def confirm_move(self):
        return QMessageBox.question(self, 'Transfer and delete originals?',
            'After each successful download, recheck both copies with SHA-256 and permanently delete that file from the iPhone.\n\nSkipped, failed, or cancelled files stay on the phone. Continue?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def tree(self):
        tree = QTreeWidget(); tree.setHeaderLabels(['Name', 'Size', 'Modified']); tree.setSelectionMode(QTreeWidget.ExtendedSelection); tree.setAlternatingRowColors(True)
        tree.header().setStretchLastSection(True); tree.setColumnWidth(0, 280)
        tree.setSortingEnabled(True)
        tree.sortItems(0, Qt.AscendingOrder)
        return tree
    def run(self, work, done, progress=None):
        job = Job(work); self.jobs.add(job)
        def complete(value):
            self.jobs.discard(job); done(value)
        def failed(message):
            self.jobs.discard(job); self.error(message)
        job.signals.done.connect(complete); job.signals.error.connect(failed)
        if progress: job.signals.progress.connect(progress)
        self.pool.start(job)
    def error(self, text):
        self.log.appendPlainText('ERROR / ' + text)
        QMessageBox.warning(self, 'NeonBridge', text)
    def confirm_trust(self, fingerprint, response):
        response['accepted'] = QMessageBox.question(self, 'Trust this iPhone?', 'SSH host fingerprint:\n' + fingerprint + '\n\nTrust and save this fingerprint?') == QMessageBox.Yes
        response['event'].set()
    def connect_phone(self):
        if self.busy: return
        if self.connection.client:
            self.connection.close(); self.status.setText('USB LINK / OFFLINE'); self.connect_button.setText('CONNECT'); return
        user, password = self.user.currentText(), self.password.text()
        if not password:
            try: password = keyring.get_password('NeonBridge', user) or ''
            except Exception: pass
        self.connect_button.setEnabled(False); self.status.setText('USB LINK / CONNECTING…')
        def trust(fingerprint):
            response = {'event': threading.Event(), 'accepted': False}
            self.trust_request.emit(fingerprint, response)
            response['event'].wait()
            return response['accepted']
        def work(emit):
            try:
                self.connection.connect(user, password, trust)
                return None
            except Exception as exc: return str(exc)
        def done(error):
            self.connect_button.setEnabled(True)
            if error: self.status.setText('USB LINK / CONNECTION FAILED'); self.error(error); return
            self.connect_button.setText('DISCONNECT'); self.status.setText('USB LINK / SSH CONNECTED / ' + user.upper())
            if self.remember.isChecked():
                try: keyring.set_password('NeonBridge', user, password)
                except Exception as exc: self.error('Connected, but Keychain save failed: ' + str(exc))
            self.password.clear(); self.browse_remote()
        self.run(work, done)
    def populate(self, tree, entries):
        tree.setSortingEnabled(False)
        tree.clear()
        for name, path, directory, size, modified in sorted(entries, key=lambda e: (not e[2], e[0].lower())):
            if not self.hidden.isChecked() and name.startswith('.'): continue
            if self.videos.isChecked() and not directory and Path(name).suffix.lower() not in VIDEO: continue
            item = FileItem([('▸ ' if directory else '') + name, '—' if directory else f'{size / 1048576:,.1f} MB', time.strftime('%Y-%m-%d %H:%M', time.localtime(modified))])
            item.setData(0, Qt.UserRole, (path, directory))
            item.setData(0, Qt.UserRole + 1, name.casefold())
            item.setData(1, Qt.UserRole + 1, -1 if directory else size)
            item.setData(2, Qt.UserRole + 1, modified)
            tree.addTopLevelItem(item)
        tree.setSortingEnabled(True)
    def browse_remote(self):
        path = self.remote_path.text()
        def work(emit):
            with self.connection.sftp() as sftp:
                canonical = sftp.normalize(path)
                return canonical, [(e.filename, str(PurePosixPath(canonical)/e.filename), stat.S_ISDIR(e.st_mode), e.st_size, e.st_mtime) for e in sftp.listdir_attr(canonical)]
        def done(result):
            self.remote = result[0]; self.remote_path.setText(self.remote); self.populate(self.remote_tree, result[1])
        self.run(work, done)
    def browse_local(self):
        try:
            path = Path(self.local_path.text()).expanduser().resolve()
            entries = []
            for child in path.iterdir():
                try:
                    info = child.stat(); entries.append((child.name, str(child), child.is_dir(), info.st_size, info.st_mtime))
                except OSError: continue
            self.local_path.setText(str(path)); self.populate(self.local_tree, entries)
            self.store.data['destination'] = str(path); self.store.save()
        except OSError as exc: self.error(str(exc))
    def refresh(self):
        self.browse_local()
        if self.connection.client: self.browse_remote()
    def enter(self, remote, item):
        path, directory = item.data(0, Qt.UserRole)
        if directory:
            (self.remote_path if remote else self.local_path).setText(path)
            self.browse_remote() if remote else self.browse_local()
    def up(self, remote):
        field = self.remote_path if remote else self.local_path
        field.setText(str(PurePosixPath(field.text()).parent) if remote else str(Path(field.text()).parent))
        self.browse_remote() if remote else self.browse_local()
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Download destination', self.local_path.text())
        if folder: self.local_path.setText(folder); self.browse_local()
    def save_location(self, remote):
        value = (self.remote_path if remote else self.local_path).text()
        key = 'remote_favorites' if remote else 'local_favorites'
        saved = self.store.data.setdefault(key, [])
        if value not in saved:
            saved.append(value); self.store.save(); (self.remote_favorites if remote else self.local_favorites).addItem(value, value)
    def favorite(self, remote, value):
        if value:
            (self.remote_path if remote else self.local_path).setText(value)
            self.browse_remote() if remote else self.browse_local()
    def selected_remote(self):
        paths = [item.data(0, Qt.UserRole)[0] for item in self.remote_tree.selectedItems()]
        # Selecting a parent already includes its descendants.
        return [path for path in dict.fromkeys(paths)
                if not any(path.startswith(parent.rstrip('/') + '/') for parent in paths if parent != path)]

    def remote_context_menu(self, position):
        item = self.remote_tree.itemAt(position)
        if item and not item.isSelected():
            self.remote_tree.clearSelection()
            item.setSelected(True)
        selected = self.selected_remote() if item else []
        ready = bool(self.connection.client) and not self.busy
        menu = QMenu(self)
        if item:
            action = menu.addAction('Download to Mac…', self.start_download)
            action.setEnabled(ready)
            menu.addSeparator()
        for label in ['Copy', 'Cut', 'Paste', 'Rename…', 'Delete…']:
            action = self.remote_actions[label]
            action.setEnabled(ready and (bool(self.remote_clipboard) if label == 'Paste' else len(selected) == 1 if label == 'Rename…' else bool(selected)))
            menu.addAction(action)
        menu.addSeparator()
        action = menu.addAction('New Folder…', self.new_remote_folder); action.setEnabled(ready)
        if item:
            menu.addAction('Copy Path', lambda: QApplication.clipboard().setText('\n'.join(selected)))
            action = menu.addAction('Properties…', self.remote_properties); action.setEnabled(ready)
        action = menu.addAction('Refresh', self.browse_remote); action.setEnabled(ready)
        menu.exec(self.remote_tree.viewport().mapToGlobal(position))
        # Keyboard shortcuts remain available after closing a menu on empty space.
        for action in self.remote_actions.values(): action.setEnabled(True)

    def remote_operation(self, work, completed=None):
        if self.busy or not self.connection.client: return
        self.busy = True
        self.cancel.clear()
        self.connect_button.setEnabled(False)
        self.download_button.setEnabled(False)
        def wrapped(emit):
            try:
                with self.connection.sftp() as sftp:
                    return work(sftp, emit), None
            except Exception as exc:
                return None, str(exc)
        def done(result):
            self.busy = False
            self.connect_button.setEnabled(True)
            self.download_button.setEnabled(True)
            if result[1]: self.error(result[1])
            elif completed: completed(result[0])
            self.telemetry.setText('READY' if not result[1] else 'OPERATION STOPPED')
            self.browse_remote()
        self.run(wrapped, done, lambda text: (self.telemetry.setText(text), self.log.appendPlainText(text)))

    def clipboard_remote(self, cut):
        if self.busy or not self.connection.client: return
        paths = self.selected_remote()
        if paths:
            self.remote_clipboard = (paths, cut, self.connection.client)
            self.log.appendPlainText(('CUT' if cut else 'COPY') + f' / {len(paths)} selected items; navigate to destination and Paste')

    def paste_remote(self):
        if self.busy or not self.remote_clipboard: return
        paths, cut, client = self.remote_clipboard
        if client is not self.connection.client:
            self.error('Clipboard belongs to a previous connection. Copy or cut the items again.'); return
        destination = self.remote
        def work(sftp, emit):
            failures = []
            for source in paths:
                if self.cancel.is_set(): raise Cancelled('Paste cancelled.')
                try:
                    target = str(PurePosixPath(destination)/PurePosixPath(source).name)
                    if remote_exists(sftp, target):
                        if cut and sftp.normalize(source) == sftp.normalize(target): continue
                        original = PurePosixPath(target); number = 1
                        while remote_exists(sftp, target):
                            target = str(original.with_name(original.stem + f' (copy {number})' + original.suffix)); number += 1
                    if cut:
                        target = str(PurePosixPath(sftp.normalize(str(PurePosixPath(target).parent)))/PurePosixPath(target).name)
                        canonical = sftp.normalize(source)
                        if target.startswith(canonical.rstrip('/') + '/'):
                            raise ValueError('Cannot move a folder into its own descendant.')
                        sftp.rename(source, target)
                        emit('MOVED / ' + target)
                    else:
                        copy_remote(sftp, source, target, self.cancel, emit)
                except Cancelled: raise
                except Exception as exc: failures.append(f'{source}: {exc}')
            if failures: raise RuntimeError('Some items could not be pasted:\n' + '\n'.join(failures))
        self.remote_operation(work, lambda value: setattr(self, 'remote_clipboard', None) if cut else None)

    def rename_remote(self):
        paths = self.selected_remote()
        if self.busy or len(paths) != 1: return
        source = paths[0]
        name, accepted = QInputDialog.getText(self, 'Rename iPhone item', 'New name:', text=PurePosixPath(source).name)
        if not accepted or name == PurePosixPath(source).name: return
        try: remote_name(name)
        except ValueError as exc: self.error(str(exc)); return
        target = str(PurePosixPath(source).parent/name)
        def work(sftp, emit):
            if remote_exists(sftp, target): raise FileExistsError('An item with that name already exists.')
            sftp.rename(source, target); emit('RENAMED / ' + target)
        self.remote_operation(work)

    def delete_remote(self):
        paths = self.selected_remote()
        if self.busy or not paths: return
        if QMessageBox.question(self, 'Permanently delete iPhone items?',
                'Delete these items and all contents of selected folders? This cannot be undone.\n\n' + '\n'.join(paths[:12]),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        def work(sftp, emit):
            failures = []
            for path in paths:
                try: remove_remote(sftp, path, self.cancel, emit)
                except Cancelled: raise
                except Exception as exc: failures.append(f'{path}: {exc}')
            if failures: raise RuntimeError('Some items could not be deleted:\n' + '\n'.join(failures))
        self.remote_operation(work)

    def new_remote_folder(self):
        if self.busy: return
        name, accepted = QInputDialog.getText(self, 'New iPhone folder', 'Folder name:')
        if not accepted: return
        try: remote_name(name)
        except ValueError as exc: self.error(str(exc)); return
        target = str(PurePosixPath(self.remote)/name)
        def work(sftp, emit):
            sftp.mkdir(target); emit('CREATED / ' + target)
        self.remote_operation(work)

    def remote_properties(self):
        paths = self.selected_remote()
        if self.busy or not paths: return
        def work(sftp, emit):
            lines = []
            for path in paths:
                info = sftp.lstat(path)
                lines.append(f'{path}\nSize: {info.st_size:,} bytes\nPermissions: {stat.filemode(info.st_mode)}\nUID / GID: {info.st_uid} / {info.st_gid}\nModified: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info.st_mtime))}')
            return '\n\n'.join(lines)
        self.remote_operation(work, lambda text: QMessageBox.information(self, 'iPhone item properties', text))

    def find_videos(self):
        folder = self.remote_path.text(); minimum = self.min_size.value()*1048576
        since = self.since.date().toPython() if self.date_enabled.isChecked() else None
        threshold = time.mktime(since.timetuple()) if since else 0
        self.search_cancel.clear(); self.log.appendPlainText('SEARCH / ' + folder)
        def work(emit):
            found, pending, skipped = [], [folder], []
            with self.connection.sftp() as sftp:
                while pending and not self.search_cancel.is_set():
                    path = pending.pop()
                    try: entries = sftp.listdir_attr(path)
                    except OSError: skipped.append(path); continue
                    for e in entries:
                        full = str(PurePosixPath(path)/e.filename)
                        if stat.S_ISDIR(e.st_mode): pending.append(full)
                        elif stat.S_ISREG(e.st_mode) and Path(e.filename).suffix.lower() in VIDEO and e.st_size >= minimum and e.st_mtime >= threshold:
                            found.append((full, full, False, e.st_size, e.st_mtime))
                    emit(f'Searched {path} / {len(found)} videos')
            return found, skipped
        def done(result):
            self.populate(self.remote_tree, result[0]); self.log.appendPlainText(f'SEARCH / {len(result[0])} videos; {len(result[1])} inaccessible folders' + (' / stopped' if self.search_cancel.is_set() else ''))
        self.run(work, done, self.telemetry.setText)
    def start_download(self):
        if self.busy: return
        selected = [i.data(0, Qt.UserRole) for i in self.remote_tree.selectedItems()]
        if not selected: return
        destination = self.local_path.text()
        # A single policy is chosen before worker execution; no dialogs from worker threads.
        choice, accepted = QInputDialog.getItem(self, 'Existing files', 'If a completed destination file exists:', ['Skip', 'Rename', 'Overwrite'], 0, False)
        if not accepted: return
        delete_after = self.delete_after.isChecked()
        if delete_after and not self.confirm_move(): return
        self.last_transfer = (selected, destination, self.verify.isChecked() or delete_after, choice, delete_after)
        self.transfer(*self.last_transfer)
    def retry(self):
        if not self.busy and hasattr(self, 'last_transfer'):
            if self.last_transfer[-1] and not self.confirm_move(): return
            self.transfer(*self.last_transfer)
    def transfer(self, selected, destination, verify, policy, delete_after=False):
        self.busy = True; self.cancel.clear(); self.connect_button.setEnabled(False); self.download_button.setEnabled(False)
        def work(emit):
            records, failures = [], []
            try:
                with self.connection.sftp() as sftp:
                    files = []
                    def collect(remote, local):
                        if self.cancel.is_set(): raise Cancelled('Cancelled')
                        info = sftp.lstat(remote)
                        if stat.S_ISDIR(info.st_mode):
                            for e in sftp.listdir_attr(remote): collect(str(PurePosixPath(remote)/e.filename), local/e.filename)
                        elif stat.S_ISREG(info.st_mode): files.append((remote, local))
                    for remote, directory in selected: collect(remote, Path(destination)/PurePosixPath(remote).name)
                    for index, (remote, local) in enumerate(files):
                        if self.cancel.is_set(): break
                        if local.exists():
                            if policy == 'Skip': emit(('log', f'SKIPPED / {local}')); continue
                            if policy == 'Rename':
                                number = 1
                                original = local
                                while local.exists():
                                    local = original.with_name(original.stem + f' ({number})' + original.suffix); number += 1
                        emit(('active', remote, str(local)))
                        emit(('log', f'DOWNLOAD / {index+1}/{len(files)} / {remote}'))
                        try:
                            record = download(sftp, remote, local, verify, self.cancel, lambda current, total, speed: emit(('progress', current, total, speed, index+1, len(files))))
                            records.append(record); emit(('record', record.copy()))
                            if delete_after:
                                emit(('log', 'RECHECK BEFORE DELETE / ' + remote))
                                try:
                                    delete_verified_original(sftp, record, self.cancel)
                                    record['deleted'] = True
                                    emit(('deleted', record.copy()))
                                except Exception as exc:
                                    failures.append(f'{remote}: download complete; original retained: {exc}')
                                    emit(('log', failures[-1]))
                                    if isinstance(exc, Cancelled): break
                        except Exception as exc:
                            failures.append(f'{remote}: {exc}'); emit(('log', failures[-1]))
                            if isinstance(exc, Cancelled): break
            except Exception as exc: failures.append(str(exc))
            return failures
        def progress(event):
            if event[0] == 'active':
                if hasattr(self, 'active_item'): self.active_item.setText(1, 'Interrupted / retry')
                self.active_item = QTreeWidgetItem([PurePosixPath(event[1]).name, 'Downloading…', event[2]])
                self.queue.insertTopLevelItem(0, self.active_item)
            elif event[0] == 'log': self.log.appendPlainText(event[1])
            elif event[0] == 'deleted':
                record = event[1]
                for saved in reversed(self.store.data.get('history', [])):
                    if saved['remote'] == record['remote'] and saved['local'] == record['local'] and saved['date'] == record['date']:
                        saved['deleted'] = True; break
                self.store.save()
                for index in range(self.queue.topLevelItemCount()):
                    item = self.queue.topLevelItem(index)
                    saved = item.data(0, Qt.UserRole)
                    if saved and saved == {k: v for k, v in record.items() if k != 'deleted'}:
                        item.setData(0, Qt.UserRole, record)
                        item.setText(1, 'Verified / original deleted'); break
                self.log.appendPlainText('DELETED / ' + record['remote'])
            elif event[0] == 'record':
                if hasattr(self, 'active_item'):
                    self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(self.active_item)); del self.active_item
                self.store.data.setdefault('history', []).append(event[1]); self.store.save(); self.history_item(event[1])
            else:
                _, current, total, speed, index, count = event
                self.progress.setValue(int(current/max(total, 1)*1000))
                self.telemetry.setText(f'{index}/{count} / VERIFYING' if speed < 0 else f'{index}/{count} / {speed/1048576:.1f} MB/s / ETA {(total-current)/max(speed,1):.0f}s')
        def done(failures):
            if hasattr(self, 'active_item'):
                self.active_item.setText(1, 'Interrupted / retry'); del self.active_item
            self.busy = False; self.connect_button.setEnabled(True); self.download_button.setEnabled(True)
            self.telemetry.setText('INTERRUPTED / RETRY TO RESUME' if failures or self.cancel.is_set() else 'QUEUE COMPLETE')
            self.browse_local()
            if delete_after: self.browse_remote()
            if failures: self.error('\n'.join(failures[:5]))
        self.run(work, done, progress)
    def history_item(self, record):
        item = QTreeWidgetItem([PurePosixPath(record['remote']).name, 'Verified / original deleted' if record.get('deleted') else ('SHA-256 verified' if record.get('sha256') else 'Size checked'), record['local']]); item.setData(0, Qt.UserRole, record); self.queue.insertTopLevelItem(0, item)
    def reveal(self):
        item = self.queue.currentItem()
        if item and item.data(0, Qt.UserRole): subprocess.Popen(['open', '-R', item.data(0, Qt.UserRole)['local']])
        else: subprocess.Popen(['open', self.local_path.text()])
    def delete_original(self):
        if self.busy: return
        item = self.queue.currentItem()
        if not item or not item.data(0, Qt.UserRole): return
        record = item.data(0, Qt.UserRole)
        if not record.get('sha256'): self.error('Checksum verification is required before deleting an original.'); return
        if QMessageBox.question(self, 'Delete iPhone original?', 'Permanently delete:\n' + record['remote'] + '\n\nThe local and remote files will be rechecked first.') != QMessageBox.Yes: return
        def work(emit):
            with self.connection.sftp() as sftp:
                delete_verified_original(sftp, record, threading.Event())
            return record['remote']
        self.run(work, lambda path: (self.log.appendPlainText('DELETED / ' + path), self.browse_remote()))
    def closeEvent(self, event):
        if self.jobs:
            self.cancel.set(); self.search_cancel.set()
            self.error('Background work is stopping. Wait for it to finish, then close the app again.'); event.ignore(); return
        self.store.data['browser_sizes'] = getattr(self, 'normal_browser_sizes', [650, 150]) if self.expand_button.isChecked() else self.browser_split.sizes()
        self.store.save()
        self.connection.close(); event.accept()

def main():
    app = QApplication(sys.argv); app.setApplicationName('NeonBridge')
    window = Window(); window.show(); sys.exit(app.exec())

if __name__ == '__main__': main()
