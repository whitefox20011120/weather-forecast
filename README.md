# 天气预报插件

## 功能

- 每天定时向指定群聊推送天气播报（使用 bot 人设生成个性化内容）
- 支持每个群聊配置不同的地点
- 手动查询：`/天气 地点` 或 `/weather 地点`
- 提供 LLM 天气查询工具，麦麦被问到天气时可自动调用

## 快速开始

1. 获取高德地图 API 密钥：https://console.amap.com/dev/key/app
2. 编辑 `config.toml`，填入 API 密钥和群聊配置

## 配置说明

在 WebUI 或 `config.toml` 中配置：

```toml
[plugin]
enabled = true
config_version = "2.0.0"

[weather]
# 高德地图API密钥（必填）
amap_key = "your_amap_key_here"

[groups]
# 目标群聊，每行一个，格式：群号 地点名称
# 地点支持省、市、区级别
target_groups = ["123456789 河北省", "987654321 北京市"]

[schedule]
# 每日播报时间 (HH:MM)
broadcast_time = "08:00"

[broadcast]
# 播报最大字数
max_length = 200

[tool]
# 是否启用 LLM 天气查询工具
enabled = true
default_days = 3
```

## 使用方法

### 手动查询

在群聊或私聊中发送：

```
/天气 北京
/weather 石家庄
/天气 朝阳区
```

即可获取当天实时天气和未来几天预报。

### 定时播报

配置好 `groups.target_groups` 后，插件会在每天设定时间自动向对应群聊推送天气播报。

## 重要提醒

### 429 限流风险

定时播报时，插件会为每个群聊单独请求天气数据并调用 LLM 生成播报内容。
如果配置了大量群聊，短时间内会产生密集的 LLM 请求，**可能触发模型服务的 429 (Too Many Requests) 限流错误**。

建议：
- 群聊数量较多时（>10个），适当关注日志中是否出现 429 错误
- 如遇限流，可考虑错开播报时间或减少群聊数量
- 插件已在每个群播报之间加入 2 秒间隔以缓解此问题

### API 配额

高德地图个人开发者账号每天有免费调用配额（通常足够日常使用），如配置大量群聊+不同地区，请关注配额消耗。

## 依赖

- httpx（HTTP 请求库）

## 协议

GPL-v3.0-or-later
