"""Pandocの読取り・AST・HTML境界。本文から変換オプションを取得しない。"""

import html
import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit

from ..registration import RegistrationError

PANDOC_VERSIONS = {'3.12', '3.12.1'}
LEGACY_READER = 'markdown-raw_html-raw_tex-raw_attribute-yaml_metadata_block-pandoc_title_block'
READER = 'markdown-raw_html-raw_tex-raw_attribute'


class Pandoc:
    def __init__(self,reader=READER):
        if reader not in {READER,LEGACY_READER}:raise RegistrationError('未対応のMarkdown読取りprofileです。')
        self.reader=reader
        self.executable = shutil.which('pandoc')
        if not self.executable:
            raise RegistrationError('HTML/PDF生成にはPandoc 3.12または3.12.1を導入してください。')
        line=self.run(['--version'],'').splitlines()[0]
        if not line.startswith('pandoc ') or line.split()[1] not in PANDOC_VERSIONS:
            raise RegistrationError('検証対象のPandoc 3.12または3.12.1を使ってください。')
        self.version=line.split()[1]

    def run(self, options, text):
        # user data directoryのテンプレート/設定を利用しない。stdinだけを渡す。
        with tempfile.TemporaryDirectory(prefix='signal-export-') as directory:
            result=subprocess.run([self.executable,'--sandbox','--fail-if-warnings','--data-dir='+directory,*options,
                                   '+RTS','-M512M','-RTS'],input=text,encoding='utf-8',
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=directory,timeout=60,check=False)
        if result.returncode or result.stderr.strip():
            raise RegistrationError('Pandocの変換が完了しませんでした。未対応の数式・構文・依存を確認してください。')
        if len(result.stdout)>64*1024*1024:
            raise RegistrationError('変換出力が上限を超えています。対象範囲を分けてください。')
        return result.stdout

    def parse(self, text):
        if len(text.encode('utf-8'))>16*1024*1024:
            raise RegistrationError('Markdownが変換のサイズ上限を超えています。')
        value=json.loads(self.run(['--from='+self.reader,'--to=json'],text))
        value['meta']={}
        return value

    def html(self, ast):
        return self.run(['--from=json','--to=html5','--math-method=mathml','--wrap=none'],json.dumps(ast,ensure_ascii=False))

    def typst(self, ast):
        return self.run(['--from=json','--to=typst','--wrap=none'],json.dumps(ast,ensure_ascii=False))


def safe_link(target):
    if not isinstance(target,str) or re.search(r'[\x00-\x20\x7f\\]',target):
        raise RegistrationError('リンク先に制御文字・空白・非正規パスがあります。')
    parsed=urlsplit(target)
    if target.startswith('#') and len(target)>1:
        return target
    if parsed.scheme in {'https','http'} and parsed.netloc and not parsed.username and not parsed.password:
        return target
    raise RegistrationError('リンクは文書内アンカーまたはhttp/httpsを使ってください。')


def safe_ast(ast, image_resolver):
    """外部画像/属性を通さず、HTML/PDF共通の内容を返す。"""
    headings=[]
    def walk(value):
        if isinstance(value,list):
            # Pandoc Attr: identifier, classes, key-value pairs。
            if (len(value)==3 and isinstance(value[0],str) and isinstance(value[1],list) and
                    all(isinstance(c,str) for c in value[1]) and isinstance(value[2],list) and
                    all(isinstance(p,list) and len(p)==2 and all(isinstance(c,str) for c in p) for p in value[2])):
                value[1]=[c for c in value[1] if re.fullmatch(r'[A-Za-z0-9_-]+',c)]
                value[2]=[]
            for item in value:walk(item)
        elif isinstance(value,dict):
            kind=value.get('t')
            if kind in {'RawInline','RawBlock'}:
                raise RegistrationError('Raw構文を派生出力へ渡すことはできません。')
            if kind=='Image':
                value['c'][2][0]=image_resolver(value['c'][2][0])
            elif kind=='Link':
                value['c'][2][0]=safe_link(value['c'][2][0])
            for child in value.values():walk(child)
            if kind=='Header':
                level,attr,inlines=value['c']
                headings.append({'level':level,'id':attr[0],'text':inline_text(inlines)})
    ast['meta']={}
    walk(ast)
    return headings


def inline_text(value):
    if isinstance(value,list):return ''.join(inline_text(item) for item in value)
    if not isinstance(value,dict):return ''
    kind=value.get('t');content=value.get('c')
    if kind=='Str':return content
    if kind in {'Space','SoftBreak','LineBreak'}:return ' '
    if kind in {'Code','Math'}:return content[1]
    if kind in {'Link','Image','Span'}:return inline_text(content[1])
    return inline_text(content)


TAGS = set(('p div span h1 h2 h3 h4 h5 h6 a img br em strong b i u s del sub sup code pre blockquote ul ol li dl dt dd hr '
            'table caption colgroup col thead tbody tfoot tr th td figure figcaption section nav '
            'math semantics mrow mi mn mo ms mtext mspace mfrac msqrt mroot msub msup msubsup munder mover munderover '
            'mfenced mtable mtr mtd maligngroup malignmark menclose mpadded mphantom annotation mstyle '
            'mmultiscripts mprescripts none').split())
ATTRS = set(('id class title role aria-label aria-hidden aria-labelledby aria-describedby href src alt '
             'start type value reversed rowspan colspan scope span style xmlns display encoding '
             'mathvariant mathsize mathcolor mathbackground stretchy symmetric fence separator form '
             'largeop movablelimits accent accentunder lspace rspace minsize maxsize width height depth '
             'align columnalign rowalign columnspacing rowspacing columnlines rowlines frame framespacing '
             'notation linethickness bevelled scriptlevel displaystyle scriptsizemultiplier scriptminsize').split())


class FragmentValidator(HTMLParser):
    def __init__(self, assets):
        super().__init__(convert_charrefs=True)
        self.assets=set(assets);self.ids=set();self.fragments=[]

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:raise RegistrationError('HTMLに許可していない要素があります。')
        names=[k for k,v in attrs]
        if len(names)!=len(set(names)):raise RegistrationError('HTMLの属性が重複しています。')
        for key,value in attrs:
            if key not in ATTRS:raise RegistrationError('HTMLに許可していない属性があります。')
            if key=='style' and not re.fullmatch(r'\s*(?:(?:text-align:\s*(?:left|right|center)|width:\s*[0-9.]+%);?\s*)+',value or ''):
                raise RegistrationError('HTMLに許可していないstyleがあります。')
            if key=='id':
                if not value or value in self.ids:raise RegistrationError('HTMLの文書内IDが重複しています。')
                self.ids.add(value)
            if key=='src':
                if tag!='img' or value not in self.assets:raise RegistrationError('HTMLの画像が登録資材と一致しません。')
            if key=='href':
                if tag!='a':raise RegistrationError('HTMLのリンク要素が不正です。')
                safe_link(value)
                if value.startswith('#'):self.fragments.append(unquote(value[1:]))
            if key=='xmlns' and (tag!='math' or value!='http://www.w3.org/1998/Math/MathML'):
                raise RegistrationError('HTMLの名前空間が不正です。')
    def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs)
    def handle_endtag(self,tag):
        if tag not in TAGS:raise RegistrationError('HTMLに許可していない閉じ要素があります。')
    def handle_decl(self,decl):raise RegistrationError('HTML fragmentに宣言は使えません。')
    def handle_pi(self,data):raise RegistrationError('HTML fragmentに処理命令は使えません。')
    def finish(self):
        self.close()
        if set(self.fragments)-self.ids:raise RegistrationError('文書内リンクの参照先がありません。')


STYLE = '''@font-face {font-family: SignalJP; src: url("NotoSansCJKjp-Regular.otf");}
body {font-family: SignalJP, sans-serif; line-height: 1.8; max-width: 52em; margin: 2em auto; padding: 0 1em; overflow-wrap: anywhere;}
img {max-width: 100%; height: auto;} table {display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse;}
th,td {border: 1px solid #aaa; padding: .25em .6em;} pre {overflow-x: auto; padding: .7em; background: #f5f5f5;}
math[display="block"] {display: block; max-width: 100%; overflow-x: auto;} nav li {list-style: none;} nav .level-2 {margin-left: 1em;} nav .level-3 {margin-left: 2em;}
.source-version {font-size: .9em; color: #555;}'''

CSP="default-src 'none'; img-src 'self'; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"


class DocumentValidator(FragmentValidator):
    def __init__(self,assets):
        super().__init__(assets)
        self.wrappers={};self.csp=0;self.css=0;self.doctype=0
    def handle_starttag(self,tag,attrs):
        if tag in {'html','head','title','body','header','main','meta','link'}:
            values=dict(attrs)
            if len(values)!=len(attrs):raise RegistrationError('HTMLの属性が重複しています。')
            if tag in {'html','head','title','body','header','main'}:
                self.wrappers[tag]=self.wrappers.get(tag,0)+1
                if self.wrappers[tag]!=1 or (tag!='html' and attrs) or (tag=='html' and set(values)!={'lang'}):
                    raise RegistrationError('HTMLの文書構造・属性が不正です。')
            elif tag=='link':
                if values!={'rel':'stylesheet','href':'style.css'}:raise RegistrationError('HTMLのCSS参照が不正です。')
                self.css+=1
            elif tag=='meta':
                if values=={'http-equiv':'Content-Security-Policy','content':CSP}:self.csp+=1
                elif values not in ({'charset':'utf-8'},{'name':'viewport','content':'width=device-width, initial-scale=1'},
                                    {'name':'referrer','content':'no-referrer'}):
                    raise RegistrationError('HTMLのmeta設定が不正です。')
        else:super().handle_starttag(tag,attrs)
    def handle_endtag(self,tag):
        if tag not in {'html','head','title','body','header','main'}:super().handle_endtag(tag)
    def handle_decl(self,decl):
        if decl.lower()!='doctype html':raise RegistrationError('HTMLの宣言が不正です。')
        self.doctype+=1
    def finish(self):
        super().finish()
        if set(self.wrappers)!={'html','head','title','body','header','main'} or self.csp!=1 or self.css!=1 or self.doctype!=1:
            raise RegistrationError('HTMLの文書構造・保護設定が不足しています。')


def document(fragment, headings, assets, title, language, version_label):
    toc='<nav aria-label="目次"><h2>目次</h2><ol>'+''.join(
        f'<li class="level-{h["level"]}"><a href="#{quote(h["id"],safe="._-")}">{html.escape(h["text"])}</a></li>' for h in headings)+'</ol></nav>'
    validator=FragmentValidator(assets)
    validator.feed(toc+fragment);validator.finish()
    # 外部リンクは自動アクセスしない。CSPは本文由来の資材/実行を追加で抑止する。
    return ('<!doctype html>\n<html lang="'+html.escape(language,quote=True)+'"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="'+CSP+'">'
            '<meta name="referrer" content="no-referrer"><title>'+html.escape(title)+'</title>'
            '<link rel="stylesheet" href="style.css"></head><body><header><h1>'+html.escape(title)+'</h1>'
            '<p class="source-version">'+html.escape(version_label)+'</p></header>'+toc+'<main>'+fragment+'</main></body></html>\n')
