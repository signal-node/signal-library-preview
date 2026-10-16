"""Offline acceptance checks for image OCR reading bundles."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("assemble", Path(__file__).with_name("assemble-book-ocr.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReadingBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.originals = self.root / "originals"
        self.run = self.root / "run"
        self.originals.mkdir()
        (self.run / "documents").mkdir(parents=True)
        documents = []
        # Byte fixtures represent files, not a claim that OCR was run on real images.
        for page in range(1, 3):
            filename = f"{page:04d}.jpg"
            original = self.originals / filename
            original.write_bytes(f"image fixture {page}".encode())
            stem = f"{page:04d}"
            asset = self.run / "documents" / f"{stem}.assets" / "page-0" / "figure.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"asset fixture")
            (self.run / "documents" / f"{stem}.md").write_text(
                f"<!-- page: 0 -->\n\n本文 {page}\n![図]({stem}.assets/page-0/figure.png)\n", encoding="utf-8"
            )
            documents.append({"source_relative_path": filename, "sha256": module.sha256(original),
                              "pages": 1, "state": "SUCCEEDED", "warnings": [],
                              "output_stem": stem, "custom_id": f"doc-{page}"})
        self.manifest = self.run / "manifest.json"
        self.payload = {"schema_version": 1, "run_id": "test-run", "run_dir": str(self.run),
                        "input_root": str(self.originals), "documents": list(reversed(documents)),
                        "integrity": {"complete": True, "all_succeeded": True}}
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.payload), encoding="utf-8")

    def assemble(self):
        module.assemble(self.manifest, self.root / "reading", "book-test", "capture-001")

    def test_order_sources_assets_and_original_run_preserved(self):
        before = module.sha256(self.manifest)
        self.assemble()
        reading = (self.root / "reading" / "reading.md").read_text(encoding="utf-8")
        self.assertLess(reading.index("本文 1"), reading.index("本文 2"))
        self.assertIn("sha256:" + module.sha256(self.originals / "0001.jpg"), reading)
        self.assertIn("documents/0001.assets/page-0/figure.png", reading)
        bundle = self.root / "reading"
        moved = self.root / "different-root"
        bundle.rename(moved)
        self.assertTrue((moved / "documents/0001.assets/page-0/figure.png").is_file())
        self.assertEqual(module.sha256(self.manifest), before)
        pages = json.loads((moved / "assembly.json").read_text())["pages"]
        self.assertEqual([page["sequence"] for page in pages], [1, 2])
        self.assertEqual([page["ocr_page_index"] for page in pages], [0, 0])

    def test_does_not_overwrite_previous_bundle(self):
        self.assemble()
        before = module.sha256(self.root / "reading/reading.md")
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertEqual(before, module.sha256(self.root / "reading/reading.md"))

    def test_rejects_modified_original(self):
        (self.originals / "0001.jpg").write_bytes(b"modified")
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse((self.root / "reading").exists())

    def test_rejects_missing_image_asset(self):
        (self.run / "documents/0001.assets/page-0/figure.png").unlink()
        with self.assertRaises(ValueError):
            self.assemble()

    def test_rejects_page_sequence_gap(self):
        self.payload["documents"] = self.payload["documents"][:1]
        self.save()
        with self.assertRaises(ValueError):
            self.assemble()

    def test_rejects_unreferenced_symlink_without_copying_external_data(self):
        outside=self.root/'private.txt';outside.write_text('preserved')
        link=self.run/'documents'/'unused'
        try:link.symlink_to(outside)
        except OSError:self.skipTest('symlink権限なし')
        with self.assertRaises((ValueError,module.OcrError,module.RegistrationError)):
            self.assemble()
        self.assertFalse((self.root/'reading').exists())
        self.assertEqual(outside.read_text(),'preserved')

    def test_rejects_symlink_destination(self):
        outside=self.root/'outside';outside.mkdir()
        try:(self.root/'reading').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('symlink権限なし')
        with self.assertRaises((ValueError,module.OcrError,module.RegistrationError)):
            self.assemble()
        self.assertEqual(list(outside.iterdir()),[])

    def test_rejects_incomplete_run(self):
        self.payload["integrity"]["complete"] = False
        self.save()
        with self.assertRaises(ValueError):
            self.assemble()


if __name__ == "__main__":
    unittest.main()
