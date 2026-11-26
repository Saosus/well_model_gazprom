"""Rebuild the versioned local knowledge index from the supplied original documents.
Run from the repository root: python -m scripts.build_knowledge
The paragraph selection is curated; review it if the source document changes.
"""
import json
import re
import zipfile
from pathlib import Path
from defusedxml import ElementTree as ET
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/'frontend/knowledge'
chunks=[]
def add(title,location,url,text):
    text=re.sub(r'\s+',' ',text).strip()
    if text: chunks.append(dict(title=title,location=location,url=url,text=text))
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
with zipfile.ZipFile(DIR/'methodology.docx') as z:
    body=ET.fromstring(z.read('word/document.xml')).find('w:body',ns)
paragraphs=[''.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in body.findall('w:p',ns)]
for a,b,section in [(9,11,'Введение: пластовая энергия'),(12,13,'Введение: узловой анализ'),(20,25,'§1.1 VLP'),(26,26,'§1.1 Исходные данные VLP'),(37,39,'§1.2.1 PVT'),(43,49,'§1.2.1 Давление насыщения'),(53,57,'§1.2.1 Растворимость газа'),(71,72,'§1.2.2 Потери давления'),(88,89,'§1.2.2 Режимы течения'),(103,108,'§1.2.2 Инклинометрия'),(114,114,'§2 IPR'),(117,119,'§2.1 Коэффициент продуктивности'),(127,127,'§2.1 Индикаторная диаграмма'),(131,135,'§2.1 Применимость линейной модели'),(142,143,'§2.2 Метод Воглея'),(151,151,'§2.2 Линейный и нелинейный участки')]:
    add('База знаний — Разработка модели скважины',f'{section}, абзацы {a}–{b}', '/knowledge/methodology.docx',' '.join(p for p in paragraphs[a-1:b] if not p.startswith('Рисунок')))
p=PdfReader(DIR/'specification.pdf')
for pg,section in [(3,'§1 Постановка задачи'),(7,'§5.2 IPR: контракт и ограничения'),(8,'§5.3 VLP: контракт и ограничения'),(9,'§5.4 Nodal'),(10,'§6.4 ИИ-ассистент'),(12,'§7 Расчётные корреляции')]:
    text=p.pages[pg-1].extract_text()
    for start in range(0,len(text),1300):
        add('Техническое задание 3.1',f'{section}, стр. {pg}',f'/knowledge/specification.pdf#page={pg}',text[start:start+1450])
(ROOT/'services/backend/assistant/knowledge_index.json').write_text(json.dumps(chunks,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Фрагментов: {len(chunks)}')
