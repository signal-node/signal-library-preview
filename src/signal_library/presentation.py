"""利用者向け表示。コマンド名とJSON契約は翻訳しない。"""
import argparse
import sys


class JapaneseArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        add_help=kwargs.pop('add_help',True)
        super().__init__(*args,add_help=False,**kwargs)
        self._positionals.title='位置引数'
        self._optionals.title='オプション'
        if add_help:self.add_argument('-h','--help',action='help',help='使い方を表示して終了')

    def format_usage(self):
        return super().format_usage().replace('usage:','使い方:',1)

    def format_help(self):
        return super().format_help().replace('usage:','使い方:',1)

    def error(self,message):
        # 引数の値には秘密が混入し得るため、argparseの原文を表示しない。
        self.print_usage(sys.stderr)
        self.exit(2,f'{self.prog}: 引数が不正です。--helpで必須項目・指定できる値を確認してください。\n')


LABELS={
    'ok':'正常','error':'エラー','action_required':'確認が必要','warning':'注意',
    'optional_missing':'任意の依存が未導入','optional_incompatible':'任意の依存が未対応',
    'passed':'確認済み','pending':'未確認','partial':'一部確認済み','failed':'不合格',
    'current':'現行','stale':'更新が必要','unknown':'不明',
    'python':'Python','library_root':'保存先','mistral_api_key':'Mistral APIキー',
    'mistral_key':'Mistral APIキー','mise':'mise','mistral_sdk':'Mistral SDK','pandoc':'Pandoc','typst':'Typst',
    'unconfigured':'保存先が未設定','ready':'利用準備済み','missing':'未作成','empty':'空の保存先',
    'planned':'計画確認済み','initialized':'初期化済み','already_initialized':'初期化済み',
    'validated':'検証済み','registered':'登録済み','already_registered':'登録済み',
    'locked':'ロック中','initializing':'初期化の途中','registering':'登録の途中',
    'legacy_unmanaged':'旧形式の未管理資料','nonempty_unmanaged':'既存資料がある未管理フォルダ',
    'export_status':'出力の状態','rebind_planned':'保存先の再接続計画','rebound':'保存先を再接続済み',
    'already_bound':'保存先を再接続済み',
}


def label(value):
    return LABELS.get(value,value)


def reading_label(source, multiline=False):
    """照合記録の存在を、全文照合済みと表示しない。"""
    names={'ocr':'OCR読書版','curated':'校正版','markdown':'登録Markdown',
           'unreviewed':'未照合','unverified':'未照合','verified':'照合済み',
           'range_records':'範囲別の照合記録あり','complete':'整形完了',
           'needs_review':'要確認','not_assessed':'未評価'}
    def display(value):return names.get(value,'状態不明（'+value+'）')
    separator='\n' if multiline else ' / '
    return (f"使用版: {display(source['kind'])} / {source['id']}"+separator+
            f"原典照合: {display(source['review_status'])} / 整形: {display(source['format_status'])}")
