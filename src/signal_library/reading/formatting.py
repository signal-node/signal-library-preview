"""原典比較から与えた整形計画を適用する。機械処理だけで照合済みにしない。"""
import re
from copy import deepcopy

from ..registration import RegistrationError

JP = r'\u3040-\u30ff\u3400-\u9fff'
PROTECTED = re.compile(r'(`+[^`\n]*`+|(?<!\\)\$(?!\$)(?:\\.|[^$\n])*?(?<!\\)\$|\\\([^\n]*?\\\)|\]\([^\n]*?\)|https?://\S+|<[^>]*>)')


def validate_plan(plan):
    def fields(value, allowed, required):
        if not isinstance(value,dict) or set(value)-set(allowed) or not set(required)<=set(value):
            raise RegistrationError('整形計画に未知・不足の項目があります。')
    def text(value):
        if not isinstance(value,str) or not value.strip():
            raise RegistrationError('整形計画の位置・対象・理由は空でない文字列で指定してください。')
    fields(plan, ('schema_version','pages','headings','join_pages','unresolved'), ('schema_version',))
    if plan['schema_version']!='signal-library-curation-1':
        raise RegistrationError('整形計画の形式が不正です。')
    for key in ('pages','headings','join_pages','unresolved'):
        if not isinstance(plan.get(key,[]),list):
            raise RegistrationError('整形計画の対応項目は配列で指定してください。')
    for page in plan.get('pages',[]):
        fields(page,('source_id','remove','replacements','expected_figures','expected_tables','header_footer_confirmed','paragraphs_confirmed'),('source_id',))
        text(page['source_id'])
        for flag in ('header_footer_confirmed','paragraphs_confirmed'):
            if flag in page and type(page[flag]) is not bool:
                raise RegistrationError('整形確認は真偽値で指定してください。')
        for count in ('expected_figures','expected_tables'):
            if count in page and (type(page[count]) is not int or page[count]<0):
                raise RegistrationError('図表の期待数は0以上の整数で指定してください。')
        for kind, allowed in (('remove',('location','text','reason')),('replacements',('before','after','reason'))):
            if not isinstance(page.get(kind,[]),list):
                raise RegistrationError('整形の修正・除去は配列で指定してください。')
            for edit in page.get(kind,[]):
                fields(edit,allowed,allowed)
                for key in allowed:
                    if key=='after' and edit[key]=='':continue
                    text(edit[key])
    for heading in plan.get('headings',[]):
        fields(heading,('before','after','reason'),('before','after','reason'))
        for value in heading.values():text(value)
    for join in plan.get('join_pages',[]):
        keys=('left_source_id','right_source_id','expected_left_suffix','expected_right_prefix','reason')
        fields(join,keys,keys)
        for value in join.values():text(value)
    for issue in plan.get('unresolved',[]):
        if isinstance(issue,str):text(issue)
        else:
            fields(issue,('source_id','issue','notes'),('issue',))
            for value in issue.values():text(value)


def comma(text):
    def ordinary(part):
        return re.sub(fr'(?<=[{JP}])[,，]|[,，](?=[{JP}])', '、', part)
    parts = PROTECTED.split(text)
    return ''.join(part if index % 2 else ordinary(part) for index, part in enumerate(parts))


def join_text(left, right):
    space = ' ' if left and right and re.search(r'[A-Za-z0-9]$', left) and re.match(r'[A-Za-z0-9]', right) else ''
    return left + space + right


def protected_blocks(text):
    """フェンス・独立数式・構造行を保持し、通常段落の紙面折返しだけ結合する。"""
    blocks, paragraph, special = [], [], []
    mode = None
    pending_gap = False
    def append(block):
        nonlocal pending_gap
        block["blank_before"] = pending_gap
        blocks.append(block)
        pending_gap = False
    def flush():
        if paragraph:
            value = paragraph[0]
            for line in paragraph[1:]: value = join_text(value, line)
            append({'kind': 'prose', 'text': value})
            paragraph.clear()
    for line in text.splitlines():
        stripped = line.strip()
        if mode:
            special.append(line)
            closes = ((mode.startswith('`') or mode.startswith('~')) and bool(re.fullmatch(re.escape(mode[0])+'{'+str(len(mode))+',}'+r'\s*',stripped))) or (mode == '$$' and '$$' in stripped) or (mode == r'\[' and r'\]' in stripped) or (mode.startswith(r'\begin') and mode.replace(r'\begin',r'\end') in stripped)
            if closes:
                append({'kind': 'protected', 'text': '\n'.join(special)})
                special.clear(); mode = None
            continue
        start = re.match(r'^(`{3,}|~{3,})', stripped)
        if start or stripped.startswith(('$$',r'\[',r'\begin{')):
            flush()
            token = start.group(1) if start else ('$$' if stripped.startswith('$$') else (r'\[' if stripped.startswith(r'\[') else re.match(r'\\begin\{[^}]+}',stripped).group(0)))
            one_line = (token == '$$' and len(stripped) > 4 and stripped.endswith('$$')) or (token == r'\[' and stripped.endswith(r'\]')) or (token.startswith(r'\begin') and token.replace(r'\begin',r'\end') in stripped)
            if one_line:append({'kind': 'protected', 'text': line})
            else:mode=token;special.append(line)
            continue
        if not stripped:
            flush();pending_gap = True;continue
        structural = bool(re.match(r'^(#{1,6}\s|[-+*]\s|\d+[.)]\s|>|\[\^[^]]+\]:|!\[|\||(?:[-*_]\s*){3,}$)', stripped)) or line.startswith(('  ','\t'))
        if structural:
            flush();append({'kind': 'structure', 'text': line if line.startswith(('    ','\t')) else comma(line)})
        elif line.endswith(('  ','\\')):
            value=comma(line)
            if paragraph:
                value=paragraph[0]
                for previous_line in paragraph[1:]:value=join_text(value,previous_line)
                value=join_text(value,comma(line));paragraph.clear()
            append({'kind': 'hardbreak', 'text': value})
        else:
            paragraph.append(comma(stripped))
    flush()
    if mode:raise RegistrationError('閉じていない数式・コードブロックがあります。')
    return blocks


def separator(previous, current):
    return '\n' if previous=='hardbreak' or previous=='structure' and current=='structure' else '\n\n'


def render_blocks(blocks):
    previous=None;result=[]
    for block in blocks:
        result.append(('' if previous is None else ('\n\n' if block.get('blank_before') else separator(previous,block['kind'])))+block['text'])
        previous=block['kind']
    return ''.join(result)


def table_count(text):
    lines=text.splitlines();count=0
    for index,line in enumerate(lines):
        if index==0 or not re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*',line):continue
        def cells(row):
            safe=PROTECTED.sub(lambda match:'PROTECTED',row.strip())
            return re.split(r'(?<!\\)\|',safe.strip('|'))
        width=len(cells(lines[index-1]))
        if width!=len(cells(line)):raise RegistrationError('表のヘッダと区切り行の列数が異なります。')
        for row in lines[index+1:]:
            if not row.strip() or '|' not in row:break
            if len(cells(row))!=width:raise RegistrationError('Markdown表の列数が揃っていません。')
        count+=1
    return count


def format_pages(pages, plan=None, image_transform=None):
    """pagesはsource_id/sequence/text、planは原典確認に基づく編集データ。"""
    plan=deepcopy(plan if plan is not None else {})
    if plan:validate_plan(plan)
    elif not isinstance(plan,dict):raise RegistrationError('整形計画はJSON objectで指定してください。')
    if not isinstance(pages,list) or not pages or any(not isinstance(page,dict) or
            not isinstance(page.get('source_id'),str) or not page['source_id'] or
            type(page.get('sequence')) is not int or page['sequence']<1 or
            not isinstance(page.get('text'),str) for page in pages):
        raise RegistrationError('整形対象の原本ID・順序・本文が不正です。')
    if len({page['sequence'] for page in pages})!=len(pages):
        raise RegistrationError('整形対象の順序が重複しています。')
    ids=[page['source_id'] for page in pages]
    if len(ids)!=len(set(ids)):raise RegistrationError('整形対象の原本IDが重複しています。')
    page_plans={entry['source_id']:entry for entry in plan.get('pages',[])}
    if len(page_plans)!=len(plan.get('pages',[])) or set(page_plans)-set(ids):raise RegistrationError('整形計画のページ対応が不正です。')
    headings=plan.get('headings',[])
    edits, output, issues = [], [], list(plan.get('unresolved',[]))
    for page in sorted(pages,key=lambda p:p['sequence']):
        identifier=page['source_id'];settings=page_plans.get(identifier,{})
        text=re.sub(r'<!-- page: \d+ -->','',page['text']).strip('\r\n')
        for removal in settings.get('remove',[]):
            lines=text.splitlines();nonempty=[i for i,line in enumerate(lines) if line.strip()]
            location=removal.get('location')
            if location not in {'header','footer'} or not removal.get('reason'):raise RegistrationError('柱・フッタ除去には位置と理由が必要です。')
            if not nonempty:raise RegistrationError('除去対象のページ本文が空です。')
            index=nonempty[0] if location=='header' else nonempty[-1]
            if lines[index]!=removal['text']:raise RegistrationError('除去対象が計画のページ端と一致しません。')
            edits.append({'source_ids':[identifier],'before':lines[index],'after':'','reason':removal['reason'],'kind':location})
            del lines[index];text='\n'.join(lines).strip('\r\n')
        for replacement in settings.get('replacements',[]):
            before,after=replacement['before'],replacement['after']
            if not before or text.count(before)!=1 or not replacement.get('reason'):raise RegistrationError('修正箇所が一意ではないか理由がありません。')
            text=text.replace(before,after,1)
            edits.append({'source_ids':[identifier],'before':before,'after':after,'reason':replacement['reason'],'kind':'correction'})
        mapping = {}
        for heading in headings:
            before, after = heading['before'], heading['after']
            if not re.fullmatch(r'#{1,6} .+', before) or not re.fullmatch(r'#{2,6} .+', after) or not heading.get('reason') or before in mapping:
                raise RegistrationError('見出し対応と理由を一意に指定してください。書籍の見出しはレベル2以降です。')
            mapping[before] = heading
        blocks=protected_blocks(text)
        for block in blocks:
            heading = mapping.get(block['text']) if block['kind'] == 'structure' else None
            if heading:
                block['text'] = heading['after']
                if heading['before'] != heading['after']:
                    edits.append({'source_ids':[identifier], 'before':heading['before'], 'after':heading['after'], 'reason':heading['reason'], 'kind':'heading'})
        if image_transform is not None:
            for block in blocks:
                if block['kind'] != 'protected' and not block['text'].startswith(('    ', '\t')):
                    block['text'] = image_transform(identifier, block['text'])
        normalized=render_blocks(blocks)
        # テーブル/箇条書き等の構造行は一つの連続blockへ戻す。
        for index,block in enumerate(blocks):
            block['sources']=[identifier]
        if blocks and output:
            blocks[0]['blank_before']=True
        visible=render_blocks([block for block in blocks if block['kind'] != 'protected' and not block['text'].startswith(('    ', '\t'))])
        figures=len(re.findall(r'!\[[^]]*]\([^\n)]+\)',visible))
        tables=table_count(visible)
        for kind,found in (('figures',figures),('tables',tables)):
            expected=settings.get('expected_'+kind)
            if type(expected) is not int or expected<0 or expected!=found:issues.append({'source_id':identifier,'issue':kind+'_unconfirmed_or_mismatch','found':found,'expected':expected})
        if not blocks:issues.append({'source_id':identifier,'issue':'empty_page_after_removal'})
        if settings.get('header_footer_confirmed') is not True:issues.append({'source_id':identifier,'issue':'header_footer_unconfirmed'})
        if settings.get('paragraphs_confirmed') is not True:issues.append({'source_id':identifier,'issue':'paragraphs_unconfirmed'})
        if re.search(r'<\s*(table|img|script)\b',visible,re.I):issues.append({'source_id':identifier,'issue':'raw_html_requires_review'})
        if normalized!=text:edits.append({'source_ids':[identifier],'before':text,'after':normalized,'reason':'読点・折返し・段落間隔を整える（数式/コード等は保持）','kind':'normalization'})
        output.append({'source_id':identifier,'blocks':blocks})
    for join in plan.get('join_pages',[]):
        left=next((p for p in output if p['source_id']==join['left_source_id']),None)
        right=next((p for p in output if p['source_id']==join['right_source_id']),None)
        if left is None or right is None or output.index(right)!=output.index(left)+1 or not left['blocks'] or not right['blocks']:raise RegistrationError('ページをまたぐ結合が隣接するページではありません。')
        a,b=left['blocks'][-1],right['blocks'][0]
        if a['kind']!='prose' or b['kind']!='prose' or not a['text'].endswith(join['expected_left_suffix']) or not b['text'].startswith(join['expected_right_prefix']) or not join.get('expected_left_suffix') or not join.get('expected_right_prefix') or not join.get('reason'):raise RegistrationError('文の結合箇所・構造・理由が計画と一致しません。')
        before=a['text']+'\n'+b['text'];a['text']=join_text(a['text'],b['text']);a['sources']+=b['sources'];right['blocks'].pop(0)
        edits.append({'source_ids':a['sources'][:],'before':before,'after':a['text'],'reason':join['reason'],'kind':'join_pages'})
    blocks=[block for page in output for block in page['blocks']]
    rendered=[];maps=[];previous=None;line=1;level=1
    for block in blocks:
        # 同じ構造の連続行（表・リスト・脚注等）は空行を挟まない。
        gap=('\n\n' if block.get('blank_before') else separator(previous,block['kind'])) if rendered else ''
        rendered.append(gap+block['text'])
        line+=gap.count('\n')
        maps.append({'start_line':line,'end_line':line+block['text'].count('\n'),'source_ids':block['sources'],'file_page':1})
        line+=block['text'].count('\n');previous=block['kind']
        match=re.match(r'^(#{1,6}) ',block['text'])
        if match:
            current=len(match.group(1))
            if current==1 or current>level+1:issues.append({'source_ids':block['sources'],'issue':'heading_hierarchy_requires_review'})
            level=current
    return {'text':''.join(rendered).strip('\n')+'\n','source_map':maps,'corrections':edits,'issues':issues,
            'format_status':'complete' if not issues else 'needs_review','review_status':'unreviewed'}
