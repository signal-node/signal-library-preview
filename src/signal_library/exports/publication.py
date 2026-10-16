"""HTML/PDF共通の排他公開・台帳接続。"""

import contextlib

from ..artifacts import publish_files
from ..registration import (RegistrationError, book_id, contained, encode, hash_bytes,
                            inventory, library_root, locked)


def render(value,identifier,export_id,font_directory,bundle_path,markdown_id,apply,prepare,validate,format,output):
    from .html import JOURNAL
    from .library import JOURNAL as INDEX_JOURNAL, index, recover
    root=library_root(value);book_id(identifier)
    font_directory=font_directory or root/'.signal-library-assets/noto-cjk-Sans2.004'
    with locked(root) if apply else contextlib.nullcontext():
        if apply:recover(root,identifier)
        elif contained(root,f'books/{identifier}/{INDEX_JOURNAL}').exists():
            raise RegistrationError('出力台帳の更新が中断しています。同じ生成操作を--applyで再開してください。')
        records=inventory(root)[0].get(identifier,{}).get('local_exports',[])
        expected=f'books/{identifier}/exports/{format}/{export_id}/export.json'
        if any(r['export_id']==export_id and r['manifest_path']!=expected for r in records):
            raise RegistrationError('export_idは書籍内で形式をまたいで一意にしてください。')
        destination,manifest,files,transaction=prepare(root,identifier,export_id,font_directory,bundle_path,markdown_id)
        if apply:
            publish_files(root,destination,files,transaction,'export.json',JOURNAL,validate)
            index(root,identifier,destination+'/export.json')
        visual={'visual_status':'pending','latest_check_id':None,'check_method':None}
        book=inventory(root)[0][identifier]
        existing=next((r for r in book.get('local_exports',[]) if r['manifest_path']==destination+'/export.json' and
                       r['manifest_sha256']==hash_bytes(encode(manifest).encode())),None)
        if existing:
            from .checks import status_for
            visual=status_for(root,book,existing)
        return {'status':'ok','state':format+('_created' if apply else '_planned'),'book_id':identifier,
                'manifest_path':destination+'/export.json','output_path':destination+'/'+output,
                'source':manifest['source'],'validation':manifest['validation'],**visual,
                'files_written':apply,'network_used':False,
                'next_actions':[] if visual['visual_status']=='passed' else ['表示確認は未完了です。export statusの記録を参照し、未確認項目・指摘箇所を確認してください。']}
