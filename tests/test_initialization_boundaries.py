import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from signal_library.diagnostics import inspect_root
from signal_library.initialization import CATALOG, InitializationError, initialize, write_new


class InitializationBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Books 日本語"
        self.repo = self.base / "repo"
        self.repo.mkdir()

    def init(self, resume=False):
        return initialize(str(self.root), True, resume, self.repo)

    def test_invalid_identity_timestamp_and_extra_fields(self):
        self.init()
        marker = self.root / "library.json"
        valid = json.loads(marker.read_text())
        for changes in ({"library_id": "invalid"}, {"created_at": "2026-10-06"},
                        {"created_at": "unknown"}, {"created_at": 10}, {"unexpected": True}):
            data = json.dumps({**valid, **changes})
            marker.write_text(data)
            self.assertEqual(inspect_root(self.root), "invalid_marker")
            with self.assertRaises(InitializationError):
                self.init()
            self.assertEqual(marker.read_text(), data)

    def test_duplicate_json_keys_and_directory_marker(self):
        self.root.mkdir()
        marker = self.root / "library.json"
        marker.write_text('{"schema_version":"signal-library-1","schema_version":"future"}')
        self.assertEqual(inspect_root(self.root), "invalid_marker")
        marker.unlink()
        marker.mkdir()
        self.assertEqual(inspect_root(self.root), "invalid_marker")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOはPOSIX専用")
    def test_fifo_marker_does_not_block(self):
        self.root.mkdir()
        os.mkfifo(self.root / "library.json")
        self.assertEqual(inspect_root(self.root), "invalid_marker")

    def test_catalog_validation_checks_all_rows(self):
        self.init()
        catalog = self.root / "catalog.csv"
        for value in (CATALOG + "too,few\n", CATALOG + '"unterminated\n'):
            catalog.write_text(value)
            self.assertEqual(inspect_root(self.root), "invalid_catalog")
            with self.assertRaises(InitializationError):
                self.init()
            self.assertEqual(catalog.read_text(), value)

    def test_legacy_detection_is_read_only(self):
        self.root.mkdir()
        (self.root / "books").mkdir()
        catalog = self.root / "catalog.csv"
        catalog.write_text(CATALOG)
        self.assertEqual(inspect_root(self.root), "legacy_unmanaged")
        with self.assertRaises(InitializationError):
            self.init()
        self.assertEqual(set(p.name for p in self.root.iterdir()), {"books", "catalog.csv"})

    def test_dangling_journal_and_locked_library_not_ready(self):
        self.init()
        lock = self.root / ".signal-library.lock"
        lock.write_text("")
        self.assertEqual(inspect_root(self.root), "locked")
        lock.unlink()
        journal = self.root / ".signal-library-init.json"
        try:
            journal.symlink_to(self.base / "absent")
        except OSError:
            self.skipTest("symlink作成権限なし")
        self.assertEqual(inspect_root(self.root), "initializing")
        with self.assertRaises(InitializationError):
            self.init(resume=True)

    def test_atomic_creation_never_overwrites_existing_target(self):
        target = self.base / "target"
        target.write_text("preserved")
        with self.assertRaises(FileExistsError):
            write_new(target, "new")
        self.assertEqual(target.read_text(), "preserved")
        self.assertFalse(list(self.base.glob(".signal-library-init-*.tmp")))

    def test_publish_failure_leaves_no_partial_target_and_resumes(self):
        actual_link = os.link
        def fail(source, destination, *args, **kwargs):
            if Path(destination).name == "catalog.csv":
                raise OSError("simulated unsupported filesystem")
            return actual_link(source, destination, *args, **kwargs)
        with patch("signal_library.initialization.os.link", side_effect=fail):
            with self.assertRaises(OSError):
                self.init()
        self.assertFalse((self.root / "catalog.csv").exists())
        self.assertFalse(list(self.root.glob("*.tmp")))
        self.init(resume=True)
        self.assertEqual(inspect_root(self.root), "ready")

    def test_resume_rechecks_journal_after_lock(self):
        with patch("signal_library.initialization.validate_partial", side_effect=OSError()):
            with self.assertRaises(OSError):
                self.init()
        journal = self.root / ".signal-library-init.json"
        record = json.loads(journal.read_text())
        real_open = os.open
        def change(path, *args, **kwargs):
            fd = real_open(path, *args, **kwargs)
            if Path(path).name == ".signal-library.lock":
                journal.write_text(json.dumps({**record, "created_at": "2026-10-06T00:00:00+00:00"}))
            return fd
        with patch("signal_library.initialization.os.open", side_effect=change):
            with self.assertRaises(InitializationError):
                self.init(resume=True)
        self.assertFalse((self.root / "catalog.csv").exists())

    def test_final_validation_preserves_unexpected_added_data(self):
        actual_write = write_new
        def changed(path, data):
            actual_write(path, data)
            if path.name == "library.json":
                (self.root / "books" / "unexpected.txt").write_text("preserved")
        with patch("signal_library.initialization.write_new", side_effect=changed):
            with self.assertRaises(InitializationError):
                self.init()
        self.assertTrue((self.root / ".signal-library-init.json").exists())
        self.assertEqual((self.root / "books" / "unexpected.txt").read_text(), "preserved")

    def test_changed_journal_before_completion_is_preserved(self):
        actual_write = write_new
        journal = self.root / ".signal-library-init.json"
        def changed(path, data):
            actual_write(path, data)
            if path.name == "library.json":
                record = json.loads(journal.read_text())
                journal.write_text(json.dumps({**record, "created_at": "2026-10-06T00:00:00+00:00"}))
        with patch("signal_library.initialization.write_new", side_effect=changed):
            with self.assertRaises(InitializationError):
                self.init()
        self.assertTrue(journal.exists())

    def test_replaced_lock_is_not_deleted(self):
        actual_write = write_new
        lock = self.root / ".signal-library.lock"
        def changed(path, data):
            actual_write(path, data)
            if path.name == "library.json":
                other = self.root / "replacement-lock"
                other.write_text("another owner")
                os.replace(other, lock)
        with patch("signal_library.initialization.write_new", side_effect=changed):
            self.init()
        self.assertEqual(lock.read_text(), "another owner")
