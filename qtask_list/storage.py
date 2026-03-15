import requests


class RemoteStorage:
    """远程大文件存储客户端"""

    def __init__(self, api_base_url: str):
        self.api_base_url = api_base_url.rstrip("/")
        self.session = requests.Session()

    def save_bytes(self, data: bytes) -> str:
        """上传 bytes 数据，返回 key"""
        url = f"{self.api_base_url}/api/storage/upload"
        files = {"file": ("payload.bin", data)}
        r = self.session.post(url, files=files)
        r.raise_for_status()
        return r.json()["key"]

    def load(self, key: str) -> bytes:
        """根据 key 下载数据"""
        url = f"{self.api_base_url}/api/storage/download/{key}"
        r = self.session.get(url)
        r.raise_for_status()
        return r.content

    def delete(self, key: str):
        """删除数据"""
        url = f"{self.api_base_url}/api/storage/delete/{key}"
        try:
            self.session.delete(url)
        except Exception:
            pass
