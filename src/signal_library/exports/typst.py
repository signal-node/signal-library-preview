"""固定Typstと隔離した入力資材だけでPDFを組版する。"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..registration import RegistrationError, relative

VERSION='0.15.1'


class Typst:
    def __init__(self):
        self.executable=shutil.which('typst')
        if not self.executable:raise RegistrationError('PDF生成にはTypst 0.15.1を導入してください。')
        result=subprocess.run([self.executable,'--version'],capture_output=True,text=True,timeout=15,check=False)
        if result.returncode or result.stdout.split()[0:2]!=['typst',VERSION]:
            raise RegistrationError('検証対象のTypst 0.15.1を使ってください。')
        self.version=VERSION

    def compile(self, files, timestamp):
        with tempfile.TemporaryDirectory(prefix='signal-pdf-') as directory:
            root=Path(directory)
            for name,data in files.items():
                target=root/relative(name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            packages=root/'empty-packages';packages.mkdir()
            # 呼出し元のTYPST_*設定やユーザーfont/packageを引き継がない。
            env={k:v for k,v in os.environ.items() if not k.startswith('TYPST_') and k!='SOURCE_DATE_EPOCH'}
            args=[self.executable,'compile','--root',str(root),'--font-path',str(root),'--ignore-system-fonts',
                  '--package-path',str(packages),'--package-cache-path',str(packages),
                  '--creation-timestamp',str(timestamp),'--jobs','1','source.typ','book.pdf']
            result=subprocess.run(args,cwd=root,env=env,capture_output=True,timeout=120,check=False)
            if result.returncode or result.stderr.strip():
                raise RegistrationError('Typstの組版が完了しませんでした。未対応の数式・図・文字と依存を確認してください。')
            path=root/'book.pdf'
            if not path.is_file() or path.stat().st_size>64*1024*1024:
                raise RegistrationError('PDFが未生成かサイズ上限を超えています。')
            data=path.read_bytes();validate_pdf(data)
            return data


def validate_pdf(data):
    # エンジンが生成した静的PDFの公開境界。ページ組版の実確認とは別。
    if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF'):
        raise RegistrationError('PDFの開始/終端が不正です。')
