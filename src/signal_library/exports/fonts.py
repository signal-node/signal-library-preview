"""固定font/OFLの導入。ダウンロードは明示的なapply時だけ行う。"""

from urllib.request import urlopen

from ..artifacts import publish_files
from ..registration import RegistrationError, contained, encode, hash_bytes, library_root, locked, read_json
from . import html

CACHE = '.signal-library-assets/noto-cjk-Sans2.004'
BASE = 'https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/'
URLS = {html.FONT:BASE+'Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf',html.LICENSE:BASE+'LICENSE'}
JOURNAL = '.font-publication.json'


def fetch(url):
    with urlopen(url,timeout=30) as response:
        if response.geturl()!=url:
            raise RegistrationError('font取得先が予定URLから変わりました。取得元を確認してください。')
        data=response.read(24*1024*1024+1)
    if len(data)>24*1024*1024:raise RegistrationError('font資材が取得サイズ上限を超えています。')
    return data


def manifest():
    return {'schema_version':'signal-library-font-1','family':'Noto Sans CJK JP','release':'Sans2.004',
            'files':[{'path':name,'sha256':sha,'url':URLS[name]} for name,sha in
                     ((html.FONT,html.FONT_HASH),(html.LICENSE,html.LICENSE_HASH))]}


def validate(folder):
    from ..ocr.files import check_tree
    check_tree(folder)
    if read_json(folder/'font.json')!=manifest():
        raise RegistrationError('font導入記録が固定版と一致しません。')
    html.font_files(folder)
    names={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if names-{JOURNAL}!={html.FONT,html.LICENSE,'font.json'}:
        raise RegistrationError('font保存先に未知のファイルがあります。')


def install(value, source_directory=None, apply=False, downloader=fetch):
    root=library_root(value)
    folder=contained(root,CACHE)
    if (folder/'font.json').exists() and not (folder/JOURNAL).exists():
        validate(folder)
        return {'status':'ok','state':'font_ready','font_directory':CACHE,'files_written':False,
                'network_used':False,'next_actions':[]}
    record=manifest()
    if not apply:
        return {'status':'ok','state':'font_installation_planned','plan':record,'plan_hash':hash_bytes(encode(record).encode()),
                'font_directory':CACHE,'files_written':False,'network_used':False,
                'next_actions':['--source-dirで取得済み資材を指定するか、--applyで固定URLからfont/OFLを取得してください。']}
    with locked(root):
        if source_directory is not None:
            files=html.font_files(source_directory)
        else:
            files={}
            for entry in record['files']:
                data=downloader(entry['url'])
                if hash_bytes(data)!=entry['sha256']:
                    raise RegistrationError('取得したfont/OFLのhashが固定版と一致しません。')
                files[entry['path']]=data
        files['font.json']=encode(record).encode()
        transaction={'schema_version':'signal-library-font-publication-1','destination':CACHE,
                     'files':[{'path':name,'sha256':hash_bytes(data)} for name,data in sorted(files.items())]}
        publish_files(root,CACHE,files,transaction,'font.json',JOURNAL,validate)
    return {'status':'ok','state':'font_installed','font_directory':CACHE,'files_written':True,
            'network_used':source_directory is None,'next_actions':[]}
