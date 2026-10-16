"""読書版の不変ファイル公開。台帳への登録・採用は別の処理で行う。"""

import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from ..ocr.files import check_tree, write_bytes_atomic
from ..registration import (RegistrationError, book_id, checked_file, contained,
                            encode, hash_bytes, read_json, relative)
from .formatting import format_pages

SCHEMA = 'signal-library-reading-bundle-1'
JOURNAL = '.reading-publication.json'


def snapshot(root, record, path_key, hash_key):
    path = checked_file(contained(root, record[path_key]))
    data = path.read_bytes()
    if hash_bytes(data) != record[hash_key]:
        raise RegistrationError('読書版の入力hashが一致しません。')
    return data


def prepare_bundle(root, destination, identity, pages, plan=None):
    """入力を再読込して公開内容を再現する。planの図参照は元Markdown相対。"""
    root = Path(root)
    identifier = book_id(identity['book_id'])
    kind = identity['kind']
    reading_id = identity['reading_id']
    if kind not in {'ocr', 'curated'} or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', reading_id):
        raise RegistrationError('読書版の種類・IDが不正です。')
    if not re.fullmatch(r'capture-[0-9]{3,}', identity['capture_id']) or not identity.get('run_id'):
        raise RegistrationError('読書版のcapture/run対応がありません。')
    expected = f'books/{identifier}/' + ('reading/' if kind == 'ocr' else 'curated/') + reading_id
    if destination != expected:
        raise RegistrationError('読書版の保存先とIDが一致しません。')
    target = contained(root, destination)
    if not pages or sorted(p['sequence'] for p in pages) != list(range(1, len(pages)+1)):
        raise RegistrationError('読書版の原本順序に欠落・重複があります。')
    files, formatted_pages, sources = {}, [], []
    for page in pages:
        if page['source_id'] != 'sha256:' + page['original_sha256']:
            raise RegistrationError('読書版の原本IDがhashと一致しません。')
        for key in ('original_path', 'markdown_path'):
            if not page[key].startswith(f'books/{identifier}/') or contained(root, page[key]).is_relative_to(target):
                raise RegistrationError('読書版の入力が書籍外または出力内です。')
        snapshot(root, page, 'original_path', 'original_sha256')
        markdown = snapshot(root, page, 'markdown_path', 'markdown_sha256').decode('utf-8')
        formatted_pages.append({'source_id':page['source_id'], 'sequence':page['sequence'], 'text':markdown})
        sources.append(dict(page))
    documents = {p['source_id']:contained(root, p['markdown_path']) for p in pages}
    def transform(identifier, text):
        if re.search(r'<\s*img\b|!\[[^]]*]\s*\[', text, re.I):
            raise RegistrationError('図はインラインMarkdownのローカル参照にしてください。')
        def image(match):
            label, link = match.groups()
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise RegistrationError('読書版に外部・複合画像参照があります。')
            asset = checked_file(contained(documents[identifier].parent, unquote(link)))
            data = asset.read_bytes()
            suffix = asset.suffix.lower()
            if suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
                raise RegistrationError('図の画像形式を確認してください。')
            name = 'assets/' + hash_bytes(data) + suffix
            files[name] = data
            return f'![{label}]({quote(name, safe="/._-")})'
        return re.sub(r'!\[([^]]*)]\(([^)\n]+)\)', image, text)
    result = format_pages(formatted_pages, plan, image_transform=transform)
    if 'source_manifest_path' in identity:
        if not identity['source_manifest_path'].startswith(f'books/{identifier}/ocr/'):
            raise RegistrationError('元OCR manifestの保存先が不正です。')
        files['source-manifest.json'] = snapshot(root,identity,'source_manifest_path','source_manifest_sha256')
    files['reading.md'] = result.pop('text').encode('utf-8')
    files['source-map.json'] = encode(result.pop('source_map')).encode('utf-8')
    files['corrections.json'] = encode(result.pop('corrections')).encode('utf-8')
    files['curation-plan.json'] = encode(plan or {}).encode('utf-8')
    manifest = {'schema_version':SCHEMA, 'identity':dict(identity), 'sources':sources, **result,
                'files':[{'path':name, 'sha256':hash_bytes(data)} for name,data in sorted(files.items())]}
    files['bundle.json'] = encode(manifest).encode('utf-8')
    transaction = {'schema_version':'signal-library-reading-publication-1', 'destination':destination,
                   'files':[{'path':name, 'sha256':hash_bytes(data)} for name,data in sorted(files.items())]}
    return manifest, files, transaction


def publish_bundle(root, destination, identity, pages, plan=None):
    """同一入力だけ再開可能。完成ファイルも旧版も上書きしない。呼出側でlibrary lockを持つ。"""
    root = Path(root)
    manifest, files, transaction = prepare_bundle(root, destination, identity, pages, plan)
    from ..artifacts import publish_files
    publish_files(root,destination,files,transaction,'bundle.json',JOURNAL,validate_bundle)
    return manifest



def validate_bundle(folder):
    """コピー後にも資材と本文を検証する。原典照合の判定は行わない。"""
    folder = Path(folder)
    check_tree(folder)
    manifest = read_json(folder / 'bundle.json')
    if manifest.get('schema_version') != SCHEMA or manifest.get('review_status') != 'unreviewed':
        raise RegistrationError('読書版manifestの形式が不正です。')
    names = set()
    for entry in manifest['files']:
        name = relative(entry['path']).as_posix()
        if name in names or name in {'bundle.json', JOURNAL}:
            raise RegistrationError('読書版のファイル一覧が不正です。')
        names.add(name)
        snapshot(folder, entry, 'path', 'sha256')
    if not {'reading.md', 'source-map.json', 'corrections.json', 'curation-plan.json'} <= names:
        raise RegistrationError('読書版に必要な対応・修正記録がありません。')
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual - {JOURNAL, 'bundle.json'} != names:
        raise RegistrationError('読書版のファイル一覧と実体が一致しません。')
    if manifest.get('format_status') not in {'complete','needs_review'} or not isinstance(manifest.get('issues'),list):
        raise RegistrationError('読書版の整形状態が不正です。')
    if (manifest['format_status']=='complete') != (not manifest['issues']):
        raise RegistrationError('読書版の整形状態と未解決箇所が一致しません。')
    sources=manifest['sources']
    identifiers={s['source_id'] for s in sources}
    if (not sources or len(identifiers)!=len(sources) or
            sorted(s['sequence'] for s in sources)!=list(range(1,len(sources)+1)) or
            any(s['source_id']!='sha256:'+s['original_sha256'] for s in sources)):
        raise RegistrationError('読書版の原本ID・順序が不正です。')
    lines=(folder/'reading.md').read_bytes().decode('utf-8').splitlines()
    covered=set();previous=0
    for entry in read_json(folder/'source-map.json'):
        start,end=entry['start_line'],entry['end_line']
        if (type(start) is not int or type(end) is not int or not previous<start<=end<=len(lines) or
                type(entry['file_page']) is not int or entry['file_page']!=1 or
                not isinstance(entry['source_ids'],list) or not entry['source_ids'] or
                len(set(entry['source_ids']))!=len(entry['source_ids']) or not set(entry['source_ids'])<=identifiers):
            raise RegistrationError('読書版の行別原本対応が不正です。')
        covered.update(range(start,end+1));previous=end
    if any(index not in covered for index,line in enumerate(lines,1) if line.strip()):
        raise RegistrationError('読書本文に原本対応がない行があります。')
    for correction in read_json(folder/'corrections.json'):
        if (not correction.get('source_ids') or not set(correction['source_ids'])<=identifiers or
                not isinstance(correction.get('before'),str) or not isinstance(correction.get('after'),str) or
                not isinstance(correction.get('reason'),str) or not correction['reason'].strip()):
            raise RegistrationError('読書版の修正理由・原本対応が不正です。')
    identity=manifest['identity']
    if identity.get('source_manifest_path'):
        if 'source-manifest.json' not in names or hash_bytes((folder/'source-manifest.json').read_bytes())!=identity['source_manifest_sha256']:
            raise RegistrationError('元OCR manifestのスナップショットが一致しません。')
    return manifest
