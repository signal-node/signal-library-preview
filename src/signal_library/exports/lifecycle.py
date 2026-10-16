"""不変の出力を保ったまま、現在の入力との差と新IDの再生成を扱う。"""

from ..registration import RegistrationError, inventory, library_root, read_json, contained
from .engine import READER, STYLE
from .inputs import resolve_input


def comparison(root,book,output,bundle_path=None,markdown_id=None):
    basis='explicit'
    if bundle_path is None and markdown_id is None:
        basis='active_reading'
        active=book.get('active_reading',{})
        if not active.get('bundle_path') and active.get('kind')!='markdown':
            # 明示入力のみの書籍でも、元版・現行rendererとの差を確認できる。
            basis='original_source'
            source=output['source']
            if source['kind']=='markdown':markdown_id=source['id']
            else:bundle_path=source['bundle_path']
    _,current=resolve_input(root,book['book_id'],bundle_path,markdown_id)
    manifest=read_json(contained(root,output['manifest_path']))
    prior=output['source'];reasons=[]
    for key,reason in (('kind','source_version'),('id','source_version'),('path','source_version'),
                       ('sha256','source_content'),('bundle_sha256','source_content'),('assets','source_assets'),
                       ('format_status','source_status'),('review_status','source_status')):
        if prior.get(key)!=current.get(key) and reason not in reasons:reasons.append(reason)
    actual=manifest['settings']
    if any(actual.get(key)!=book[key] for key in ('title','language')):reasons.append('book_metadata')
    if output['format']=='html':
        from ..registration import hash_bytes
        from .html import POLICY
        expected={'policy':POLICY,'reader':READER,'style_sha256':hash_bytes(STYLE.encode())}
    else:
        from .pdf import settings
        expected=settings(book)
    if any(actual.get(k)!=v for k,v in expected.items() if k not in {'title','language'}):reasons.append('renderer_settings')
    return {'freshness':'stale' if reasons else 'current','stale_reasons':reasons,
            'compared_source':current,'comparison_basis':basis}


def regenerate(value,identifier,from_id,export_id,font_directory=None,bundle_path=None,markdown_id=None,apply=False):
    root=library_root(value);books,_=inventory(root)
    if identifier not in books:raise RegistrationError('登録済み書籍を指定してください。')
    book=books[identifier]
    records=[r for r in book.get('local_exports',[]) if r['export_id']==from_id]
    if len(records)!=1:raise RegistrationError('再生成元のexport_idを指定してください。')
    old=records[0]
    if export_id==from_id:raise RegistrationError('再生成には元出力と異なる新しいexport_idを指定してください。')
    state=comparison(root,book,old,bundle_path,markdown_id)
    source=state['compared_source']
    if source['kind']=='markdown':markdown_id=source['id'];bundle_path=None
    else:bundle_path=source['bundle_path'];markdown_id=None
    if old['format']=='html':
        from .html import export_html as render
    else:
        from .pdf import export_pdf as render
    # 出力は常に新IDで生成する。元出力への確認記録や状態は移さない。
    result=render(value,identifier,export_id,font_directory,bundle_path,markdown_id,apply)
    return {**result,'regenerated_from':old['manifest_path'],'previous_freshness':state['freshness'],
            'stale_reasons':state['stale_reasons']}
