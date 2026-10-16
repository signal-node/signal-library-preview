"""共通の検査済みASTから固定組版のPDFを作成する。"""

import contextlib
import json
import re
from datetime import datetime

from ..artifacts import publish_files
from ..ocr.files import check_tree
from ..registration import (RegistrationError, book_id, contained, encode, hash_bytes,
                            inventory, library_root, locked, read_json, relative)
from . import html
from .engine import Pandoc, PANDOC_VERSIONS, READER
from .inputs import resolve_input, content, checked_bytes
from .typst import Typst, VERSION, validate_pdf
from ..presentation import reading_label

# 本文のヘッダ/フッタを復活させず、再組版のページ番号を原典位置と区別する。
TEMPLATE='''#set page(paper: "a4", margin: (x: 20mm, y: 20mm), numbering: "1", number-align: center)
#set text(font: "Noto Sans CJK JP", size: 10pt, lang: "ja")
#set par(justify: false, leading: 0.65em)
#set heading(numbering: none)
#show figure.where(kind: table): set block(breakable: true)
#show raw: set text(font: "Noto Sans CJK JP")
#show image: it => { set image(width: 100%); layout(size => { let dim = measure(it); set image(height: calc.min(dim.height, 190mm), fit: "contain"); it }) }
'''
POLICY='safe-pdf-2'
PROFILES={'safe-pdf-1',POLICY}


def settings(book,policy=POLICY):
    if policy not in PROFILES:raise RegistrationError('未対応のPDF生成profileです。')
    return {'policy':policy,'reader':READER,'title':book['title'],'language':book['language'],
            'paper':'a4','margin_mm':20,'font_size_pt':10,'toc':True,'page_number':'reflowed',
            'template_sha256':hash_bytes(TEMPLATE.encode()),'font_sha256':html.FONT_HASH,
            'font_license_sha256':html.LICENSE_HASH}


def literal_citations(value):
    # 初版では外部bibliographyを読まない。@表記を勝手に文献へ解決しない。
    if isinstance(value,dict):
        if value.get('t')=='Cite':value.update(t='Span',c=[['',[],[]],value['c'][1]])
        for item in value.values():literal_citations(item)
    elif isinstance(value,list):
        for item in value:literal_citations(item)


def prepare(root, identifier, export_id, font_directory, bundle_path=None, markdown_id=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',export_id):
        raise RegistrationError('export_idは英数字・ハイフン・アンダースコアで指定してください。')
    book,source=resolve_input(root,identifier,bundle_path,markdown_id)
    destination=f'books/{identifier}/exports/pdf/{export_id}';folder=contained(root,destination)
    if folder.exists():check_tree(folder)
    if (folder/'export.json').exists():policy=read_json(folder/'export.json')['settings']['policy']
    elif (folder/html.JOURNAL).exists():policy=read_json(folder/html.JOURNAL)['context'].get('policy','safe-pdf-1')
    else:policy=POLICY
    render_settings=settings(book,policy)
    files=html.font_files(font_directory)
    pandoc=Pandoc();typst=Typst()
    data,ast,headings,used=content(root,source,pandoc,files)
    literal_citations(ast)
    legacy_label=(f"使用版: {source['kind']} / {source['id']}\n原典照合: {source['review_status']} / 整形: {source['format_status']}\n"
           'このPDFのページ番号は再組版後の位置であり、原著の紙面番号ではありません。')
    label=(reading_label(source,True)+'\nこのPDFのページ番号は再組版後の位置であり、原著の紙面番号ではありません。'
           if policy==POLICY else legacy_label)
    front={'pandoc-api-version':ast['pandoc-api-version'],'meta':{},'blocks':[
        {'t':'Para','c':[{'t':'Strong','c':[{'t':'Str','c':book['title']}]}]},
        {'t':'Para','c':[{'t':'Str','c':label}]}]}
    # ユーザー文字列をTypstコードへ直接埋め込まず、同じwriterでエスケープする。
    prefix=TEMPLATE+'#set text(lang: '+json.dumps((book['language'] or 'ja').split('-')[0],ensure_ascii=False)+')\n'
    prefix+='#set document(title: '+json.dumps(book['title'],ensure_ascii=False)+')\n'
    text=prefix+pandoc.typst(front)+'\n#outline(title: [目次], depth: 6)\n\n'+pandoc.typst(ast)
    stamp=html.created_at(folder)
    files['source.typ']=text.encode()
    files['book.pdf']=typst.compile(files,int(datetime.fromisoformat(stamp).timestamp()))
    files.update({'source.md':data,'source.json':encode(ast).encode(),'template.typ':TEMPLATE.encode()})
    manifest={'schema_version':'signal-library-export-1','format':'pdf','book_id':identifier,'export_id':export_id,
              'created_at':stamp,'source':source,'used_assets':used,'settings':render_settings,
              'engine':{'pandoc':pandoc.version,'typst':typst.version},
              'validation':{'structural':'passed','visual':'pending'},
              'files':[{'path':p,'sha256':hash_bytes(v)} for p,v in sorted(files.items())]}
    files['export.json']=encode(manifest).encode()
    transaction={'schema_version':'signal-library-export-publication-1','destination':destination,
                 'context':{'created_at':stamp,'policy':policy},
                 'files':[{'path':p,'sha256':hash_bytes(v)} for p,v in sorted(files.items())]}
    return destination,manifest,files,transaction


def validate(folder):
    check_tree(folder);manifest=read_json(folder/'export.json')
    if manifest.get('schema_version')!='signal-library-export-1' or manifest.get('format')!='pdf':
        raise RegistrationError('PDF manifestの形式が不正です。')
    book_id(manifest['book_id'])
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',manifest['export_id']) or
            datetime.fromisoformat(manifest['created_at']).tzinfo is None or
            manifest['validation']!={'structural':'passed','visual':'pending'} or
            manifest['engine']!={'pandoc':manifest['engine'].get('pandoc'),'typst':VERSION} or
            manifest['engine']['pandoc'] not in PANDOC_VERSIONS or
            manifest['settings']!=settings(manifest['settings'],manifest['settings'].get('policy'))):
        raise RegistrationError('PDFの設定・日時・エンジン記録が固定rendererと一致しません。')
    files=manifest['files'];names=[relative(f['path']).as_posix() for f in files]
    required={html.FONT,html.LICENSE,'book.pdf','source.md','source.json','source.typ','template.typ'}
    if len(names)!=len(set(names)) or set(names)&{'export.json',html.JOURNAL}:
        raise RegistrationError('PDFのファイル一覧が不正です。')
    for record in files:checked_bytes(folder,record)
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual-{'export.json',html.JOURNAL}!=set(names) or not required<=set(names):
        raise RegistrationError('PDFのファイル一覧と実体が一致しません。')
    if hash_bytes((folder/'source.md').read_bytes())!=manifest['source']['sha256'] or (folder/'template.typ').read_bytes()!=TEMPLATE.encode():
        raise RegistrationError('PDFの原文/テンプレートsnapshotが一致しません。')
    html.font_files(folder)
    from .images import image_suffix
    for name in names:
        if name.startswith('assets/'):image_suffix(folder/name,(folder/name).read_bytes())
    validate_pdf((folder/'book.pdf').read_bytes())
    return manifest


def export_pdf(value, identifier, export_id, font_directory=None, bundle_path=None, markdown_id=None, apply=False):
    from .publication import render
    return render(value,identifier,export_id,font_directory,bundle_path,markdown_id,apply,
                  prepare,validate,'pdf','book.pdf')
