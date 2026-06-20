from .queue import SmartQueue
from .worker import Worker
from .storage import RemoteStorage
from .admin import QueueAdmin, QueueState

__all__ = ["SmartQueue", "Worker", "RemoteStorage", "QueueAdmin", "QueueState", "start_dashboard"]

__version__ = "0.1.0"


def start_dashboard(
    port: int = 8765,
    redis_url: str = "redis://localhost:6379/0",
    host: str = "127.0.0.1",
    user: str = "admin",
    password: str | None = None,
    session_ttl: int = 86400,
    secure_cookie: bool = False,
):
    """
    启动 Dashboard
    
    Args:
        port: Dashboard 端口 (默认 8765)
        redis_url: Redis 连接 URL
        host: Dashboard 监听地址，远程访问可用 0.0.0.0
        user: 登录用户名，设置 password 后生效
        password: 登录密码，设置后启用登录
        session_ttl: 登录会话有效期，秒
        secure_cookie: HTTPS 部署时启用 Secure Cookie
    
    Example:
        >>> from qtask_list import start_dashboard
        >>> start_dashboard(port=9000)
    """
    import os
    import subprocess
    import sys
    
    # 找到 dashboard/main.py 的路径
    import qtask_list
    pkg_dir = os.path.dirname(qtask_list.__file__)
    dashboard_path = os.path.join(pkg_dir, "..", "dashboard", "main.py")
    dashboard_path = os.path.abspath(dashboard_path)
    
    # 设置环境变量
    env = os.environ.copy()
    env["REDIS_URL"] = redis_url
    env["PORT"] = str(port)
    env["QTASK_DASHBOARD_USER"] = user
    env["QTASK_DASHBOARD_SESSION_TTL"] = str(session_ttl)
    if password is not None:
        env["QTASK_DASHBOARD_PASSWORD"] = password
    if secure_cookie:
        env["QTASK_DASHBOARD_SECURE_COOKIE"] = "1"
    
    # 启动 dashboard
    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    print(f"Starting qtask_list Dashboard on http://{display_host}:{port}")
    print(f"Redis: {redis_url}")
    print(f"Auth: {'enabled' if password else 'disabled'}")
    print("\nPress Ctrl+C to stop\n")
    
    subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dashboard.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        env=env,
    )
