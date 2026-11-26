"""JSON-контракты Well Model Builder. Давления — атм, дебиты — м³/сут."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False, extra="forbid")


Positive = Annotated[float, Field(gt=0)]
NonNegative = Annotated[float, Field(ge=0)]
WaterCut = Annotated[float, Field(ge=0, le=100, description="Обводнённость, %")]
PointCount = Annotated[int, Field(ge=2, le=201)]


class Health(ApiModel):
    status: Literal["ok"]
    service: str


class ApiError(ApiModel):
    detail: str


class Reservoir(ApiModel):
    p_res: Positive = Field(description="Пластовое давление, атм")
    pi: NonNegative = Field(description="Коэффициент продуктивности, м³/(сут·атм)")
    pb: NonNegative = Field(description="Давление насыщения для IPR, атм")


class IprRequest(Reservoir):
    model_config = ConfigDict(json_schema_extra={"examples": [{"p_res": 200, "wct": 50, "pi": 1, "pb": 150}]})
    wct: WaterCut
    points: PointCount = Field(default=21, description="Число точек равномерной сетки давления от 1 атм до p_res")


class Inclinometry(ApiModel):
    MD: list[float] = Field(min_length=1, description="Измеренная глубина, м")
    TVD: list[float] = Field(min_length=1, description="Вертикальная глубина, м")

    @model_validator(mode="after")
    def check_survey(self) -> Self:
        if len(self.MD) != len(self.TVD):
            raise ValueError("MD и TVD должны быть одной длины")
        if any(a >= b for a, b in zip(self.MD, self.MD[1:])):
            raise ValueError("MD должна строго возрастать")
        if any(a > b for a, b in zip(self.TVD, self.TVD[1:])):
            raise ValueError("TVD не должна убывать")
        return self


class Casing(ApiModel):
    d: Positive = Field(description="Внутренний диаметр, м")


class Tubing(Casing):
    h_mes: NonNegative = Field(description="Глубина спуска НКТ по стволу, м")


class Well(ApiModel):
    inclinometry: Inclinometry
    casing: Casing
    tubing: Tubing
    p_wh: Positive = Field(description="Устьевое давление, атм")
    geo_grad: float = Field(description="Геотермический градиент, °C/100 м")
    h_res: float = Field(description="Глубина пласта по стволу, м")
    roughness: Positive = Field(default=0.0001, description="Относительная шероховатость")

    @model_validator(mode="after")
    def check_geometry(self) -> Self:
        if self.casing.d <= self.tubing.d:
            raise ValueError("casing.d должен быть больше tubing.d")
        if not self.tubing.h_mes < self.h_res <= self.inclinometry.MD[-1]:
            raise ValueError("Требуется tubing.h_mes < h_res <= max(MD)")
        return self


class Fluid(ApiModel):
    wct: WaterCut
    rp: NonNegative = Field(description="Газовый фактор, м³ газа при 20 °C и 1 атм на тонну нефти")
    gamma_oil: Positive = Field(description="Относительная плотность товарной нефти по воде")
    gamma_gas: Positive = Field(description="Относительная плотность газа по воздуху")
    gamma_wat: Positive = Field(description="Относительная плотность воды")
    t_res: float = Field(gt=-273.15, description="Пластовая температура, °C")


class VlpRequest(Well):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "inclinometry": {"MD": [0, 1000, 1500], "TVD": [0, 1000, 1100]},
        "casing": {"d": 0.1},
        "tubing": {"d": 0.062, "h_mes": 1000},
        "pvt": {"wct": 50, "rp": 100, "gamma_oil": 0.8, "gamma_gas": 0.7, "gamma_wat": 1.0, "t_res": 90},
        "p_wh": 10,
        "geo_grad": 3,
        "h_res": 1500,
        "roughness": 0.0001,
        "q_max": 400,
        "points": 21,
    }]})

    pvt: Fluid
    q_max: Positive = Field(default=400, description="Максимальный дебит, м³/сут")
    points: PointCount = 21


class Curve(ApiModel):
    q_liq: list[NonNegative] = Field(min_length=2, description="Дебит жидкости, м³/сут")
    p_wf: list[Positive] = Field(min_length=2, description="Забойное давление, атм")

    @model_validator(mode="after")
    def check_lengths(self) -> Self:
        if len(self.q_liq) != len(self.p_wf):
            raise ValueError("q_liq и p_wf должны быть одной длины")
        return self


class NodalRequest(ApiModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "ipr": {"q_liq": [2, 0], "p_wf": [1, 3]},
        "vlp": {"q_liq": [0, 2], "p_wf": [1, 3]},
    }]})

    ipr: Curve
    vlp: Curve


class OperatingPoint(ApiModel):
    q_liq: NonNegative = Field(description="Дебит жидкости, м³/сут")
    p_wf: Positive = Field(description="Забойное давление, атм")


NodalResult = Annotated[list[OperatingPoint], Field(min_length=1, max_length=1)]


class Sampling(ApiModel):
    ipr_points: PointCount = 21
    vlp_points: PointCount = 21
    q_max: Positive = Field(default=400, description="Верхняя граница сетки VLP, м³/сут")


class CalculateRequest(ApiModel):
    reservoir: Reservoir
    well: Well
    pvt: Fluid
    sampling: Sampling = Field(default_factory=Sampling)


class CalculateResponse(NodalRequest):
    operating_point: OperatingPoint
