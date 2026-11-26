"""Проверить модели, поднять четыре отдельных HTTP-процесса и записать отчёт."""

import argparse
import copy
from pathlib import Path
import sys
import unittest

import httpx

from scripts.verification.cases import ROOT, read_json, reference_cases, water_cases, well_request
from scripts.verification.http_stack import running_stack
from scripts.verification.report import Report, compare


def post(client, urls, stage, payload):
    url = urls["backend"] + "/api/calculate" if stage == "e2e" else urls[stage] + f"/{stage}/calc"
    response = client.post(url, json=payload)
    if response.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ValueError(f"{stage}: ожидался application/json, получен HTTP {response.status_code}")
    return response.status_code, response.json()


def calculate(client, urls, stage, payload):
    status, body = post(client, urls, stage, payload)
    if status != 200:
        raise ValueError(f"{stage}: HTTP {status}, {body}")
    return body


def check_cases(report, client, urls, cases):
    for stage, (req, expected) in cases.items():
        try:
            actual = calculate(client, urls, stage, req)
            compare(report, stage, req, expected, actual)
        except (KeyError, TypeError, ValueError, httpx.HTTPError) as exc:
            report.row(stage.upper(), "полный JSON и HTTP 200", str(exc), "ошибка сверки", False)


def check_behavior(report, client, urls):
    report.section("Смешанная скважина · связь сервисов и повторяемость")
    vlp = read_json(ROOT / "examples/vlp.json")
    ipr = {"p_res": 200, "pi": 1, "pb": 150, "wct": vlp["pvt"]["wct"]}
    well = well_request(ipr, vlp)
    curves = {s: calculate(client, urls, s, req) for s, req in (("ipr", ipr), ("vlp", vlp))}
    direct = calculate(client, urls, "nodal", curves)
    result = calculate(client, urls, "e2e", well)
    for key, curve in curves.items():
        equal = result[key] == curve
        report.row(f"E2E {key.upper()}", "все значения прямого вызова",
                   "кривые совпали" if equal else "различаются", "—", equal)
    for field in ("q_liq", "p_wf"):
        ref, actual = direct[0][field], result["operating_point"][field]
        report.row(f"E2E {field}", ref, actual, abs(actual - ref), actual == ref)
    repeat = calculate(client, urls, "e2e", well)
    report.row("Повтор того же POST", "одинаковый JSON", repeat == result, "—", repeat == result)

    changed = copy.deepcopy(well)
    changed["well"]["p_wh"] = 20
    point = calculate(client, urls, "e2e", changed)["operating_point"]
    for field, direction in (("q_liq", "меньше"), ("p_wf", "больше")):
        base = result["operating_point"][field]
        ok = point[field] < base if field == "q_liq" else point[field] > base
        report.row(f"При Pу = 20 атм, {field}", f"{direction} {base:.8f}", point[field], "—", ok)

    report.section("Границы и ошибки по HTTP")
    shut = calculate(client, urls, "ipr", {**ipr, "pi": 0, "points": 201})
    report.row("IPR, PI = 0 и 201 точка", "201 нулевой дебит", f"{shut['q_liq'].count(0)} нулей",
               "—", shut["q_liq"] == [0] * 201)
    apart = {"ipr": {"q_liq": [0, 10], "p_wf": [4, 3]},
             "vlp": {"q_liq": [0, 10], "p_wf": [5, 6]}}
    changed["well"]["p_wh"] = 210
    for label, stage, payload, fragment in [
        ("IPR, обводнённость 101 %", "ipr", {**ipr, "wct": 101}, "wct"),
        ("VLP, одинаковые диаметры", "vlp", {**vlp, "casing": {"d": vlp["tubing"]["d"]}}, "casing.d"),
        ("Nodal, пустой запрос", "nodal", {}, "ipr"),
        ("Nodal, нет пересечения", "nodal", apart, "не пересекаются"),
        ("E2E, нет пересечения", "e2e", changed, "не пересекаются"),
        ("E2E, пустой запрос", "e2e", {}, "reservoir"),
    ]:
        status, body = post(client, urls, stage, payload)
        detail = body.get("detail", "") if isinstance(body, dict) else ""
        ok = 400 <= status < 500 and isinstance(detail, str) and fragment in detail
        report.row(label, f"4xx JSON, «{fragment}»", f"HTTP {status} · {detail}", "—", ok)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, default=ROOT / "test_data/well_01/manifest.json", help="путь к карте оригинальных JSON для well_01")
    parser.add_argument("--report", type=Path, default=Path("accuracy-report.md"), help="путь к отчёту")
    args = parser.parse_args()
    report = Report()
    report.section("Автотесты проекта")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    tests = unittest.TextTestRunner(verbosity=1).run(suite)
    report.row("unittest", "все тесты пройдены", f"{tests.testsRun} тестов",
               f"провалов {len(tests.failures)}, ошибок {len(tests.errors)}", tests.wasSuccessful())
    try:
        with running_stack() as (client, urls):
            report.section("Собственные аналитические эталоны · IPR, VLP, Nodal ≤ 5 %, E2E ≤ 10 %")
            check_cases(report, client, urls, water_cases())
            check_behavior(report, client, urls)
            report.section("Эталонные данные · " + args.references.parent.name)
            check_cases(report, client, urls, reference_cases(args.references))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, httpx.HTTPError) as exc:
        report.row("Запуск проверки", "выполнение всех проверок", str(exc), "ошибка", False)
    report.lines.extend(["", f"Строк OK: {report.passed}. Строк FAIL: {report.failed}.", ""])
    args.report.write_text("\n".join(report.lines), encoding="utf-8")
    print(f"OK: {report.passed}, FAIL: {report.failed}. Отчёт: {args.report.resolve()}")
    return int(report.failed != 0)


if __name__ == "__main__":
    sys.exit(main())
