import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from signal_library.cli import main
from signal_library.initialization import initialize
from signal_library import registration as r


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'Books 日本語'
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        initialize(str(self.root), True, False, self.repo)
        self.input = self.base / '入力 空白'
        self.input.mkdir()
        self.a = self.input / 'z.png'
        self.b = self.input / 'a.jpeg'
        # シグネチャ検査用。画像の復号・画質検査の証拠にはしない。
        self.a.write_bytes(b'\x89PNG\r\n\x1a\n' + b'a' * 20)
        self.b.write_bytes(b'\xff\xd8\xff' + b'b' * 20)
        self.md = self.input / '本文.md'
        self.md.write_text('# テスト\n\n![図](assets/図.png)\n', encoding='utf-8')
        (self.input / 'assets').mkdir()
        (self.input / 'assets' / '図.png').write_bytes(self.a.read_bytes())
        self.plan = self.input / 'plan.json'
        self.data = {'schema_version': 'signal-library-registration-1', 'metadata': {'title': '人工の本、試験用', 'contributors': ['著者'], 'language': 'ja'},
                     'order_confirmed': True, 'images': [{'path': 'z.png', 'scope': '表紙'}, {'path': 'a.jpeg', 'scope': '本文'}],
                     'markdown': [{'path': '本文.md', 'provenance': '人工fixture', 'assets': ['assets/図.png']}]}
        self.save()

    def save(self):
        self.plan.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def register(self, apply=True, resume=False):
        return r.register(str(self.root), self.plan, apply, resume)

    def test_full_registration_order_ids_assets_and_idempotence(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        planned = self.register(False)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        result = self.register()
        self.assertEqual(planned['book_id'], result['book_id'])
        book = r.inventory(self.root)[0][result['book_id']]
        self.assertEqual([s['sha256'] for s in book['originals']], [r.digest(self.a), r.digest(self.b)])
        self.assertTrue(book['originals'][0]['path'].endswith('0001.png'))
        self.assertTrue(book['originals'][1]['path'].endswith('0002.jpg'))
        self.assertEqual(book['ocr_runs'], [])
        self.assertEqual(book['active_reading']['kind'], None)
        source = book['markdown_sources'][0]
        copied_md = self.root / source['path']
        self.assertEqual(copied_md.read_bytes(), self.md.read_bytes())
        self.assertEqual((copied_md.parent / 'assets/図.png').read_bytes(), self.a.read_bytes())
        self.assertEqual(self.register()['state'], 'already_registered')
        self.assertEqual(r.list_books(str(self.root))['count'], 1)
        self.assertEqual(self.a.read_bytes(), b'\x89PNG\r\n\x1a\n' + b'a' * 20)

    def test_no_api_key_and_cli_json(self):
        with patch.dict(os.environ, {}, clear=True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['register', '--root', str(self.root), '--plan', str(self.plan), '--apply', '--json'])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(output.getvalue())['network_used'])

    def test_copy_failure_can_resume_without_duplicate(self):
        actual = r.publish
        def fail(root, entry):
            if entry['path'].endswith('0002.jpg'):
                raise OSError('injected failure')
            actual(root, entry)
        with patch.object(r, 'publish', side_effect=fail):
            with self.assertRaises(OSError):
                self.register()
        self.assertTrue((self.root / r.JOURNAL).exists())
        self.assertFalse((self.root / r.LOCK).exists())
        with self.assertRaises(r.RegistrationError):
            self.register()
        self.assertEqual(self.register(resume=True)['state'], 'registered')
        self.assertEqual(len(next(iter(r.inventory(self.root)[0].values()))['originals']), 2)

    def test_catalog_failure_after_book_can_resume(self):
        actual = r.publish
        def fail(root, entry):
            if entry['path'] == 'catalog.csv':
                raise OSError('catalog failure')
            actual(root, entry)
        with patch.object(r, 'publish', side_effect=fail):
            with self.assertRaises(OSError):
                self.register()
        self.register(resume=True)
        self.assertEqual(r.list_books(str(self.root), True)['count'], 1)

    def test_changed_input_on_resume_is_rejected(self):
        with patch.object(r, 'publish', side_effect=OSError()):
            with self.assertRaises(OSError):
                self.register()
        self.a.write_bytes(self.a.read_bytes() + b'changed')
        with self.assertRaises(r.RegistrationError):
            self.register(resume=True)
        self.assertTrue((self.root / r.JOURNAL).exists())

    def test_unexpected_destination_is_never_overwritten(self):
        with patch.object(r, 'publish', side_effect=OSError()):
            with self.assertRaises(OSError):
                self.register()
        transaction = r.read_json(self.root / r.JOURNAL)
        entry = next(e for e in transaction['files'] if e['path'].endswith('0001.png'))
        target = self.root / entry['path']
        target.parent.mkdir(parents=True)
        target.write_bytes(b'preserved')
        with self.assertRaises(r.RegistrationError):
            self.register(resume=True)
        self.assertEqual(target.read_bytes(), b'preserved')

    def test_tampered_journal_target_or_content_rejected(self):
        with patch.object(r, 'publish', side_effect=OSError()):
            with self.assertRaises(OSError):
                self.register()
        journal = self.root / r.JOURNAL
        original = journal.read_bytes()
        for entry_change in ({'path': 'library.json'}, {'text': 'malicious', 'sha256': r.hash_bytes(b'malicious')}):
            value = json.loads(original)
            value['files'][0].update(entry_change)
            journal.write_text(r.encode(value), encoding='utf-8')
            with self.assertRaises(r.RegistrationError):
                self.register(resume=True)
        journal.write_bytes(original)
        self.register(resume=True)

    def test_duplicate_and_unconfirmed_images_rejected(self):
        for change in ({'order_confirmed': False}, {'images': [{'path': 'z.png'}, {'path': 'z.png'}]}):
            self.data.update(change)
            self.save()
            with self.assertRaises(r.RegistrationError):
                self.register()
        self.assertEqual(r.list_books(str(self.root))['count'], 0)

    def test_existing_book_add_capture_preserves_first_and_old_book(self):
        result = self.register()
        original_book = (self.root / 'books' / result['book_id'] / 'book.json').read_bytes()
        self.data = {'schema_version': 'signal-library-registration-1', 'book_id': result['book_id'], 'capture_id': 'capture-002', 'order_confirmed': True, 'images': [{'path': 'z.png'}]}
        self.save()
        with self.assertRaises(r.RegistrationError):
            self.register()
        self.a.write_bytes(self.a.read_bytes() + b'new capture')
        self.register()
        book = r.inventory(self.root)[0][result['book_id']]
        self.assertEqual(len(book['originals']), 3)
        self.assertEqual([s['capture_id'] for s in book['originals']], ['capture-001', 'capture-001', 'capture-002'])
        self.assertEqual(self.register()['state'], 'already_registered')
        snapshots = list((self.root / 'books' / result['book_id'] / 'metadata').rglob('book-before.json'))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].read_bytes(), original_book)
        self.assertNotEqual(original_book, (self.root / 'books' / result['book_id'] / 'book.json').read_bytes())

    def test_existing_book_update_interruption_recovers(self):
        result = self.register()
        self.a.write_bytes(self.a.read_bytes() + b'new capture')
        self.data = {'schema_version': 'signal-library-registration-1', 'book_id': result['book_id'], 'capture_id': 'capture-002', 'order_confirmed': True, 'images': [{'path': 'z.png'}]}
        self.save()
        actual = r.publish
        def fail(root, entry):
            if entry['path'].endswith('book.json'):
                raise OSError('failure after pages')
            actual(root, entry)
        with patch.object(r, 'publish', side_effect=fail):
            with self.assertRaises(OSError):
                self.register()
        self.register(resume=True)
        self.assertEqual(len(r.inventory(self.root)[0][result['book_id']]['originals']), 3)

    def test_validation_detects_changed_original_and_catalog(self):
        result = self.register()
        book = r.inventory(self.root)[0][result['book_id']]
        original = self.root / book['originals'][0]['path']
        original.write_bytes(b'changed')
        with self.assertRaises(r.RegistrationError):
            r.list_books(str(self.root), True)

    def test_windows_paths_collisions_and_asset_escape_rejected(self):
        for name in ('../outside', '/absolute', 'CON.png', 'a\\b', 'a.', 'a:stream', '.'):
            with self.assertRaises(r.RegistrationError):
                r.relative(name)
        self.data['markdown'][0]['assets'] = ['../outside']
        self.save()
        with self.assertRaises(r.RegistrationError):
            self.register()

    def test_symlink_input_and_destination_rejected(self):
        link = self.input / 'link.png'
        try:
            link.symlink_to(self.a)
        except OSError:
            self.skipTest('symlink権限なし')
        self.data['images'] = [{'path': 'link.png'}]
        self.save()
        with self.assertRaises(r.RegistrationError):
            self.register()

    def test_adopt_only_adds_marker_and_keeps_files(self):
        self.register()
        (self.root / 'library.json').unlink()
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(r.adopt(str(self.root))['state'], 'adoption_planned')
        r.adopt(str(self.root), True)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, {k: v for k, v in after.items() if k != 'library.json'})
        self.assertEqual(r.adopt(str(self.root), True)['state'], 'already_managed')

    def test_lock_blocks_all_writes(self):
        (self.root / r.LOCK).write_bytes(b'other owner')
        with self.assertRaises(r.RegistrationError):
            self.register()
        self.assertEqual((self.root / r.LOCK).read_bytes(), b'other owner')

    def test_copy_library_to_another_root_preserves_references(self):
        self.register()
        copied = self.base / '復元 Books'
        shutil.copytree(self.root, copied)
        self.assertEqual(r.list_books(str(copied), True)['count'], 1)

    def test_interruption_at_every_publish_step_recovers(self):
        prepared = r.prepare(self.root, self.plan)
        count = len(r.build_transaction(self.root, prepared)["files"])
        # 各公開直後に中断し、最終catalog公開直後も再開する。
        for stop in range(count):
            with self.subTest(step=stop):
                for path in list((self.root / 'books').iterdir()):
                    shutil.rmtree(path)
                (self.root / 'catalog.csv').write_text(','.join(r.CATALOG_FIELDS) + '\n', encoding='utf-8')
                actual = r.publish
                index = 0
                def fail(root, entry):
                    nonlocal index
                    actual(root, entry)
                    if index == stop:
                        raise OSError('after publication')
                    index += 1
                with patch.object(r, 'publish', side_effect=fail):
                    with self.assertRaises(OSError):
                        self.register()
                self.assertEqual(r.inspect_root(self.root), 'registering')
                self.register(resume=True)
                self.assertEqual(r.list_books(str(self.root), True)['count'], 1)

    def test_crlf_catalog_and_pages_are_compared_as_exact_bytes(self):
        result = self.register()
        for path in (self.root / 'catalog.csv', self.root / 'books' / result['book_id'] / 'pages.csv', self.root / 'books' / result['book_id'] / 'book.json'):
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        original = (self.root / 'books' / result['book_id'] / 'book.json').read_bytes()
        self.a.write_bytes(self.a.read_bytes() + b'new')
        self.data = {'schema_version': 'signal-library-registration-1', 'book_id': result['book_id'], 'capture_id': 'capture-002', 'order_confirmed': True, 'images': [{'path': 'z.png'}]}
        self.save()
        self.register()
        snapshots = list((self.root / 'books' / result['book_id'] / 'metadata').rglob('book-before.json'))
        self.assertEqual(snapshots[0].read_bytes(), original)
        self.assertEqual(r.list_books(str(self.root), True)['count'], 1)

    def test_changed_source_during_copy_keeps_journal(self):
        actual = r.publish
        def changed(root, entry):
            if entry['path'].endswith('0001.png'):
                self.a.write_bytes(self.a.read_bytes() + b'changed')
            actual(root, entry)
        with patch.object(r, 'publish', side_effect=changed):
            with self.assertRaises(r.RegistrationError):
                self.register()
        self.assertTrue((self.root / r.JOURNAL).exists())
        self.assertFalse(list((self.root / 'books').rglob('0001.png')))

    def test_duplicate_input_under_another_filename_is_detected(self):
        self.register()
        duplicate = self.input / 'renamed.png'
        duplicate.write_bytes(self.a.read_bytes())
        self.data['images'] = [{'path': 'renamed.png'}]
        self.data.pop('markdown')
        self.save()
        with self.assertRaises(r.RegistrationError):
            self.register()
        self.assertEqual(r.list_books(str(self.root))['count'], 1)

    def test_asset_case_collision_and_corrupt_pages_rejected(self):
        self.data['markdown'][0]['assets'] = ['assets/図.png', 'ASSETS/図.png']
        self.save()
        with self.assertRaises(r.RegistrationError):
            self.register()
        self.data['markdown'][0]['assets'] = ['assets/図.png']
        self.save()
        result = self.register()
        pages = self.root / 'books' / result['book_id'] / 'pages.csv'
        pages.write_text('wrong header', encoding='utf-8')
        with self.assertRaises(r.RegistrationError):
            r.list_books(str(self.root), True)

    def test_unknown_fields_or_wrong_signature_rejected(self):
        self.data['unexpected'] = True
        self.save()
        with self.assertRaises(r.RegistrationError):
            self.register()
        self.data.pop('unexpected')
        self.a.write_bytes(b'not an image')
        self.save()
        with self.assertRaises(r.RegistrationError):
            self.register()


if __name__ == '__main__':
    unittest.main()
