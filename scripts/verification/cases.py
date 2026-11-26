"""Контрольные задачи без импорта расчётного кода и адаптер данных организаторов."""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("ipr", "vlp", "nodal", "e2e")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def well_request(ipr, vlp):
    if ipr["wct"] != vlp["pvt"]["wct"]:
        raise ValueError("Разная обводнённость IPR и VLP для сквозного расчёта")
    return {
        "reservoir": {key: ipr[key] for key in ("p_res", "pi", "pb")},
        "well": {key: value for key, value in vlp.items() if key not in ("q_max", "points", "pvt")},
        "pvt": vlp["pvt"],
        "sampling": {"ipr_points": ipr.get("points", 21), "vlp_points": vlp.get("points", 21),
                     "q_max": vlp.get("q_max", 400)},
    }


def water_cases():
    # Для выбранной геометрии и q <= 4 м³/сут даже в НКТ Re < 1000.
    ipr = {"wct": 100, "p_res": 30, "pb": 15, "pi": 0.1, "points": 6}
    vlp = read_json(ROOT / "examples/vlp.json")
    vlp["pvt"]["wct"] = 100
    vlp.update(inclinometry={"MD": [0, 100, 150], "TVD": [0, 100, 110]},
               tubing={"d": 0.062, "h_mes": 100}, h_res=150, q_max=4, points=5)
    # Гидростатика и закон Пуазейля на 100 м НКТ и 50 м обсадной колонны.
    static = 10 + 1000 * 9.80665 * 110 / 101325
    slope = 128 * 0.001 / (math.pi * 86400 * 101325) * (100 / 0.062**4 + 50 / 0.1**4)
    pressures = [1 + i * 29 / 5 for i in range(6)]
    rates = [0, 1, 2, 3, 4]
    q_star = (30 - static) / (10 + slope)
    return {
        "ipr": (ipr, {"q_liq": [(30 - p) / 10 for p in pressures], "p_wf": pressures}),
        "vlp": (vlp, {"q_liq": rates, "p_wf": [static + slope * q for q in rates]}),
        "nodal": (read_json(ROOT / "examples/nodal.json"), [{"q_liq": 1, "p_wf": 2}]),
        "e2e": (well_request(ipr, vlp), {"q_liq": q_star, "p_wf": static + slope * q_star}),
    }


def reference_cases(manifest_path):
    manifest = read_json(manifest_path)
    result = {}
    for stage in STAGES:
        spec = manifest[stage]
        if stage == "e2e" and "request" not in spec:
            request = well_request(result["ipr"][0], result["vlp"][0])
        else:
            request = read_json(manifest_path.parent / spec["request"])
        expected = read_json(manifest_path.parent / spec["expected"])
        if stage == "e2e":
            if isinstance(expected, list) and len(expected) == 1:
                expected = expected[0]
            elif isinstance(expected, dict) and "operating_point" in expected:
                expected = expected["operating_point"]
        result[stage] = request, expected
    return result
