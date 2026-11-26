"""Black-Oil для VLP. Одна точка ствола, абсолютное давление в атм и температура в °C."""

from .model import PvtState, calculate_pvt

__all__ = ["PvtState", "calculate_pvt"]
