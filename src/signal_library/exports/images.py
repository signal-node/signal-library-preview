"""配布資材の形式とSVGの参照/実行境界を確認する。"""

import re
import xml.etree.ElementTree as ET

from ..registration import RegistrationError


def image_suffix(path, data):
    suffix=path.suffix.lower()
    valid={'.png':data.startswith(b'\x89PNG\r\n\x1a\n'),'.jpg':data.startswith(b'\xff\xd8\xff'),
           '.jpeg':data.startswith(b'\xff\xd8\xff'),'.gif':data.startswith((b'GIF87a',b'GIF89a')),
           '.webp':data.startswith(b'RIFF') and data[8:12]==b'WEBP'}
    if suffix=='.svg':
        validate_svg(data);return suffix
    if not valid.get(suffix):raise RegistrationError('図の拡張子と画像形式が一致しません。')
    return suffix


def validate_svg(data):
    if len(data)>4*1024*1024:raise RegistrationError('SVGがサイズ上限を超えています。')
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:raise RegistrationError('SVGはUTF-8で保存してください。') from exc
    encoding=re.search(r'<\?xml\s[^?]*\bencoding\s*=\s*([\'\"])(.*?)\1',text,re.I)
    if '\x00' in text or encoding and encoding.group(2).lower() not in {'utf-8','utf8'}:
        raise RegistrationError('SVGの文字encodingはUTF-8に限定してください。')
    if (re.search(r'<!\s*(?:DOCTYPE|ENTITY)',text,re.I) or
            re.search(r'<\?(?!xml(?:\s|\?>))',text,re.I)):
        raise RegistrationError('SVGのサイズ・宣言を確認してください。')
    try:root=ET.fromstring(text)
    except ET.ParseError as exc:raise RegistrationError('SVGのXML形式が不正です。') from exc
    allowed=set('svg g path rect circle line polyline polygon ellipse text tspan defs marker linearGradient radialGradient stop clipPath mask use title desc'.split())
    names=set('id x y x1 x2 y1 y2 cx cy r rx ry d points width height viewBox preserveAspectRatio fill fill-rule fill-opacity stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-dasharray opacity transform font-family font-size font-weight text-anchor dominant-baseline dx dy offset stop-color stop-opacity gradientUnits gradientTransform markerWidth markerHeight refX refY orient marker-start marker-mid marker-end clip-path mask href version'.split())
    identifiers=set();references=[]
    if root.tag!='{http://www.w3.org/2000/svg}svg':raise RegistrationError('SVGの名前空間が不正です。')
    for element in root.iter():
        if not isinstance(element.tag,str) or element.tag.removeprefix('{http://www.w3.org/2000/svg}') not in allowed:
            raise RegistrationError('SVGに許可していない要素があります。')
        for key,value in element.attrib.items():
            key=key.removeprefix('{http://www.w3.org/1999/xlink}')
            if key not in names or re.search(r'[\x00-\x1f\\]',value) or '/*' in value or '*/' in value:
                raise RegistrationError('SVGに許可していない属性があります。')
            if key=='id':
                if value in identifiers:raise RegistrationError('SVGのIDが重複しています。')
                identifiers.add(value)
            if key=='href':
                if not value.startswith('#'):raise RegistrationError('SVGの外部参照は使えません。')
                references.append(value[1:])
            if re.search(r'url\s*\(',value,re.I):
                match=re.fullmatch(r'url\(#([A-Za-z0-9_.:-]+)\)',value)
                if not match:raise RegistrationError('SVGの資材参照は内部IDに限定してください。')
                references.append(match.group(1))
    if set(references)-identifiers:raise RegistrationError('SVG内部の参照先がありません。')
