"""Deterministic grounding, unit normalization and diagnostics for the assistant.
The calculation service contracts are intentionally unchanged.
"""
import json
import math
import re

from pydantic import ValidationError
from shared.contracts import CalculateRequest
from shared.validation import domain_issues, schema_issues

# (human label, canonical unit, accepted label expressions)
SPEC = {
    'reservoir.p_res': ('Пластовое давление', 'атм', r'пластов\w*\s+давлен\w*|давлен\w*\s+пласта|p[._ ]?пл'),
    'reservoir.pi': ('Коэффициент продуктивности', 'м³/(сут·атм)', r'коэффициент\w*\s+продуктивност\w*|индекс\s+продуктивност\w*'),
    'reservoir.pb': ('Давление насыщения', 'атм', r'давлен\w*\s+насыщен\w*(?:\s+нефти(?:\s+газом)?)?'),
    'well.inclinometry.MD': ('Глубины MD', 'м', r'измеренн\w*\s+глубин\w*|глубин\w*\s+по\s+стволу(?:\s+MD)?'),
    'well.inclinometry.TVD': ('Глубины TVD', 'м', r'вертикальн\w*\s+глубин\w*'),
    'well.casing.d': ('Диаметр ЭК', 'м', r'(?:внутренн\w*\s+)?диаметр\w*\s+(?:эк|эксплуатационн\w*\s+колонн\w*|обсадн\w*\s+колонн\w*)|эк\s+диаметр'),
    'well.tubing.d': ('Диаметр НКТ', 'м', r'(?:внутренн\w*\s+)?диаметр\w*\s+(?:нкт|насосно-компрессорн\w*\s+труб\w*)|нкт\s+диаметр'),
    'well.tubing.h_mes': ('Глубина спуска НКТ', 'м', r'глубин\w*\s+(?:спуска|подвески|установки)\s+нкт|нкт\s+спущен\w*\s+на|длин\w*\s+нкт'),
    'well.p_wh': ('Устьевое давление', 'атм', r'устьев\w*\s+давлен\w*|давлен\w*\s+на\s+устье'),
    'well.geo_grad': ('Геотермический градиент', '°C/100 м', r'геотермическ\w*\s+градиент\w*|температурн\w*\s+градиент\w*'),
    'well.h_res': ('Глубина пласта', 'м', r'глубин\w*\s+(?:пласта|забоя)'),
    'well.roughness': ('Относительная шероховатость', 'доля', r'(?:относительн\w*\s+)?шероховатост\w*'),
    'pvt.wct': ('Обводнённость', '%', r'обводн[её]нност\w*|содержан\w*\s+воды'),
    'pvt.rp': ('Газовый фактор', 'м³/т', r'газов\w*\s+фактор\w*'),
    'pvt.gamma_oil': ('Относительная плотность нефти', 'доля', r'(?:относительн\w*\s+)?плотност\w*\s+нефти'),
    'pvt.gamma_gas': ('Относительная плотность газа', 'доля', r'(?:относительн\w*\s+)?плотност\w*\s+газа'),
    'pvt.gamma_wat': ('Относительная плотность воды', 'доля', r'(?:относительн\w*\s+)?плотност\w*\s+воды'),
    'pvt.t_res': ('Пластовая температура', '°C', r'пластов\w*\s+температур\w*|температур\w*\s+(?:в\s+)?пласт[ае]'),
    'sampling.ipr_points': ('Число точек IPR', 'целое', r'(?:число|количество)\s+точек\s+ipr|точек\s+ipr'),
    'sampling.vlp_points': ('Число точек VLP', 'целое', r'(?:число|количество)\s+точек\s+vlp|точек\s+vlp'),
    'sampling.q_max': ('Максимальный дебит VLP', 'м³/сут', r'максимальн\w*\s+дебит\w*(?:\s+vlp)?|верхн\w*\s+границ\w*\s+дебита'),
}
FIELDS = {p: v[1] for p, v in SPEC.items()}
DEFAULTS = {'well.roughness': .0001, 'sampling.ipr_points': 21, 'sampling.vlp_points': 21, 'sampling.q_max': 400}
ARRAYS = {'well.inclinometry.MD', 'well.inclinometry.TVD'}
NUM = r'[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?'
# longest labels first; explicit API names also accepted in text
LABELS = re.compile('|'.join(rf'(?P<f{i}>(?<![\w.])(?:{re.escape(p)}|{re.escape(p.split(".")[-1]) + "|" if p.split(".")[-1] != "d" else re.escape(p.removeprefix("well.")) + "|"}{v[2]})(?!\w))' for i,(p,v) in enumerate(SPEC.items())), re.I)
PATHS = list(SPEC)

def label(path):
    return SPEC.get(path, (path, '', ''))[0]

def issue(path, message, **kw):
    return {'path':path, 'message':message, **kw}

def flatten(obj, prefix=''):
    result = {}
    for k,v in obj.items():
        p = f'{prefix}.{k}' if prefix else k
        if isinstance(v,dict) and v: result.update(flatten(v,p))
        else: result[p] = v
    return result

def unflatten(data):
    result = {}
    for p,v in data.items():
        node = result
        parts = p.split('.')
        for part in parts[:-1]: node = node.setdefault(part,{})
        node[parts[-1]] = v
    return result

def valid_value(path, v):
    def number(x): return type(x) in (int,float) and abs(x) <= 1e308 and math.isfinite(x)
    return (isinstance(v,list) and 0 < len(v) <= 2000 and all(number(x) for x in v)) if path in ARRAYS else number(v)

def norm_unit(s):
    s = s.casefold().replace('ё','е').replace('³','3').replace('²','2').replace('º','°').replace('·','*').replace('⋅','*').replace('–','-')
    s = re.sub(r'\s+', '', s).strip('.,;:!')
    return s

PRESSURE = {'атм':1, 'atm':1, 'атмосфер':1, 'атмосферы':1, 'атмосфера':1, 'атмосферах':1, 'мпа':1e6/101325, 'mpa':1e6/101325, 'кпа':1000/101325, 'kpa':1000/101325, 'па':1/101325, 'pa':1/101325, 'бар':1e5/101325, 'bar':1e5/101325, 'psi':6894.757293168/101325, 'psia':6894.757293168/101325}
LENGTH = {'м':1,'m':1,'метр':1,'метра':1,'метров':1,'мм':.001,'mm':.001,'см':.01,'cm':.01,'км':1000,'km':1000}

def normalize(path, value, unit, canonical=False):
    """No LLM arithmetic. No assumed units for prose, except dimensionless values."""
    u = norm_unit(unit)
    target = FIELDS[path]
    def scale(f): return [x*f for x in value] if isinstance(value,list) else value*f
    if not u and (canonical or target in ('доля','целое')): out = value
    elif target == 'атм' and u in PRESSURE: out = scale(PRESSURE[u])
    elif target == 'м' and u in LENGTH: out = scale(LENGTH[u])
    elif target == '%' and u in ('%', 'процент','процента','процентов'): out = value
    elif target == '%' and u in ('доля','доли','долях','д.ед.','долиед'): out = scale(100)
    elif target == 'доля' and u in ('доля','доли','долях','д.ед','долиед','ед','безразмерная'): out = value
    elif target == 'доля' and u == '%': out = scale(.01)
    elif target == '°C' and u in ('°c','°с','c','с','градусовцельсия','градусацельсия'): out = value
    elif target == '°C' and u in ('k','к'): out = value-273.15
    elif target == '°C' and u in ('°f','f'): out = (value-32)*5/9
    elif target == '°C/100 м' and u in ('°c/100м','°с/100м','c/100m','°c/100m','к/100м','k/100m'): out = value
    elif target == '°C/100 м' and u in ('°c/м','°с/м','°c/m','к/м','k/m'): out = scale(100)
    elif target == '°C/100 м' and u in ('°c/км','°с/км','°c/km','к/км','k/km'): out = scale(.1)
    elif target == 'м³/т' and u in ('м3/т','m3/t','м3/тонну','м3/тонна'): out = value
    elif target == 'м³/сут' and u in ('м3/сут','м3/сутки','м3/день','m3/day','m3/d'): out = value
    elif target == 'м³/сут' and u in ('м3/ч','m3/h'): out = scale(24)
    elif target == 'м³/(сут·атм)' and u in ('м3/(сут*атм)','м3/сут/атм','m3/(d*atm)','м3/(сутатм)','м3/(сут*atm)'): out = value
    elif target == 'м³/(сут·атм)' and u in ('м3/(сут*мпа)','м3/сут/мпа','m3/(d*mpa)'): out = scale(.101325)
    elif target == 'целое' and u in ('точек','точки','точка','шт'): out = value
    else: raise ValueError(f'Уточните единицы: ожидаются {target}. Получено: {unit or "не указаны"}.')
    if target == 'целое' and type(out) in (int,float) and float(out).is_integer(): out = int(out)
    if not valid_value(path,out): raise ValueError('Требуется конечное число или массив чисел для инклинометрии.')
    return out

# Units are parsed as a whole; trailing arbitrary prose is not silently treated as a unit.
UNITS = sorted(set(PRESSURE)|set(LENGTH)|{'%','процент','процента','процентов','доля','доли','долях','д.ед','долиед','ед','°C','°С','°F','K','К','C','С','F','градусов Цельсия','градуса Цельсия','°C/100 м','°С/100 м','°C/100 m','C/100m','К/100 м','K/100m','°C/м','°С/м','°C/m','К/м','K/m','°C/км','°С/км','°C/km','К/км','K/km','м³/т','м3/т','m3/t','м³/тонну','м³/сут','м3/сут','м³/сутки','м3/сутки','м³/день','m3/day','m3/d','м³/ч','м3/ч','m3/h','м³/(сут·атм)','м3/(сут*атм)','м³/(сут*атм)','м³/сут/атм','м3/сут/атм','m3/(d*atm)','м³/(сут атм)','м³/(сут·МПа)','м3/(сут*мпа)','м³/сут/МПа','m3/(d*mpa)','точек','точки','точка','шт'},key=len, reverse=True)
UNIT_RE = re.compile(r'^(?:'+'|'.join(re.escape(x) for x in UNITS)+r')(?![\w/])',re.I)
LEAD = re.compile(r'^\s*(?:(?:равн[оаы]|составляет|будет|до|на уровне)\s+|[:=—–]\s*|-(?=\s)\s*)*',re.I)

def parse_value(path, tail, canonical=False):
    tail = LEAD.sub('',tail,count=1)
    if path in ARRAYS:
        m = re.match(r'\[([^\]]+)\]',tail)
        if not m: raise ValueError('Укажите массив глубин в квадратных скобках, например [0, 1000, 1500] м.')
        inside=m.group(1)
        parts = re.split(r'\s*;\s*',inside) if ';' in inside else re.split(r'\s*,\s*',inside)
        if not all(re.fullmatch(NUM,x.strip()) for x in parts): raise ValueError('Массив должен содержать числа.')
        value = [float(x.replace(',','.')) for x in parts]
    else:
        m = re.match(NUM,tail)
        if not m: raise ValueError('Укажите конкретное числовое значение.')
        value = float(m.group().replace(',','.'))
    rest=tail[m.end():].lstrip()
    u=UNIT_RE.match(rest)
    unit=u.group() if u else ''
    remainder=rest[len(unit):].lstrip()
    if re.match(r'(?:изб\w*|gauge|psig|±|(?:или|либо|от|до)\s*[-+]?\d|[-–]\s*\d|/)',remainder,re.I):
        raise ValueError('Неоднозначное значение или вид давления. Укажите одно абсолютное значение в единицах поля.')
    if not unit and rest and re.match(r'[%°\w/]',rest) and not re.match(r'(?:и|а|затем|далее|пожалуйста|используй)\b',rest,re.I):
        raise ValueError(f'Неизвестная единица измерения. Ожидается {FIELDS[path]}.')
    return normalize(path,value,unit,canonical), unit

def parse_text(text):
    found, problems, evidence = {}, {}, {}
    matches=list(LABELS.finditer(text))
    for i,m in enumerate(matches):
        path=PATHS[int(m.lastgroup[1:])]
        tail=text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)]
        # Ignore theoretical mentions but block explicit ambiguous assignments.
        stripped=LEAD.sub('',tail,count=1)
        if not re.match(r'\[|'+NUM,stripped):
            if re.match(r'(?:не|от|около|примерно|порядка|~)\s*'+NUM,stripped,re.I):
                problems[path]=issue(path,'Неоднозначное или приблизительное значение. Укажите точное число.'); found.pop(path,None); evidence.pop(path,None)
            continue
        try:
            v,unit=parse_value(path,tail,canonical=False)
            if path in found and found[path] != v:
                raise ValueError('Указаны разные значения одного параметра. Уточните, какое использовать.')
            if path not in problems:
                found[path]=v
                evidence[path]={'source':text[m.start():m.end()+len(tail)].strip(' ,;\n'), 'unit':unit or FIELDS[path], 'value':v}
        except ValueError as exc:
            problems[path]=issue(path,str(exc)); found.pop(path,None); evidence.pop(path,None)
    return found,problems,evidence

def parse_json(text):
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d: raise ValueError(f'Повтор ключа JSON: {k}.')
            d[k]=v
        return d
    obj=json.loads(text,object_pairs_hook=unique,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('NaN/Inf недопустимы.')))
    if not isinstance(obj,dict) or not obj: raise ValueError('Нужен непустой JSON-объект.')
    if set(obj)<= {'p_res','pi','pb','wct','points'} and set(obj)&{'p_res','pi','pb'}:
        obj={'ipr':obj}
    elif set(obj)&{'inclinometry','casing','tubing','p_wh','h_res','geo_grad','q_max','roughness'}:
        obj={'vlp':obj}
    if set(obj)<= {'ipr','vlp'}:
        fields=[]
        for section, val in obj.items():
            if not isinstance(val,dict): raise ValueError('IPR/VLP должны быть объектами.')
            for p,v in flatten(val).items():
                if section=='ipr': dest={'p_res':'reservoir.p_res','pi':'reservoir.pi','pb':'reservoir.pb','wct':'pvt.wct','points':'sampling.ipr_points'}.get(p)
                else: dest=({'points':'sampling.vlp_points','q_max':'sampling.q_max'}.get(p) or (p if p.startswith('pvt.') else 'well.'+p))
                fields.append((dest,v,section+'.'+p))
    else: fields=[(p,v,p) for p,v in flatten(obj).items()]
    found,problems,evidence={}, {}, {}
    for p,v,src in fields:
        if p not in FIELDS: raise ValueError(f'Неизвестное поле JSON: {src}.')
        if p in ARRAYS and isinstance(v, list) and 0 < len(v) <= 2000:
            # Keep station positions so the shared validator can address each bad cell.
            v = [x if valid_value('well.h_res', x) else None for x in v]
        elif not valid_value(p, v):
            problems[p] = issue(p, 'Требуется конечное число или числовой массив.')
            continue
        if p in found and found[p]!=v: problems[p]=issue(p,'Противоречивые значения в IPR и VLP. Уточните параметр.'); found.pop(p,None); evidence.pop(p,None); continue
        if p not in problems: found[p]=v; evidence[p]={'source':src,'unit':FIELDS[p],'value':v}
    return found,problems,evidence




def validation(data, pending=None):
    errors=list((pending or {}).values()); warnings=[]
    def add(p,msg,**kw):
        item=issue(p,msg,**kw)
        if item not in errors: errors.append(item)
    def warn(p,msg,**kw): warnings.append(issue(p,msg,**kw))
    missing=[p for p in FIELDS if p not in data and p not in DEFAULTS]
    defaults={p:v for p,v in DEFAULTS.items() if p not in data}
    body = unflatten(data)
    issues = domain_issues(body, "backend")
    try:
        request = CalculateRequest.model_validate(body)
    except ValidationError as exc:
        request = None
        issues += schema_issues([e for e in exc.errors() if e["type"] != "missing"])
    for item in issues:
        for field in item["paths"]:
            path, _, index = field.rpartition(".")
            where = {"row": int(index) + 1} if index.isdecimal() else {}
            report = issue(path if where else field, item["message"], **where)
            target = errors if item["level"] == "error" else warnings
            if report not in target:
                target.append(report)
    def has(*paths):
        return all(p in data for p in paths)
    if has('reservoir.p_res','reservoir.pb') and data['reservoir.pb']>data['reservoir.p_res']: warn('reservoir.pb','Давление насыщения выше пластового: текущая IPR ограничивает границу насыщения пластовым давлением.')
    if has('well.p_wh','reservoir.p_res') and data['well.p_wh']>=data['reservoir.p_res']: warn('well.p_wh','Устьевое давление не ниже пластового; рабочая точка может отсутствовать.')
    return {'valid':not missing and not defaults and not errors and request is not None,'missing':missing,'defaults_requiring_consent':defaults,'errors':errors,'warnings':warnings},request
