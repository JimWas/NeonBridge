from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import paramiko

STATE = Path.home() / 'Library/Application Support/NeonBridge'
VIDEO = {'.mov', '.mp4', '.m4v', '.mkv', '.avi', '.webm'}

class Cancelled(Exception):
    pass

class Store:
    def __init__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.path = STATE / 'settings.json'
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2))
        temporary.replace(self.path)

class Connection:
    def __init__(self):
        self.proxy = None
        self.client = None
        self.lock = threading.RLock()
    def connect(self, user, password, trust):
        with self.lock:
            self.close()
            try:
                with socket.create_connection(('127.0.0.1', 2222), timeout=.5):
                    pass
            except OSError:
                executable = shutil.which('iproxy') or next((p for p in ['/opt/homebrew/bin/iproxy', '/usr/local/bin/iproxy'] if Path(p).is_file()), None)
                if not executable:
                    raise RuntimeError('iproxy missing. Install libusbmuxd with Homebrew, then reconnect.')
                self.proxy = subprocess.Popen([executable, '2222', '22'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(.7)
            client = paramiko.SSHClient()
            hosts = STATE / 'known_hosts'
            hosts.touch(exist_ok=True)
            client.load_host_keys(str(hosts))
            class Trust(paramiko.MissingHostKeyPolicy):
                def missing_host_key(self, ssh, hostname, key):
                    import base64
                    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                    if not trust(fingerprint):
                        raise RuntimeError('Connection cancelled: host fingerprint was not accepted.')
                    ssh.get_host_keys().add(hostname, key.get_name(), key)
                    ssh.save_host_keys(str(hosts))
            client.set_missing_host_key_policy(Trust())
            try:
                client.connect('127.0.0.1', port=2222, username=user, password=password,
                               look_for_keys=False, allow_agent=False, timeout=8, banner_timeout=8, auth_timeout=8)
                try:
                    with client.open_sftp():
                        pass
                except Exception as exc:
                    raise RuntimeError('SFTP unavailable: SSH authenticated, but the iPhone SFTP subsystem could not open. ' + str(exc)) from exc
            except paramiko.AuthenticationException as exc:
                client.close()
                self.close()
                raise RuntimeError('Incorrect username or password.') from exc
            except paramiko.BadHostKeyException as exc:
                client.close()
                self.close()
                raise RuntimeError('SSH host key changed. Check which iPhone is connected; saved trust was not replaced.') from exc
            except RuntimeError:
                client.close()
                self.close()
                raise
            except (OSError, EOFError, paramiko.SSHException) as exc:
                client.close()
                self.close()
                raise RuntimeError('Phone disconnected, SSH server unavailable, or port 2222 belongs to another service. Check the USB cable, unlock/trust the phone, and confirm SSH is running. Details: ' + str(exc)) from exc
            self.client = client
    def sftp(self):
        if not self.client or not self.client.get_transport() or not self.client.get_transport().is_active():
            raise RuntimeError('Phone disconnected. Reconnect, then retry the transfer.')
        return self.client.open_sftp()
    def close(self):
        if self.client:
            self.client.close()
            self.client = None
        if self.proxy:
            self.proxy.terminate()
            try:
                self.proxy.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proxy.kill()
                self.proxy.wait()
            self.proxy = None

def download(sftp, remote, destination, verify, cancel, progress):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.partial')
    metadata = partial.with_name(partial.name + '.json')
    info = sftp.stat(remote)
    identity = {'remote': remote, 'size': info.st_size, 'mtime': info.st_mtime}
    if partial.exists():
        try:
            previous = json.loads(metadata.read_text())
        except (OSError, ValueError):
            previous = None
        if previous != identity or partial.stat().st_size > info.st_size:
            raise RuntimeError('Partial file does not match this source. Rename or remove the partial file before retrying.')
    offset = partial.stat().st_size if partial.exists() else 0
    if shutil.disk_usage(destination.parent).free < info.st_size - offset:
        raise RuntimeError('Not enough free disk space on your Mac.')
    metadata.write_text(json.dumps(identity))
    started = time.monotonic()
    with sftp.open(remote, 'rb') as source, partial.open('ab') as target:
        source.seek(offset)
        current = offset
        while True:
            if cancel.is_set():
                raise Cancelled('Cancelled; partial download retained for resume.')
            chunk = source.read(1024 * 256)
            if not chunk:
                break
            target.write(chunk)
            current += len(chunk)
            progress(current, info.st_size, (current-offset)/max(.001, time.monotonic()-started))
    after = sftp.stat(remote)
    if after.st_size != info.st_size or after.st_mtime != info.st_mtime or partial.stat().st_size != info.st_size:
        raise RuntimeError('Source changed or download size mismatch; partial retained.')
    digest = None
    if verify:
        progress(info.st_size, info.st_size, -1)
        local_hash, remote_hash = hashlib.sha256(), hashlib.sha256()
        with partial.open('rb') as local, sftp.open(remote, 'rb') as source:
            for stream, hasher in [(local, local_hash), (source, remote_hash)]:
                while chunk := stream.read(1024 * 256):
                    if cancel.is_set():
                        raise Cancelled('Verification cancelled; partial retained.')
                    hasher.update(chunk)
        final = sftp.stat(remote)
        if local_hash.digest() != remote_hash.digest() or final.st_size != info.st_size or final.st_mtime != info.st_mtime:
            raise RuntimeError('Checksum mismatch or source changed; partial retained.')
        digest = local_hash.hexdigest()
    os.replace(partial, destination)
    metadata.unlink(missing_ok=True)
    return {'remote': remote, 'local': str(destination), 'size': info.st_size, 'sha256': digest,
            'date': time.strftime('%Y-%m-%d %H:%M:%S')}

def delete_verified_original(sftp, record, cancel):
    """Delete only after freshly verifying the durable local copy and remote source."""
    expected = record.get('sha256')
    if not expected:
        raise RuntimeError('Checksum verification is required before deleting an original.')
    def checksum(stream):
        result = hashlib.sha256()
        while True:
            if cancel.is_set():
                raise Cancelled('Deletion cancelled; original retained.')
            chunk = stream.read(262144)
            if not chunk:
                return result.hexdigest()
            result.update(chunk)
    with open(record['local'], 'rb') as local:
        if checksum(local) != expected:
            raise RuntimeError('Local copy changed; deletion blocked.')
    before = sftp.stat(record['remote'])
    with sftp.open(record['remote'], 'rb') as remote:
        if checksum(remote) != expected:
            raise RuntimeError('Remote original changed; deletion blocked.')
    after = sftp.stat(record['remote'])
    if before.st_size != after.st_size or before.st_mtime != after.st_mtime:
        raise RuntimeError('Remote original changed during verification; deletion blocked.')
    if cancel.is_set():
        raise Cancelled('Deletion cancelled; original retained.')
    sftp.remove(record['remote'])

# Remote file operations use SFTP paths directly, never shell command strings.
def remote_name(name):
    if not name or name in {'.', '..'} or '/' in name or '\x00' in name:
        raise ValueError('Enter a single filename without /, . or ...')
    return name

def remote_exists(sftp, path):
    import errno
    try:
        sftp.lstat(path)
        return True
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return False
        raise

def remove_remote(sftp, path, cancel, progress=lambda text: None):
    import stat
    from pathlib import PurePosixPath
    import posixpath
    path = posixpath.normpath(path)
    if not path.startswith('/') or path == '/':
        raise ValueError('Refusing to delete the filesystem root or a relative path.')
    if cancel.is_set():
        raise Cancelled('Operation cancelled. Completed deletions cannot be undone.')
    info = sftp.lstat(path)
    if stat.S_ISDIR(info.st_mode):
        for entry in sftp.listdir_attr(path):
            remove_remote(sftp, str(PurePosixPath(path)/entry.filename), cancel, progress)
        sftp.rmdir(path)
    else:
        sftp.remove(path)  # Deletes symlinks themselves, never their targets.
    progress('DELETED / ' + path)

def copy_remote(sftp, source, destination, cancel, progress=lambda text: None):
    import stat
    import uuid
    from pathlib import PurePosixPath
    if cancel.is_set():
        raise Cancelled('Copy cancelled.')
    parent = sftp.normalize(str(PurePosixPath(destination).parent))
    destination = str(PurePosixPath(parent)/PurePosixPath(destination).name)
    source_canonical = sftp.normalize(source)
    if destination == source_canonical or destination.startswith(source_canonical.rstrip('/') + '/'):
        raise ValueError('Cannot copy a folder into itself or one of its descendants.')
    if remote_exists(sftp, destination):
        raise FileExistsError('Destination already exists: ' + destination)
    info = sftp.lstat(source)
    if stat.S_ISLNK(info.st_mode):
        sftp.symlink(sftp.readlink(source), destination)
    elif stat.S_ISDIR(info.st_mode):
        sftp.mkdir(destination)
        for entry in sftp.listdir_attr(source):
            copy_remote(sftp, str(PurePosixPath(source)/entry.filename), str(PurePosixPath(destination)/entry.filename), cancel, progress)
    elif stat.S_ISREG(info.st_mode):
        temporary = str(PurePosixPath(parent)/('.neonbridge-' + uuid.uuid4().hex + '.partial'))
        try:
            with sftp.open(source, 'rb') as incoming, sftp.open(temporary, 'wx') as outgoing:
                copied = 0
                last_update = 0
                while True:
                    if cancel.is_set():
                        raise Cancelled('Copy cancelled.')
                    chunk = incoming.read(262144)
                    if not chunk:
                        break
                    outgoing.write(chunk)
                    copied += len(chunk)
                    if time.monotonic() - last_update >= .2:
                        progress(f'COPY / {source} / {copied / 1048576:.1f} MB')
                        last_update = time.monotonic()
            after = sftp.lstat(source)
            if copied != info.st_size or after.st_size != info.st_size or after.st_mtime != info.st_mtime:
                raise RuntimeError('Source changed during copy; destination was not committed.')
            sftp.rename(temporary, destination)
        finally:
            if remote_exists(sftp, temporary):
                sftp.remove(temporary)
    else:
        raise ValueError('Only regular files, folders, and symbolic links can be copied.')
    progress('COPIED / ' + destination)
