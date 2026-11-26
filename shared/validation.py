"""Field-addressed checks shared by the dashboard preview and calculation services."""
from bisect import bisect_left
from math import isfinite, ulp

from fastapi import HTTPException
from pydantic import ValidationError

from shared.contracts import CalculateRequest


def issue(paths, message, level="error"):
    return {"paths": paths, "message": message, "level": level}


def number(value):
    try:
        return type(value) in (float, int) and isfinite(value)
    except OverflowError:
        return False


def lookup(data, path):
    for key in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return float(data) if number(data) else None


def domain_issues(body, service):
    if not isinstance(body, dict) or service not in ("vlp", "backend"):
        return []
    dashboard = service == "backend"
    prefix = "well." if dashboard else ""
    well = body.get("well") if dashboard else body
    problems = []

    def add(paths, message, level="error"):
        problems.append(issue(paths, message, level))

    def geometry(paths, message):
        add([prefix + path for path in paths], message)

    casing, tubing, shoe, depth, grad, wh, rough = (
        lookup(well, key) for key in ("casing.d", "tubing.d", "tubing.h_mes", "h_res", "geo_grad", "p_wh", "roughness"))
    if None not in (casing, tubing) and casing <= tubing:
        geometry(["casing.d", "tubing.d"], "НКТ не помещается в колонне. Проверьте диаметры в метрах: диаметр ЭК должен быть больше диаметра НКТ.")
    if depth is not None and depth <= 0:
        geometry(["h_res"], "Пласт должен лежать ниже устья. Введите глубину по стволу больше 0 м.")
    if None not in (shoe, depth) and shoe >= depth:
        geometry(["tubing.h_mes", "h_res"], "Башмак НКТ должен находиться выше пласта. Укажите глубину спуска меньше глубины пласта.")
    survey = well.get("inclinometry") if isinstance(well, dict) else None
    survey = survey if isinstance(survey, dict) else {}
    md, tvd = (survey.get(k) if isinstance(survey.get(k), list) else [] for k in ("MD", "TVD"))
    clean = bool(md) and len(md) == len(tvd)

    def cell(col, i):
        return f"inclinometry.{col}.{i}"

    for i in range(max(len(md), len(tvd))):
        for col, values in (("MD", md), ("TVD", tvd)):
            path = cell(col, i)
            if i >= len(values):
                geometry([path], f"Строка {i+1}, {col}: добавьте парный замер в метрах.")
                continue
            value = values[i]
            if not number(value):
                clean = False
                continue
            if value < 0:
                clean = False
                geometry([path], f"Строка {i+1}, {col}: глубина отсчитывается от устья. Введите 0 м или больше.")
            if i and number(values[i-1]) and (value < values[i-1] or col == "MD" and value == values[i-1]):
                clean = False
                rule = "строго больше предыдущей" if col == "MD" else "не меньше предыдущей"
                geometry([cell(col, i-1), path], f"Строки {i}–{i+1}, {col}: проверьте замеры и их порядок. Следующая глубина должна быть {rule}.")
        if i >= min(len(md), len(tvd)):
            continue
        a, b = (md[i-1], tvd[i-1]) if i else (0, 0)
        if all(map(number, (a, b, md[i], tvd[i]))) and md[i] >= a:
            excess = (tvd[i]-b) - (md[i]-a)
            if excess > 4 * max(map(ulp, (a, b, md[i], tvd[i]))) or (i == 0 and md[i] == 0 and tvd[i] != 0):
                clean = False
                indices = [i-1, i] if i else [0]
                geometry([cell(col, j) for j in indices for col in ("MD", "TVD")],
                         f"Участок до строки {i+1}: перепад TVD больше длины по стволу. Сверьте MD и TVD от одной отметки устья (0, 0).")
    if md and all(map(number, md)) and depth is not None and depth > max(md):
        geometry(["h_res", cell("MD", md.index(max(md)))], "Замеры заканчиваются выше пласта. Проверьте глубину пласта или добавьте станцию с MD до пласта включительно.")

    grid_size = lookup(body, "sampling.vlp_points" if dashboard else "points")
    grid_max = lookup(body, "sampling.q_max" if dashboard else "q_max")
    if grid_size is None:
        grid_size = 21.0
    if grid_max is not None and grid_max > 0 and 2 <= grid_size <= 201 and grid_size.is_integer():
        # For subnormal steps NumPy scales unit fractions before multiplying.
        if grid_max / ulp(0.0) < grid_size - 1:
            grid_paths = ["sampling.q_max", "sampling.vlp_points"] if dashboard else ["q_max", "points"]
            add(grid_paths, "Шаг сетки меньше минимального представимого числа. Увеличьте максимальный дебит или уменьшите число точек VLP.")

    temp, water, oil, gas = (lookup(body, "pvt."+k) for k in ("t_res", "wct", "gamma_oil", "gamma_gas"))
    oil_present = water is not None and 0 <= water < 100
    temperatures = []
    if temp is not None:
        temperatures.append((temp, ["pvt.t_res"]))
    if clean and None not in (depth, grad, temp) and 0 < depth <= md[-1]:
        i = bisect_left(md, depth)
        a, b = (md[i-1], tvd[i-1]) if i else (0., 0.)
        vertical = b + ((depth-a)/(md[i]-a))*(tvd[i]-b)
        nodes = [i] if depth == md[i] or i == 0 else [i-1, i]
        paths = [prefix+"geo_grad", "pvt.t_res", prefix+"h_res"]
        paths += [prefix+cell(col, j) for j in nodes for col in ("MD", "TVD")]
        temperatures.append((temp - vertical*(grad/100), paths))
    for local, paths in temperatures:
        position = "на устье" if len(paths) > 1 else "в пласте"
        if not isfinite(local) or local <= -273.15:
            add(paths, f"Температура {position} вне физической области. Проверьте исходные данные: требуется конечное значение выше −273,15 °C.")
        elif oil_present and local*1.8+32 <= 0:
            add(paths+["pvt.wct"], f"Температура {position} слишком низка для вязкости нефти. При наличии нефти проверьте температурный профиль: требуется выше 0 °F (≈ −17,78 °C).")
        elif oil_present and local*1.8+32 > 295:
            add(paths, f"Температура {position} выше 295 °F (≈ 146,11 °C). Вязкость нефти рассчитывается за пределами диапазона базы знаний.", "warning")
    if oil_present and oil is not None and 0 < oil < 141.5/(58+131.5):
        add(["pvt.gamma_oil"], "Плотность соответствует более 58 °API. Проверьте относительную плотность нефти: оценка вязкости экстраполируется.", "warning")
    if oil_present and gas is not None and gas > 0 and wh is not None:
        pc = 756.8 - gas*(131.07+3.6*gas)
        tc = 169.2 + gas*(349.5-74*gas)
        if min(pc, tc) <= 0:
            level = "error" if wh > 1 else "warning"
            add(["pvt.gamma_gas", prefix+"p_wh"], "Проверьте плотность газа по воздуху. При давлении выше 1 атм формула Sutton требует положительных псевдокритических давления и температуры.", level)
        elif wh > 1:
            if wh*(101325/6894.757293168)/pc > 20:
                add([prefix+"p_wh", "pvt.gamma_gas"], "На устье приведённое давление выше 20. Вязкость газа по Dempsey экстраполируется.", "warning")
            for local, paths in temperatures:
                if not 1.2 <= (local*1.8+491.67)/tc <= 3:
                    place = "на устье" if len(paths) > 1 else "в пласте"
                    add(paths+["pvt.gamma_gas"], f"Приведённая температура {place} вне диапазона Dempsey 1,2–3. Оценка вязкости газа менее надёжна.", "warning")
    if rough is not None and rough >= 3.7:
        add([prefix+"roughness"], "При переходном или турбулентном течении Colebrook–White требует шероховатость меньше 3,7. В ламинарном режиме расчёт возможен.", "warning")
    return problems


def schema_issues(errors):
    result = []
    for e in errors:
        path = ".".join(str(x) for x in e["loc"] if x != "body")
        kind = e["type"]
        ctx = e.get("ctx", {})
        if kind == "value_error":
            msg = str(ctx.get("error", e["msg"]))
            # These legacy model checks are expanded into specific cell/field issues.
            if msg.startswith(("MD и TVD", "MD должна", "TVD не должна", "casing.d", "Требуется tubing.h_mes")):
                continue
        else:
            msg = {"missing": "Заполните обязательное поле.", "extra_forbidden": "Удалите неизвестное поле.",
                   "int_type": "Введите целое число от 2 до 201.", "too_short": "Добавьте недостающие значения.",
                   "list_type": "Передайте массив значений.", "json_invalid": "Проверьте синтаксис JSON.",
                   "model_type": "Передайте объект с параметрами."}.get(kind, "Введите конечное число.")
            if kind in ("float_type", "finite_number"):
                msg = "Требуется конечное число. Заполните поле значением в указанных единицах."
            for key, phrase in (("gt", "больше"), ("ge", "не меньше"), ("le", "не больше")):
                if key in ctx:
                    msg = f"Введите число {phrase} {ctx[key]:g}."
            if path.endswith("wct"):
                msg = "Введите долю воды от 0 до 100 % включительно."
            if path.endswith("points"):
                msg = "Введите целое число от 2 до 201 включительно."
        result.append(issue([path], msg))
    return result


def preview(body):
    try:
        CalculateRequest.model_validate(body)
        errors = []
    except ValidationError as exc:
        errors = schema_issues(exc.errors())
    return errors + domain_issues(body, "backend")


def detail(issues):
    return " | ".join(f"{path} — {i['message']}" for i in issues if i["level"] == "error" for path in i["paths"])


def enforce(body, service):
    message = detail(domain_issues(body, service))
    if message:
        raise HTTPException(422, message)
