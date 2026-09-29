"""运维界面可展示的连接信息，避免凭据进入日志和 HTTP 响应。"""

from __future__ import annotations

from urllib.parse import urlsplit


def redis_endpoint_label(url: str) -> str:
    """只展示 Redis 主机与端口，不展示认证、路径及查询参数。"""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            return "Redis（已配置）"
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{host}{port}"
    except ValueError:
        return "Redis（已配置）"
