import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from neonbridge.backend import download, Cancelled

class SFTP:
    def __init__(self, data): self.data = data
    def stat(self, path): return SimpleNamespace(st_size=len(self.data), st_mtime=123)
    def open(self, path, mode): return io.BytesIO(self.data)

class TransferTests(unittest.TestCase):
    def test_verified_download(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'video.mov'
            record = download(SFTP(b'video'*100), '/video.mov', target, True, threading.Event(), lambda *args: None)
            self.assertEqual(target.read_bytes(), b'video'*100)
            self.assertEqual(len(record['sha256']), 64)
            self.assertFalse(Path(str(target)+'.partial').exists())
    def test_cancel_then_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'video.mov'
            source = SFTP(b'x'*700000)
            cancel = threading.Event()
            with self.assertRaises(Cancelled):
                download(source, '/video.mov', target, True, cancel, lambda *args: cancel.set())
            self.assertFalse(target.exists())
            self.assertGreater(Path(str(target)+'.partial').stat().st_size, 0)
            download(source, '/video.mov', target, True, threading.Event(), lambda *args: None)
            self.assertEqual(target.read_bytes(), source.data)
    def test_changed_source_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'video.mov'
            Path(str(target)+'.partial').write_bytes(b'old')
            Path(str(target)+'.partial.json').write_text(json.dumps({'remote':'/video.mov','size':3,'mtime':1}))
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                download(SFTP(b'new'), '/video.mov', target, False, threading.Event(), lambda *args: None)
            self.assertFalse(target.exists())

if __name__ == '__main__': unittest.main()

class DeleteTests(unittest.TestCase):
    def check_delete(self, local=b'video', remote=b'video', verified=True, cancelled=False):
        import hashlib
        from neonbridge.backend import delete_verified_original
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'video.mov'; target.write_bytes(local)
            sftp = SFTP(remote); removed = []
            sftp.remove = removed.append
            record = {'remote':'/video.mov', 'local':str(target), 'sha256':hashlib.sha256(b'video').hexdigest() if verified else None}
            cancel = threading.Event()
            if cancelled: cancel.set()
            if local == remote == b'video' and verified and not cancelled:
                delete_verified_original(sftp, record, cancel)
                self.assertEqual(removed, ['/video.mov'])
            else:
                with self.assertRaises((RuntimeError, Cancelled)):
                    delete_verified_original(sftp, record, cancel)
                self.assertEqual(removed, [])
            self.assertTrue(target.exists())
    def test_delete_verified_copy(self): self.check_delete()
    def test_changed_local_retained(self): self.check_delete(local=b'changed')
    def test_changed_remote_retained(self): self.check_delete(remote=b'changed')
    def test_unverified_retained(self): self.check_delete(verified=False)
    def test_cancelled_retained(self): self.check_delete(cancelled=True)
