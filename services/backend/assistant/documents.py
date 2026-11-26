"""Untrusted document ingestion. Only whitelisted, explicitly labelled data is accepted.
No file text reaches the LLM; no macro, spreadsheet formula or instruction is executed.
"""
import base64
import binascii
import csv
import io
import re
import zipfile
from pathlib import PurePath
from defusedxml import ElementTree as ET
from .parameters import (FIELDS, LABELS, PATHS, ARRAYS, NUM, normalize, parse_json,
                         unflatten, validation, valid_value)

MAX_BYTES=5*1024*1024
MAX_TEXT=250_000
MAX_ROWS=3000
EXTENSIONS={'.txt','.csv','.xlsx','.docx','.pdf','.json'}


def decode_text(raw):
    for encoding in ('utf-8-sig','cp1251'):
        try: return raw.decode(encoding)
        except UnicodeDecodeError: pass
    raise ValueError('Не удалось определить кодировку текста.')


def safe_zip(raw):
    z=zipfile.ZipFile(io.BytesIO(raw))
    infos=z.infolist()
    if len(infos)>1000 or sum(i.file_size for i in infos)>30*1024*1024 or any(i.flag_bits&1 for i in infos):
        z.close(); raise ValueError('Слишком большой или зашифрованный архив Office.')
    return z


def field_path(name):
    m=LABELS.fullmatch(str(name).strip())
    return PATHS[int(m.lastgroup[1:])] if m else None


def read_rows(raw, ext):
    """Yield (location, name, value, unit), or (location, line, None, None)."""
    if ext in ('.txt','.csv'):
        text=decode_text(raw)
        if len(text)>MAX_TEXT: raise ValueError('Слишком много текста в файле.')
        if ext=='.txt':
            for i,line in enumerate(text.splitlines(),1): yield f'строка {i}',line,None,None
        else:
            try: dialect=csv.Sniffer().sniff(text[:4096],delimiters=';,\t')
            except csv.Error: dialect=csv.excel
            reader=csv.reader(io.StringIO(text),dialect)
            for i,row in enumerate(reader,1):
                if len(row)==3: yield f'строка {i}',*row
                elif len(row)==2: yield f'строка {i}',row[0],row[1],''
    elif ext=='.xlsx':
        from openpyxl import load_workbook
        with safe_zip(raw): pass
        wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=False,keep_links=False)
        try:
            for sheet in wb.worksheets:
                if (sheet.max_row or 0)>MAX_ROWS or (sheet.max_column or 0)>100: raise ValueError('Слишком большая таблица.')
                for i,row in enumerate(sheet.iter_rows(max_col=3,values_only=True),1):
                    if all(v is None for v in row): continue
                    name,value,unit=row
                    yield f'лист «{sheet.title}», A{i}:C{i}',name,value,'' if unit is None else str(unit)
        finally: wb.close()
    elif ext=='.docx':
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        with safe_zip(raw) as z: root=ET.fromstring(z.read('word/document.xml'))
        body=root.find('w:body',ns)
        def text(node): return ''.join(t.text or '' for t in node.findall('.//w:t',ns))
        for i,p in enumerate(body.findall('w:p',ns),1):
            for j,line in enumerate(text(p).splitlines(),1): yield f'абзац {i}, строка {j}',line,None,None
        for n,table in enumerate(body.findall('w:tbl',ns),1):
            for i,row in enumerate(table.findall('w:tr',ns),1):
                cells=[text(c) for c in row.findall('w:tc',ns)]
                if len(cells) in (2,3): yield f'таблица {n}, строка {i}',cells[0],cells[1],cells[2] if len(cells)==3 else ''
    elif ext=='.pdf':
        from pypdf import PdfReader
        pdf=PdfReader(io.BytesIO(raw))
        if pdf.is_encrypted or len(pdf.pages)>60: raise ValueError('PDF зашифрован или превышает 60 страниц.')
        total=0
        for i,p in enumerate(pdf.pages,1):
            text=p.extract_text() or ''; total+=len(text)
            if total>MAX_TEXT: raise ValueError('Слишком много текста в PDF.')
            for j,line in enumerate(text.splitlines(),1): yield f'страница {i}, строка {j}',line,None,None


def parse_line(name, value, unit):
    if value is None and unit is None:
        # Strict assignment syntax: never parse arbitrary sentences/instructions.
        match=LABELS.match(str(name).strip())
        if not match: return None
        path=PATHS[int(match.lastgroup[1:])]
        tail=str(name).strip()[match.end():]
        if not re.match(r'^\s*(?:[:=—–]|\[|'+NUM+r')',tail): return None
        tail=re.sub(r'^\s*(?:[:=—–]\s*)?','',tail)
        pattern=r'(\[[^\]]+\]|'+NUM+r')\s*(.*?)\s*$'
        m=re.fullmatch(pattern,tail)
        if not m: raise ValueError(f'{path}: ожидается число и единица измерения.')
        value,unit=m.groups()
    else:
        path=field_path(name)
        if not path: return None
    if type(value) in (int,float): parsed=value
    elif path in ARRAYS:
        m=re.fullmatch(r'\[([^\]]+)\]',str(value).strip())
        if not m: raise ValueError(f'{path}: требуется массив в квадратных скобках.')
        parts=re.split(r'\s*;\s*' if ';' in m[1] else r'\s*,\s*',m[1])
        if not all(re.fullmatch(NUM,p.strip()) for p in parts): raise ValueError(f'{path}: массив содержит нечисловые значения.')
        parsed=[float(p.replace(',','.')) for p in parts]
    elif re.fullmatch(NUM,str(value).strip()): parsed=float(str(value).replace(',','.'))
    else: raise ValueError(f'{path}: требуется число, формулы и произвольный текст не принимаются.')
    return path,normalize(path,parsed,str(unit or ''),canonical=False)


def extract(files):
    data={}; provenance={}; errors=[]; conflicts=set(); total=0
    def add(path,value,source):
        if not valid_value(path,value): errors.append(f'{source}: недопустимое значение {path}.'); return
        if path in conflicts: return
        if path in data and data[path]!=value:
            errors.append(f'Противоречие {path}: {data[path]} ({provenance[path]["source"]}) и {value} ({source}).')
            conflicts.add(path); data.pop(path); provenance.pop(path); return
        data[path]=value
        if path in provenance: provenance[path]['source']+='; '+source
        else: provenance[path]={'source':source,'unit':FIELDS[path],'value':value}
    for file in files:
        name=PurePath(file['name'].replace('\\','/')).name[:180]
        ext=PurePath(name).suffix.lower()
        if ext not in EXTENSIONS: raise ValueError('Поддерживаются TXT, CSV, XLSX, DOCX, PDF и JSON.')
        try: raw=base64.b64decode(file['content'],validate=True)
        except (ValueError,binascii.Error): raise ValueError('Некорректное кодирование файла.') from None
        total+=len(raw)
        if total>MAX_BYTES or not raw: raise ValueError('Общий объём файлов не должен превышать 5 МБ; пустые файлы недопустимы.')
        try:
            if ext=='.json':
                values,issues,evidence=parse_json(decode_text(raw))
                for p,v in values.items(): add(p,v,f'{name} · JSON: {evidence[p]["source"]}')
                errors.extend(f'{name}: {p} — {e["message"]}' for p,e in issues.items())
            else:
                for i,(loc,n,v,u) in enumerate(read_rows(raw,ext),1):
                    if i>MAX_ROWS: raise ValueError('Превышен лимит строк документа.')
                    try:
                        pair=parse_line(n,v,u)
                        if pair: add(*pair,f'{name} · {loc}')
                    except ValueError as exc: errors.append(f'{name} · {loc}: {exc}')
        except Exception as exc:
            # Never expose arbitrary parser internals or file contents.
            errors.append(f'{name}: не удалось безопасно прочитать файл ({type(exc).__name__}).')
    check,_=validation(data)
    return {'parameters':unflatten(data),'provenance':provenance,'errors':errors[:50],
            'validation':check,'can_import':bool(data) and not errors and not check['errors']}
