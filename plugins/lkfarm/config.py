from ATRI.utils.model import BaseModel


class LKFarmConfig(BaseModel):
    """
    lkfarm设置:
    """
    weather_forecast_group: list[int] = []
    hour: int = 8
    minute: int = 0
