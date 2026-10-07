import errno
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from neonbridge.backend import copy_remote, remove_remote, remote_name, Cancelled

class LocalSFTP:
    def __init__(self, root): self.root = Path(root).resolve()
    def path(self, path): return self.root / path.lstrip('/')
    def normalize(self, path): return '/' + str(self.path(path).resolve().relative_to(self.root))
    def lstat(self, path): return self.path(path).lstat()
    def listdir_attr(self, path): return [SimpleNamespace(filename=child.name) for child in self.path(path).iterdir()]
    def mkdir(self, path): self.path(path).mkdir()
    def rmdir(self, path): self.path(path).rmdir()
    def remove(self, path): self.path(path).unlink()
    def readlink(self, path): return os.readlink(self.path(path))
    def symlink(self, source, target): self.path(target).symlink_to(source)
    def open(self, path, mode): return self.path(path).open('xb' if mode == 'wx' else mode)
    def rename(self, source, target):
        if self.path(target).exists(): raise FileExistsError(target)
        self.path(source).rename(self.path(target))

class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.sftp = LocalSFTP(self.temp.name); self.root = Path(self.temp.name)
        (self.root/'source').mkdir(); (self.root/'destination').mkdir()
        (self.root/'source'/'video.mov').write_bytes(b'video'*100)
        self.cancel = threading.Event()
    def test_recursive_copy(self):
        copy_remote(self.sftp, '/source', '/destination/source', self.cancel)
        self.assertEqual((self.root/'destination/source/video.mov').read_bytes(), b'video'*100)
        self.assertTrue((self.root/'source/video.mov').exists())
    def test_collision_preserves_existing(self):
        (self.root/'destination/video.mov').write_bytes(b'existing')
        with self.assertRaises(FileExistsError): copy_remote(self.sftp, '/source/video.mov', '/destination/video.mov', self.cancel)
        self.assertEqual((self.root/'destination/video.mov').read_bytes(), b'existing')
        self.assertTrue((self.root/'source/video.mov').exists())
    def test_descendant_copy_blocked(self):
        with self.assertRaises(ValueError): copy_remote(self.sftp, '/source', '/source/nested', self.cancel)
        self.assertFalse((self.root/'source/nested').exists())
    def test_delete_symlink_preserves_target(self):
        (self.root/'destination/link').symlink_to(self.root/'source', target_is_directory=True)
        remove_remote(self.sftp, '/destination', self.cancel)
        self.assertTrue((self.root/'source/video.mov').exists())
        self.assertFalse((self.root/'destination').exists())
    def test_root_delete_blocked(self):
        with self.assertRaises(ValueError): remove_remote(self.sftp, '/', self.cancel)
    def test_cancel_copy_cleans_temporary(self):
        def progress(text): self.cancel.set()
        with self.assertRaises(Cancelled): copy_remote(self.sftp, '/source/video.mov', '/destination/video.mov', self.cancel, progress)
        self.assertEqual(list((self.root/'destination').iterdir()), [])
    def test_invalid_names(self):
        for name in ['', '.', '..', 'folder/file', '\x00']:
            with self.assertRaises(ValueError): remote_name(name)
