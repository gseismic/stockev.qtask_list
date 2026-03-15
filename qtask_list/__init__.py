from .queue import SmartQueue
from .worker import Worker
from .storage import RemoteStorage

__all__ = ["SmartQueue", "Worker", "RemoteStorage", "start_dashboard"]

__version__ = "0.1.0"


def start_dashboard(port: int = 8765, redis_url: str = "redis://localhost:6379/0"):
    """
    启动 Dashboard
    
    Args:
        port: Dashboard 端口 (默认 8765)
        redis_url: Redis 连接 URL
    
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
    
    # 启动 dashboard
    print(f"Starting qtask_list Dashboard on http://localhost:{port}")
    print(f"Redis: {redis_url}")
    print("\nPress Ctrl+C to stop\n")
    
    subprocess.run([sys.executable, "-m", "uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", str(port)], env=env)
