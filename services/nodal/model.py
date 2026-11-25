"""Пересечение двух полилиний в координатах (дебит, давление)."""

from fractions import Fraction
from itertools import pairwise

from shared.contracts import Curve, NodalRequest, OperatingPoint

Point = tuple[float, float]
Segment = tuple[Point, Point]
ExactPoint = tuple[Fraction, Fraction]


def segments(curve: Curve) -> list[Segment]:
    # Соседство задаётся входной кривой. Упорядочиваем лишь концы каждого отрезка.
    points = zip(curve.q_liq, curve.p_wf, strict=True)
    return [(min(a, b), max(a, b)) for a, b in pairwise(points)]


def cross(ax: Fraction, ay: Fraction, bx: Fraction, by: Fraction) -> Fraction:
    return ax * by - ay * bx


def intersection(first: Segment, second: Segment) -> ExactPoint | None:
    a, b = first
    c, d = second
    if b[0] < c[0] or d[0] < a[0]:
        return None
    if max(min(a[1], b[1]), min(c[1], d[1])) > min(max(a[1], b[1]), max(c[1], d[1])):
        return None

    # Точная арифметика для переданных float исключает переполнение определителя
    # и различия в координатах общего узла при проверке соседних отрезков.
    ax, ay = map(Fraction, a)
    bx, by = map(Fraction, b)
    cx, cy = map(Fraction, c)
    dx, dy = map(Fraction, d)
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    ox, oy = cx - ax, cy - ay
    det = cross(rx, ry, sx, sy)
    t_num = cross(ox, oy, sx, sy)
    u_num = cross(ox, oy, rx, ry)
    if det == 0:
        if t_num != 0 or u_num != 0:
            return None
        # На общей прямой лексикографический порядок работает и по вертикали.
        start, end = max(a, c), min(b, d)
        if start < end:
            raise ValueError("Кривые совпадают на участке. Рабочая точка неоднозначна.")
        if start == end:
            return Fraction(start[0]), Fraction(start[1])
        return None

    t, u = t_num / det, u_num / det
    if 0 <= t <= 1 and 0 <= u <= 1:
        return ax + t * rx, ay + t * ry
    return None


def calculate_nodal(request: NodalRequest) -> OperatingPoint:
    found = None
    vlp_segments = segments(request.vlp)
    for inflow in segments(request.ipr):
        for lift in vlp_segments:
            point = intersection(inflow, lift)
            if point is None:
                continue
            if found is not None and point != found:
                raise ValueError("Кривые пересекаются в нескольких точках.")
            found = point
    if found is None:
        raise ValueError("Кривые IPR и VLP не пересекаются.")
    return OperatingPoint(q_liq=float(found[0]), p_wf=float(found[1]))
