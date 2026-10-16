import contextlib
import getpass
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from signal_library.credentials import CredentialError, configure_mistral
from signal_library.cli import main


@unittest.skipUnless(shutil.which("git") and shutil.which("mise"), "Git/miseが必要")
class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        (self.repo / ".gitignore").write_text("mise.local.toml\n", encoding="utf-8")
        (self.repo / "mise.toml").write_text("[env]\n", encoding="utf-8")
        self.target = self.repo / "mise.local.toml"

    def configure(self, key="canary-secret-only-test", replace=False):
        with patch("signal_library.credentials.getpass.getpass", return_value=key):
            return configure_mistral(self.repo, replace)

    def test_real_mise_preserves_settings_and_hides_key(self):
        self.target.write_text('# 設定\n[env]\nLIBRARY_ROOT = "C:/日本語 Books"\n\n[settings]\njobs = 2\n', encoding="utf-8")
        result = self.configure()
        self.assertNotIn("canary-secret", json.dumps(result))
        import tomllib
        config = tomllib.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(config["env"]["LIBRARY_ROOT"], "C:/日本語 Books")
        self.assertEqual(config["settings"], {"jobs": 2})
        self.assertEqual(config["env"]["MISTRAL_API_KEY"], "canary-secret-only-test")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        before = self.target.read_bytes()
        with self.assertRaises(CredentialError):
            self.configure()
        self.assertEqual(self.target.read_bytes(), before)
        self.configure("updated-canary", replace=True)
        self.assertIn("updated-canary", self.target.read_text(encoding="utf-8"))
        self.assertEqual(list(self.repo.glob(".signal-library-config*")), [])

    def test_unignored_or_tracked_file_is_rejected_before_input(self):
        (self.repo / ".gitignore").write_text("")
        with patch("signal_library.credentials.getpass.getpass") as prompt:
            with self.assertRaises(CredentialError):
                configure_mistral(self.repo)
            prompt.assert_not_called()
        (self.repo / ".gitignore").write_text("mise.local.toml\n")
        self.target.write_text("[env]\n")
        subprocess.run(["git", "add", "-f", "mise.local.toml"], cwd=self.repo, check=True)
        with self.assertRaises(CredentialError):
            self.configure()

    def test_no_echo_fallback(self):
        def unavailable(*args):
            warnings.warn("no tty", getpass.GetPassWarning)
            return "must-not-write"
        with patch("signal_library.credentials.getpass.getpass", side_effect=unavailable):
            with self.assertRaises(CredentialError):
                configure_mistral(self.repo)
        self.assertFalse(self.target.exists())

    def test_failed_mise_output_is_redacted_and_original_preserved(self):
        original = b"[env]\n"
        self.target.write_bytes(original)
        actual_run = subprocess.run
        def fail_mise(args, **kwargs):
            if "--stdin" in args:
                self.assertNotIn("canary-secret-only-test", " ".join(args))
                return subprocess.CompletedProcess(args, 1, "canary-secret-only-test", "canary-secret-only-test")
            return actual_run(args, **kwargs)
        output = io.StringIO()
        with patch("signal_library.credentials.subprocess.run", side_effect=fail_mise), patch("signal_library.credentials.getpass.getpass", return_value="canary-secret-only-test"), patch("signal_library.cli.Path.cwd", return_value=self.repo):
            with contextlib.redirect_stdout(output):
                code = main(["configure-key", "--json"])
        self.assertEqual(code, 1)
        self.assertNotIn("canary-secret", output.getvalue())
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(list(self.repo.glob(".signal-library-config*")), [])

    def test_config_changed_during_input_is_not_overwritten(self):
        def changed(*args):
            self.target.write_text("# another edit\n[env]\n", encoding="utf-8")
            return "canary-secret-only-test"
        with patch("signal_library.credentials.getpass.getpass", side_effect=changed):
            with self.assertRaises(CredentialError):
                configure_mistral(self.repo)
        self.assertEqual(self.target.read_text(), "# another edit\n[env]\n")

    def test_invalid_input_and_existing_lock(self):
        for value in ("", "with space", "line\nbreak", "{{template}}"):
            with self.assertRaises(CredentialError):
                self.configure(value)
        lock = self.repo / ".signal-library-config.lock"
        lock.write_text("busy")
        with self.assertRaises(FileExistsError):
            self.configure()
        self.assertEqual(lock.read_text(), "busy")
        self.assertFalse(self.target.exists())
