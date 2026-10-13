from ATRI.configs import Field, PluginConfig


class LKFarmConfig(PluginConfig):
    """
    lkfarm设置:
    """

    weather_forecast_group = Field(list, [], "天气预报订阅群", item_type=str)
    hour = Field(int, 8, "天气预报小时", min=0, max=23)
    minute = Field(int, 0, "天气预报分钟", min=0, max=59)
