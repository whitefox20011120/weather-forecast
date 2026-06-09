"""
天气预报插件 
定时获取指定地区天气并推送到群聊，支持 /天气 和 /weather 手动查询。
"""

import asyncio
import datetime
import re
from typing import Any

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .weather_service import WeatherService


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="2.0.0", description="配置版本")


class WeatherConfig(PluginConfigBase):
    """天气 API 配置。"""

    __ui_label__ = "天气API"
    __ui_icon__ = "cloud"
    __ui_order__ = 1

    amap_key: str = Field(
        default="your_amap_key_here",
        description="高德地图API密钥，申请地址: https://console.amap.com/dev/key/app",
    )



class GroupsConfig(PluginConfigBase):
    """群聊推送配置。"""

    __ui_label__ = "群聊推送"
    __ui_icon__ = "users"
    __ui_order__ = 2

    target_groups: list[str] = Field(
        default_factory=list,
        description='每行一组，格式："群号 地点名称"。',
        json_schema_extra={
            "hint": (
                "每行一组群聊推送配置。\n"
                "格式：群号 地点名称\n"
                "示例：123456789 河北省\n"
                "地点支持省、市、区级别"
            ),
            "label": "群聊推送列表",
            "placeholder": "123456789 河北省",
            "order": 0,
        },
    )


class ScheduleConfig(PluginConfigBase):
    """定时任务配置。"""

    __ui_label__ = "定时任务"
    __ui_icon__ = "clock"
    __ui_order__ = 3

    broadcast_enabled: bool = Field(default=True, description="是否启用定时推送（关闭后指令和工具仍可用）")
    broadcast_time: str = Field(default="08:00", description="每日播报时间 (HH:MM格式)")


class BroadcastConfig(PluginConfigBase):
    """播报内容配置。"""

    __ui_label__ = "播报"
    __ui_icon__ = "message-circle"
    __ui_order__ = 4

    max_length: int = Field(default=200, description="播报内容最大字数")


class ToolConfig(PluginConfigBase):
    """天气查询工具配置。"""

    __ui_label__ = "查询工具"
    __ui_icon__ = "search"
    __ui_order__ = 5

    enabled: bool = Field(default=True, description="是否启用天气查询工具（供LLM调用）")
    default_days: int = Field(default=3, description="默认返回未来几天预报（1-4）")


class WeatherForecastPluginConfig(PluginConfigBase):
    """天气预报插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    groups: GroupsConfig = Field(default_factory=GroupsConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    broadcast: BroadcastConfig = Field(default_factory=BroadcastConfig)
    tool: ToolConfig = Field(default_factory=ToolConfig)


def _parse_target_groups(raw: list[str]) -> list[tuple[str, str]]:
    """解析群聊配置列表，返回 [(group_id, location), ...]"""
    results = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            results.append((parts[0], parts[1]))
        elif len(parts) == 1:
            results.append((parts[0], ""))
    return results


class WeatherForecastPlugin(MaiBotPlugin):
    """天气预报插件"""

    config_model = WeatherForecastPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._scheduler_task: asyncio.Task[None] | None = None

    async def on_load(self) -> None:
        if self.config.plugin.enabled:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            self.ctx.logger.info("天气预报插件已启动")

    async def on_unload(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        self.ctx.logger.info("天气预报插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        del scope, config_data, version

    # ===== 定时广播 =====

    async def _scheduler_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            try:
                now = datetime.datetime.now()
                time_str = self.config.schedule.broadcast_time
                hour, minute = map(int, time_str.split(":"))
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if now >= target:
                    target += datetime.timedelta(days=1)
                wait = (target - now).total_seconds()
                self.ctx.logger.info(f"下次天气播报: {target.strftime('%Y-%m-%d %H:%M')}")
                await asyncio.sleep(wait)
                if self.config.schedule.broadcast_enabled:
                    await self._broadcast_all()
                else:
                    self.ctx.logger.info("定时推送已关闭，跳过本次播报")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.ctx.logger.error(f"定时任务出错: {e}")
                await asyncio.sleep(60)


    async def _broadcast_all(self) -> None:
        groups = _parse_target_groups(self.config.groups.target_groups)
        if not groups:
            self.ctx.logger.warning("未配置目标群聊，跳过播报")
            return

        service = WeatherService(self.config.weather.amap_key)
        if not service.is_configured():
            self.ctx.logger.error("未配置高德地图API密钥")
            return

        location_to_groups: dict[str, list[str]] = {}
        for group_id, location in groups:
            location_to_groups.setdefault(location, []).append(group_id)

        broadcast_cache: dict[str, str] = {}
        for location, group_ids in location_to_groups.items():
            try:
                if location not in broadcast_cache:
                    city_info = await service.resolve_city(location)
                    if not city_info:
                        self.ctx.logger.warning(f"无法解析地点: {location}")
                        continue
                    weather_text = await service.get_region_summary(city_info["adcode"], location)
                    if not weather_text:
                        self.ctx.logger.warning(f"获取天气数据失败: {location}")
                        continue
                    broadcast = await self._generate_broadcast(weather_text)
                    if not broadcast:
                        continue
                    broadcast_cache[location] = broadcast

                text = broadcast_cache[location]
                for group_id in group_ids:
                    try:
                        stream = await self.ctx.chat.get_stream_by_group_id(group_id)
                        if not stream:
                            self.ctx.logger.warning(f"未找到群聊流: {group_id}")
                            continue
                        stream_id = stream.get("stream_id", "") if isinstance(stream, dict) else str(stream)
                        await self.ctx.send.text(text, stream_id)
                        self.ctx.logger.info(f"天气播报已发送: {group_id} ({location})")
                        await asyncio.sleep(2)
                    except Exception as e:
                        self.ctx.logger.error(f"发送到群 {group_id} 失败: {e}")
            except Exception as e:
                self.ctx.logger.error(f"处理地点 {location} 播报失败: {e}")


    async def _generate_broadcast(self, weather_info: str) -> str | None:
        max_length = self.config.broadcast.max_length
        bot_name = await self.ctx.config.get("bot.nickname", "我")
        personality = await self.ctx.config.get("personality.personality", "")
        reply_style = await self.ctx.config.get("personality.reply_style", "")

        prompt = (
            f"你现在是 {bot_name}。\n"
            f"【核心人设】\n你{personality}\n\n"
            f"【说话风格】\n{reply_style}\n\n"
            f"【本次任务】\n"
            f"根据以下天气信息，生成一段天气播报发送到群聊中。\n\n"
            f"【天气信息】\n{weather_info}\n\n"
            f"【输出要求】\n"
            f"1. 严格遵守上面的人设和说话风格，自然真诚。\n"
            f"2. 字数不超过 {max_length} 个汉字。\n"
            f"3. 直接输出播报内容，不要前后缀或Markdown标记。\n"
            f"4. 对各城市天气做整体概括，挑出重点。\n"
            f"5. 重点提醒温度变化大或需注意的天气（降雨、大风等）。\n"
            f"6. 可加入关心建议，如提醒带伞、添衣。\n"
        )

        result = await self.ctx.llm.generate(prompt, model="utils")
        if not result.get("success"):
            self.ctx.logger.error("LLM生成播报失败")
            return None

        text = str(result.get("response", "")).strip().strip('"“”')
        return text[:max_length] if text else None

    # ===== 手动查询命令 =====

    @Command(
        "weather_query_cmd",
        description="查询指定地点天气",
        pattern=r"^[/／](天气|weather)\s+(?P<location>.+)$",
    )
    async def handle_weather_cmd(self, stream_id: str = "", **kwargs: Any):
        matched_groups = kwargs.get("matched_groups", {})
        location = str(matched_groups.get("location", "")).strip() if isinstance(matched_groups, dict) else ""

        if not location:
            raw_text = str(kwargs.get("text", ""))
            m = re.match(r"^[/／](?:天气|weather)\s+(.+)$", raw_text)
            if m:
                location = m.group(1).strip()

        if not location:
            await self.ctx.send.text("请提供地点名称，如：/天气 北京", stream_id)
            return False, "未提供地点", True

        service = WeatherService(self.config.weather.amap_key)
        if not service.is_configured():
            await self.ctx.send.text("天气服务未配置API密钥", stream_id)
            return False, "未配置", True

        city_info = await service.resolve_city(location)
        if not city_info:
            await self.ctx.send.text(f"未找到「{location}」对应的地区", stream_id)
            return False, "地区未找到", True

        adcode = city_info["adcode"]
        live = await service.get_live(adcode)
        forecast = await service.get_forecast(adcode)

        if not live and not forecast:
            await self.ctx.send.text(f"未能获取「{location}」的天气数据", stream_id)
            return False, "无数据", True

        text = WeatherService.format_city_weather(live, forecast, days=3)
        await self.ctx.send.text(text, stream_id)
        return True, "天气查询完成", True

    # ===== LLM 天气查询工具 =====

    @Tool(
        "weather_query",
        description="天气查询工具。当有人询问某地天气、气温、是否下雨等问题时使用，基于高德地图API。",
        parameters=[
            ToolParameterInfo(name="city", param_type=ToolParamType.STRING, description="城市或地区名称，如：北京、石家庄", required=True),
            ToolParameterInfo(name="days", param_type=ToolParamType.INTEGER, description="返回未来几天预报(1-4)", required=False),
        ],
    )
    async def handle_weather_tool(self, city: str = "", days: int = 3, **kwargs: Any):
        city = city.strip()
        if not city:
            return {"name": "weather_query", "content": "未提供城市名称。"}

        try:
            days = max(1, min(int(days), 4))
        except (TypeError, ValueError):
            days = self.config.tool.default_days

        service = WeatherService(self.config.weather.amap_key)
        if not service.is_configured():
            return {"name": "weather_query", "content": "天气服务未配置API密钥。"}

        city_info = await service.resolve_city(city)
        if not city_info:
            return {"name": "weather_query", "content": f"未找到「{city}」对应的地区。"}

        adcode = city_info["adcode"]
        live = await service.get_live(adcode)
        forecast = await service.get_forecast(adcode)

        if not live and not forecast:
            return {"name": "weather_query", "content": f"未能获取「{city}」的天气数据。"}

        text = WeatherService.format_city_weather(live, forecast, days=days)
        return {"name": "weather_query", "content": text or f"「{city}」天气数据为空。"}


def create_plugin() -> WeatherForecastPlugin:
    return WeatherForecastPlugin()

