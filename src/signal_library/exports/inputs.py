"""登録済み本文・参照資材をHTML/PDF共通の入力へ解決する。"""

from urllib.parse import unquote, urlsplit

from ..registration import RegistrationError, inventory, read_json, contained, checked_file, hash_bytes
from .engine import safe_ast
from .images import image_suffix


def resolve_input(root, identifier, bundle_path=None, markdown_id=None):
    books,_=inventory(root)
    if identifier not in books or bundle_path is not None and markdown_id is not None:
        raise RegistrationError('登録書籍と、1種類の入力版を指定してください。')
    book=books[identifier]
    if bundle_path is None and markdown_id is None:
        active=book.get('active_reading',{})
        bundle_path=active.get('bundle_path')
        if active.get('kind')=='markdown':markdown_id=active.get('id')
        if not bundle_path and not markdown_id:
            raise RegistrationError('採用版がありません。--bundleまたは--markdown-idで登録済み版を指定してください。')
    if bundle_path is not None:
        records=[b for b in book.get('reading_bundles',[]) if b['bundle_path']==bundle_path]
        if len(records)!=1:raise RegistrationError('登録済みの読書bundleを指定してください。')
        record=records[0];value=read_json(contained(root,bundle_path))
        folder=bundle_path.removesuffix('bundle.json')
        assets=[{'path':folder+f['path'],'sha256':f['sha256']} for f in value['files'] if f['path'].startswith('assets/')]
        source={'kind':record['kind'],'id':record['reading_id'],'path':record['markdown_path'],'sha256':record['markdown_sha256'],
                'bundle_path':bundle_path,'bundle_sha256':record['bundle_sha256'],
                'source_ids':[s['source_id'] for s in value['sources']],
                'format_status':record['format_status'],'review_status':'range_records' if any(
                    r['bundle_path']==bundle_path for r in book.get('reading_reviews',[])) else 'unreviewed'}
    else:
        records=[s for s in book.get('markdown_sources',[]) if s['source_id']==markdown_id]
        if len(records)!=1:raise RegistrationError('登録済みのMarkdown原本IDを指定してください。')
        record=records[0];assets=record['assets']
        source={'kind':'markdown','id':markdown_id,'path':record['path'],'sha256':record['sha256'],
                'source_ids':[markdown_id],'provenance':record['provenance'],
                'format_status':'not_assessed','review_status':record.get('status','unreviewed')}
    return book,{**source,'assets':assets}


def checked_bytes(root, record):
    path=checked_file(contained(root,record['path']))
    if path.stat().st_size>64*1024*1024:raise RegistrationError('変換ファイルがサイズ上限を超えています。対象範囲を分けてください。')
    data=path.read_bytes()
    if hash_bytes(data)!=record['sha256']:raise RegistrationError('派生出力の入力hashが一致しません。')
    return data



def content(root,source,engine,files):
    data=checked_bytes(root,source)
    allowed={a['path']:a for a in source['assets']}
    markdown_path=contained(root,source['path'])
    used=[]
    def image(target):
        parsed=urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise RegistrationError('図は登録済みのローカル資材を参照してください。')
        path=contained(markdown_path.parent,unquote(target))
        relative_path=path.relative_to(root).as_posix()
        if relative_path not in allowed:raise RegistrationError('本文が未登録の画像資材を参照しています。')
        value=checked_bytes(root,allowed[relative_path])
        suffix=image_suffix(path,value)
        name='assets/'+hash_bytes(value)+suffix
        files[name]=value
        if relative_path not in {a['path'] for a in used}:used.append(allowed[relative_path])
        return name
    ast=engine.parse(data.decode('utf-8'))
    headings=safe_ast(ast,image)
    return data,ast,headings,used
