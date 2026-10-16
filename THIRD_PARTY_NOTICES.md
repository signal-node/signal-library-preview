# 外部依存と由来

Signal Library本体のコード・スキル・共通文書・空の雛形・人工試験資料は[MITライセンス](LICENSE)。権利表示はCopyright (c) 2026 signal-node。Signal Stackから移管したOCRコードは所有者本人のコードで、移管・公開してよいと利用者が確認済み。別のSignalプラグインやインストール済みキャッシュはこの候補へ含めない。

このソース候補/wheelは外部CLI・SDK・fontのバイナリを同梱しない。必要な依存を各利用者が取得する。依存のライセンスが本体の採用ライセンスへ置き換わるとは扱わない。

| 依存 | 用途・取得方式 | 表示/条件の参照 |
| --- | --- | --- |
| Mistral SDK 2.7.0 | OCR extra、pipによる別導入 | [SDKの公式ライセンス](https://github.com/mistralai/client-python/blob/main/LICENSE)。固定2.7.0の配布物内LICENSEがApache-2.0であることを確認した |
| Pandoc 3.12/3.12.1 | 外部CLI | [GPL-2.0-or-later](https://github.com/jgm/pandoc/blob/main/COPYRIGHT)。同梱は今回行わない |
| Typst 0.15.1 | 外部CLI | [Apache-2.0](https://github.com/typst/typst/blob/v0.15.1/LICENSE) |
| Noto Sans CJK JP Regular Sans2.004 | 固定hashで取得。HTML束/PDF出力用資材へ元font/OFLを保持 | [SIL OFL 1.1](https://github.com/notofonts/noto-cjk/blob/Sans2.004/LICENSE)。配布候補自体にfontファイルは含めない |
| setuptools 80.9.0、build 1.3.0、wheel 0.45.1 | 隔離ビルド用、製品実行には不要 | 各PyPI配布物のライセンス/metadataを確認する |
| pypdf 6.10.0 | 実PDFの検証専用、製品実行には不要 | [BSD-3-Clause](https://github.com/py-pdf/pypdf/blob/main/LICENSE) |

OCRの推移依存はrequirements/ocr-constraints.txtに版を固定する。MIT系に加え、BSD、Apache-2.0、MPL-2.0、PSF系等を含む。固定SDK導入環境のmetadataとライセンスファイルhashを[一覧](docs/dependency-inventory.json)へ記録し、依存を将来同梱する場合は通知/ソース提供等の条件を再確認する。

以前のエンジン試作に使ったPlaywright/PyMuPDFは、今回の製品依存・配布候補へ含めない。元の試作は開発正本に保持する。試験専用という理由でライセンス条件が免除されるとは扱わない。
