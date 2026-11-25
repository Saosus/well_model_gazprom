"""Сравнение по индексам без округления перед проверкой допуска."""

from decimal import Decimal
from math import isfinite

FIELDS = ("q_liq", "p_wf")


class Report:
    def __init__(self):
        self.lines = ["# Проверка Well Model Builder · версия по базе знаний", ""]
        self.failed = 0
        self.passed = 0

    def section(self, name):
        self.lines.extend(["", f"## {name}", "", "| Проверка | Эталон | Факт | Отклонение | Итог |",
                           "| --- | --- | --- | --- | --- |"])

    def row(self, name, expected, actual, deviation, ok):
        self.failed += int(not ok)
        self.passed += int(ok)
        def escape(value):
            return str(value).replace("|", r"\|").replace("\n", " ")
        self.lines.append("| " + " | ".join(map(escape, (name, expected, actual, deviation,
                                                        "OK" if ok else "FAIL"))) + " |")

    def number(self, label, ref, fact, limit):
        if not all(type(v) in (float, int) and isfinite(v) for v in (ref, fact)):
            self.row(label, ref, fact, "требуется конечное число", False)
            return
        target, value = Decimal(str(ref)), Decimal(str(fact))
        delta = abs(value - target)
        if target == 0:
            deviation, ok = f"абс. {delta:.6g} (эталон 0)", delta == 0
        else:
            percent = 100 * delta / abs(target)
            deviation, ok = f"{percent:.7g} %", percent <= limit
        self.row(label, ref, fact, deviation, ok)


def compare(report, stage, request, expected, actual):
    limit = 10 if stage == "e2e" else 5
    label = stage.upper()
    if stage in ("ipr", "vlp"):
        count = request.get("points", 21)
        for field in FIELDS:
            ref, fact = expected[field], actual[field]
            if not isinstance(ref, list) or not isinstance(fact, list):
                raise ValueError(f"{field}: ожидаются массивы")
            if len(ref) != count or len(fact) != count:
                report.row(f"{label} {field}, длина", f"{count} по запросу, {len(ref)} в эталоне",
                           len(fact), "неполная сетка", False)
                continue
            for i, (a, b) in enumerate(zip(ref, fact, strict=True)):
                report.number(f"{label} {field}[{i}]", a, b, limit)
        return
    if stage == "nodal":
        if not all(isinstance(points, list) and len(points) == 1 for points in (expected, actual)):
            raise ValueError("Nodal: ожидается массив с одной рабочей точкой")
        expected, actual = expected[0], actual[0]
    else:
        actual = actual["operating_point"]
    for field in FIELDS:
        report.number(f"{label} {field}", expected[field], actual[field], limit)
