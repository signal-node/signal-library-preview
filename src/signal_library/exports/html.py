"""登録済みMarkdownから外部通信なしでHTML資材束を作る。"""

import contextlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..artifacts import publish_files
from ..ocr.files import check_tree
from ..registration import (RegistrationError, book_id, checked_file, contained, encode, hash_bytes,
                            inventory, library_root, locked, read_json, relative)
from .engine import Pandoc, PANDOC_VERSIONS, READER, LEGACY_READER, STYLE, DocumentValidator, document, safe_ast
from .images import image_suffix
from ..presentation import reading_label
from .inputs import resolve_input, checked_bytes, content

FONT = 'NotoSansCJKjp-Regular.otf'
LICENSE = 'font-LICENSE.txt'
FONT_HASH = '68a3fc98800b2a27b371f2fb79991daf3633bd89309d4ffaa6946fd587f375b5'
LICENSE_HASH = '6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2'
JOURNAL = '.export-publication.json'
POLICY='safe-html-3'
PROFILES = {'safe-html-1':LEGACY_READER,'safe-html-2':READER,POLICY:READER}


def profile(folder):
    if (folder/'export.json').exists():policy=read_json(folder/'export.json')['settings']['policy']
    elif (folder/JOURNAL).exists():policy=read_json(folder/JOURNAL)['context'].get('policy','safe-html-1')
    else:policy=POLICY
    if policy not in PROFILES:raise RegistrationError('未対応のHTML生成profileです。')
    return policy,PROFILES[policy]




def font_files(directory):
    folder=Path(directory)
    result={}
    for name,sha in ((FONT,FONT_HASH),(LICENSE,LICENSE_HASH)):
        if not (folder/name).exists():
            raise RegistrationError('固定font/OFLが未導入です。export install-fontを使うか、--font-dirで取得済み資材を指定してください。')
        path=checked_file(folder/name)
        if path.stat().st_size>24*1024*1024:raise RegistrationError('font資材がサイズ上限を超えています。')
        data=path.read_bytes()
        if hash_bytes(data)!=sha:raise RegistrationError('固定版Noto CJK fontまたはOFLライセンスのhashが一致しません。')
        result[name]=data
    return result


def created_at(folder):
    if (folder/JOURNAL).exists():value=read_json(folder/JOURNAL)['context']['created_at']
    elif (folder/'export.json').exists():value=read_json(folder/'export.json')['created_at']
    else:return datetime.now(timezone.utc).isoformat()
    stamp=datetime.fromisoformat(value)
    if stamp.tzinfo is None:raise RegistrationError('派生出力の作成日時が不正です。')
    return value


def prepare(root, identifier, export_id, font_directory, bundle_path=None, markdown_id=None, engine=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',export_id):
        raise RegistrationError('export_idは英数字・ハイフン・アンダースコアで指定してください。')
    book,source=resolve_input(root,identifier,bundle_path,markdown_id)
    destination=f'books/{identifier}/exports/html/{export_id}'
    folder=contained(root,destination)
    if folder.exists():check_tree(folder)
    policy,reader=profile(folder)
    files=font_files(font_directory)
    engine=engine or Pandoc(reader=reader)
    if getattr(engine,'reader',reader)!=reader:raise RegistrationError('エンジンと保存済み読取りprofileが一致しません。')
    data,ast,headings,used=content(root,source,engine,files)
    fragment=engine.html(ast)
    label=(reading_label(source) if policy==POLICY else
           f"{source['kind']} / {source['id']} / {source['review_status']} / 整形: {source['format_status']}")
    output=document(fragment,headings,[p for p in files if p.startswith('assets/')],book['title'],book['language'],label)
    files.update({'index.html':output.encode(),'style.css':STYLE.encode(),'source.md':data,'source.json':encode(ast).encode()})
    manifest={'schema_version':'signal-library-export-1','format':'html','book_id':identifier,'export_id':export_id,
              'created_at':created_at(folder),'source':source,'used_assets':used,
              'settings':{'language':book['language'],'title':book['title'],'math':'mathml','toc':True,'policy':policy,
                          'style_sha256':hash_bytes(STYLE.encode()),'font_sha256':FONT_HASH,'font_license_sha256':LICENSE_HASH},
              'engine':{'pandoc':engine.version},'validation':{'structural':'passed','visual':'pending'},
              'files':[{'path':p,'sha256':hash_bytes(v)} for p,v in sorted(files.items())]}
    if policy!='safe-html-1':manifest['settings']['reader']=reader
    files['export.json']=encode(manifest).encode()
    transaction={'schema_version':'signal-library-export-publication-1','destination':destination,
                 'context':{'created_at':manifest['created_at']},
                 'files':[{'path':p,'sha256':hash_bytes(v)} for p,v in sorted(files.items())]}
    if policy!='safe-html-1':transaction['context']['policy']=policy
    return destination,manifest,files,transaction


def validate(folder):
    check_tree(folder)
    manifest=read_json(folder/'export.json')
    if manifest.get('schema_version')!='signal-library-export-1' or manifest.get('format')!='html':
        raise RegistrationError('派生出力manifestの形式が不正です。')
    book_id(manifest['book_id'])
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',manifest['export_id']) or
            datetime.fromisoformat(manifest['created_at']).tzinfo is None or
            manifest['validation']!={'structural':'passed','visual':'pending'} or
            manifest['engine'].get('pandoc') not in PANDOC_VERSIONS):
        raise RegistrationError('派生出力のID・日時・生成時状態・エンジン記録が不正です。')
    settings=manifest['settings']
    expected={'language','title','math','toc','policy','style_sha256','font_sha256','font_license_sha256'}
    if settings.get('policy')!='safe-html-1':expected.add('reader')
    if (set(settings)!=expected or settings.get('policy') not in PROFILES or
            (settings['policy']!='safe-html-1' and settings.get('reader')!=PROFILES[settings['policy']]) or
            settings['math']!='mathml' or settings['toc'] is not True or
            settings['style_sha256']!=hash_bytes(STYLE.encode()) or settings['font_sha256']!=FONT_HASH or
            settings['font_license_sha256']!=LICENSE_HASH):
        raise RegistrationError('派生出力の設定と対応する固定rendererが一致しません。')
    files=manifest['files'];names=[relative(e['path']).as_posix() for e in files]
    if len(names)!=len(set(names)) or set(names)&{'export.json',JOURNAL}:
        raise RegistrationError('派生出力のファイル一覧が不正です。')
    for record in files:checked_bytes(folder,record)
    if hash_bytes((folder/'source.md').read_bytes())!=manifest['source']['sha256']:
        raise RegistrationError('派生出力の原文snapshotと入力hashが一致しません。')
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual-{JOURNAL,'export.json'}!=set(names) or not {FONT,LICENSE,'style.css','index.html','source.json','source.md'}<=set(names):
        raise RegistrationError('派生出力のファイル一覧と実体が一致しません。')
    font_files(folder)
    if (folder/'style.css').read_bytes()!=STYLE.encode():
        raise RegistrationError('派生出力のCSSが固定版と一致しません。')
    assets=[name for name in names if name.startswith('assets/')]
    for name in assets:image_suffix(folder/name,(folder/name).read_bytes())
    validator=DocumentValidator(assets)
    validator.feed((folder/'index.html').read_bytes().decode('utf-8'));validator.finish()
    return manifest


def export_html(value, identifier, export_id, font_directory=None, bundle_path=None, markdown_id=None, apply=False):
    from .publication import render
    return render(value,identifier,export_id,font_directory,bundle_path,markdown_id,apply,
                  prepare,validate,'html','index.html')
