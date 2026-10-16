import contextlib
import io
import json
import os
import sys
import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from signal_library.cli import main
from signal_library.diagnostics import doctor
from signal_library.initialization import InitializationError, initialize, write_new


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "日本語 Books"
        self.repo = self.base / "repository"
        self.repo.mkdir()

    def init(self, apply=True, resume=False):
        return initialize(str(self.root), apply, resume, self.repo)

    def test_preview_and_idempotence(self):
        self.assertEqual(self.init(apply=False)["state"], "planned")
        self.assertFalse(self.root.exists())
        self.init()
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        (self.root / "books" / "preserved.txt").write_text("original")
        self.assertEqual(self.init()["state"], "already_initialized")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})
        self.assertEqual(doctor(str(self.root), {})["status"], "ok")

    def test_unknown_data_is_preserved(self):
        self.root.mkdir()
        source = self.root / "original.txt"
        source.write_bytes(b"original")
        with self.assertRaises(InitializationError):
            self.init()
        self.assertEqual(list(self.root.iterdir()), [source])
        self.assertEqual(source.read_bytes(), b"original")

    def test_repository_overlap(self):
        for target in (self.repo, self.repo / "books", self.base):
            with self.assertRaises(InitializationError):
                initialize(str(target), True, False, self.repo)

    def test_live_or_orphan_lock_is_never_removed(self):
        self.root.mkdir()
        lock = self.root / ".signal-library.lock"
        lock.write_text("another operation")
        with self.assertRaises(InitializationError):
            self.init()
        self.assertEqual(lock.read_text(), "another operation")

    def test_interruption_can_resume(self):
        def interrupted(path, data):
            if path.name == "catalog.csv":
                raise OSError("simulated disk failure")
            write_new(path, data)
        with patch("signal_library.initialization.write_new", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.init()
        self.assertTrue((self.root / ".signal-library-init.json").is_file())
        self.assertFalse((self.root / ".signal-library.lock").exists())
        with self.assertRaises(InitializationError):
            self.init()
        self.assertEqual(self.init(resume=True)["state"], "initialized")

    def test_partial_or_modified_output_is_not_overwritten(self):
        with patch("signal_library.initialization.write_new", side_effect=lambda p, d: write_new(p, d) if p.name.endswith(".json") else (_ for _ in ()).throw(OSError())):
            with self.assertRaises(OSError):
                self.init()
        catalog = self.root / "catalog.csv"
        catalog.write_text("partial")
        with self.assertRaises(InitializationError):
            self.init(resume=True)
        self.assertEqual(catalog.read_text(), "partial")

    def test_unknown_schema_and_symlink(self):
        self.init()
        marker = self.root / "library.json"
        marker.write_text('{"schema_version": "future"}')
        with self.assertRaises(InitializationError):
            self.init()
        marker.unlink()
        try:
            marker.symlink_to(self.repo / "private.json")
        except OSError:
            self.skipTest("この環境にはsymlink作成権限がありません。未知schema拒否は検証済み。")
        with self.assertRaises(InitializationError):
            self.init()

    def test_diagnostics_do_not_disclose_secrets_or_write(self):
        output = io.StringIO()
        secret = "canary-sensitive-123"
        with patch.dict(os.environ, {"MISTRAL_API_KEY": secret, "LIBRARY_ROOT": str(self.root)}, clear=True):
            with contextlib.redirect_stdout(output):
                code = main(["doctor", "--json"])
        self.assertEqual(code, 1)
        self.assertNotIn(secret, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertFalse(result["network_used"])
        self.assertFalse(result["files_written"])
        self.assertFalse(self.root.exists())

    def test_permission_failure_is_structured_and_redacted(self):
        with patch("signal_library.cli.initialize", side_effect=PermissionError("secret-value")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["init", "--root", str(self.root), "--apply", "--json"])
        self.assertEqual(code, 1)
        self.assertNotIn("secret-value", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["error_code"], "initialization_failed")

    def test_clean_source_copy_runs_without_developer_environment(self):
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / "src", self.repo / "src", ignore=shutil.ignore_patterns("__pycache__"))
        (self.repo / "tools").mkdir()
        shutil.copyfile(source / "tools" / "signal-library.py", self.repo / "tools" / "signal-library.py")
        environment = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        command = [sys.executable, "-I", str(self.repo / "tools" / "signal-library.py")]
        result = subprocess.run([*command, "doctor", "--json"], cwd=self.base, env=environment, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["checks"][1]["detail"], "LIBRARY_ROOTが未設定です。")
        result = subprocess.run([*command, "init", "--root", str(self.root), "--apply", "--json"], cwd=self.base, env=environment, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "initialized")


if __name__ == "__main__":
    unittest.main()
