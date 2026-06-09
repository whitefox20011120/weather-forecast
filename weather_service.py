"""天气服务模块

封装高德地图天气与行政区划相关请求。
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any

import httpx

logger = logging.getLogger("plugin.weather_forecast.service")

AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"


class WeatherService:
    """高德地图天气服务封装"""

    def __init__(self, amap_key: str, timeout: float = 10.0):
        self.amap_key = amap_key
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.amap_key) and self.amap_key != "your_amap_key_here"

    async def _request_district(self, keyword: str, subdistrict: str = "1") -> Optional[Dict[str, Any]]:
        params = {
            "key": self.amap_key,
            "keywords": keyword,
            "subdistrict": subdistrict,
            "extensions": "base",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(AMAP_DISTRICT_URL, params=params)
                data = response.json()
                if data.get("status") == "1" and data.get("districts"):
                    return data
                logger.warning(f"行政区划查询无结果({keyword}): {data.get('info', 'Unknown')}")
                return None
        except Exception as e:
            logger.error(f"请求行政区划API失败({keyword}): {e}")
            return None

    async def get_sub_cities(self, city_code: str) -> Optional[List[Dict[str, str]]]:
        """获取省/市下属城市列表 (adcode 和 name)"""
        data = await self._request_district(city_code)
        if not data:
            return None

        parent = data["districts"][0]
        sub_districts = parent.get("districts", [])
        cities = [
            {"adcode": d["adcode"], "name": d["name"]}
            for d in sub_districts if d.get("level") == "city"
        ]
        if cities:
            return cities
        # 配置的本身就是市级或区县，直接返回自身
        return [{"adcode": parent["adcode"], "name": parent["name"]}]

    async def resolve_city(self, name: str) -> Optional[Dict[str, str]]:
        """根据城市/地区名解析adcode"""
        data = await self._request_district(name, subdistrict="0")
        if not data:
            return None
        for d in data["districts"]:
            if d.get("adcode"):
                return {"adcode": d["adcode"], "name": d.get("name", name), "level": d.get("level", "")}
        return None

    async def get_forecast(self, city_adcode: str) -> Optional[Dict[str, Any]]:
        """获取未来几天的天气预报"""
        params = {
            "key": self.amap_key,
            "city": city_adcode,
            "extensions": "all",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(AMAP_WEATHER_URL, params=params)
                data = response.json()
                if data.get("status") == "1" and data.get("forecasts"):
                    return data["forecasts"][0]
                return None
        except Exception as e:
            logger.error(f"请求天气预报API失败({city_adcode}): {e}")
            return None

    async def get_live(self, city_adcode: str) -> Optional[Dict[str, Any]]:
        """获取实时天气"""
        params = {
            "key": self.amap_key,
            "city": city_adcode,
            "extensions": "base",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(AMAP_WEATHER_URL, params=params)
                data = response.json()
                if data.get("status") == "1" and data.get("lives"):
                    return data["lives"][0]
                return None
        except Exception as e:
            logger.error(f"请求实时天气API失败({city_adcode}): {e}")
            return None

    async def get_region_summary(self, city_code: str, region_name: str = "") -> Optional[str]:
        """聚合省/市下属城市天气，生成多行汇总文本"""
        cities = await self.get_sub_cities(city_code)
        if not cities:
            return None

        weather_lines: List[str] = []
        today_date = ""
        for city_info in cities:
            forecast = await self.get_forecast(city_info["adcode"])
            if not forecast:
                continue
            casts = forecast.get("casts", [])
            if not casts:
                continue
            today = casts[0]
            if not today_date:
                today_date = today.get("date", "")

            city_name = forecast.get("city", city_info["name"])
            line = (
                f"{city_name}：{today.get('dayweather', '')}/{today.get('nightweather', '')}，"
                f"{today.get('nighttemp', '')}~{today.get('daytemp', '')}℃，"
                f"{today.get('daywind', '')}风{today.get('daypower', '')}级"
            )
            weather_lines.append(line)
            await asyncio.sleep(0.2)

        if not weather_lines:
            return None

        header = f"{region_name or '该地区'}各地天气（{today_date}）：\n" if region_name else f"各地天气（{today_date}）：\n"
        return header + "\n".join(weather_lines)

    @staticmethod
    def format_city_weather(live: Optional[Dict[str, Any]], forecast: Optional[Dict[str, Any]], days: int = 3) -> str:
        """格式化单个城市的实时+多日预报为可读文本"""
        lines: List[str] = []
        city_name = ""
        if live:
            city_name = live.get("city", "")
            lines.append(
                f"{city_name} 实时天气：{live.get('weather', '')}，"
                f"{live.get('temperature', '')}℃，"
                f"{live.get('winddirection', '')}风{live.get('windpower', '')}级，"
                f"湿度{live.get('humidity', '')}%（{live.get('reporttime', '')}）"
            )

        if forecast:
            casts = forecast.get("casts", []) or []
            if not city_name:
                city_name = forecast.get("city", "")
                if city_name:
                    lines.append(f"{city_name} 天气预报：")
            else:
                lines.append("未来几天预报：")
            for cast in casts[:max(1, days)]:
                lines.append(
                    f"  {cast.get('date', '')}（周{cast.get('week', '')}）："
                    f"{cast.get('dayweather', '')}/{cast.get('nightweather', '')}，"
                    f"{cast.get('nighttemp', '')}~{cast.get('daytemp', '')}℃，"
                    f"{cast.get('daywind', '')}风{cast.get('daypower', '')}级"
                )

        return "\n".join(lines).strip()
