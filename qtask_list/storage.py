"""RemoteStorage 客户端及错误分类。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from loguru import logger

from .errors import (
    RemoteObjectNotFoundError,
    RemoteStorageConfigurationError,
    RemoteStorageError,
    RemoteStorageTransientError,
)


@dataclass(frozen=True)
class StoredObject:
    """上传结果；created 用于失败入队后的安全 best-effort 清理。"""

    key: str
    created: bool = False
    retain_until: float | None = None


class RemoteStorage:
    """远程 payload 存储客户端。

    客户端把 HTTP/网络故障分类成稳定异常，Worker 无需匹配错误字符串即可决定
    “退避重试”还是“永久失败进 DLQ”。
    """

    def __init__(self, api_base_url: str, timeout: float | None = 30):
        if not api_base_url:
            raise ValueError("api_base_url must not be empty")
        self.api_base_url = api_base_url.rstrip("/")
        self.session = requests.Session()
        self.timeout = timeout

    def save(self, data: bytes, retain_until: float | None = None) -> StoredObject:
        """上传 bytes，并请求服务端至少保留到 retain_until；None/0 表示持久保留。"""
        if not data:
            raise ValueError("data must not be empty")
        url = f"{self.api_base_url}/api/storage/upload"
        files = {"file": ("payload.bin", data)}
        form = {"retain_until": "0" if retain_until is None else str(retain_until)}
        response = self._request("post", url, files=files, data=form)
        try:
            body = response.json()
            key = str(body["key"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RemoteStorageConfigurationError(
                "storage_protocol",
                "RemoteStorage 上传响应缺少合法 key",
                status_code=response.status_code,
            ) from exc
        return StoredObject(
            key=key,
            created=bool(body.get("created", False)),
            retain_until=self._float_or_none(body.get("retain_until", retain_until)),
        )

    def save_bytes(self, data: bytes, retain_until: float | None = None) -> str:
        """兼容接口：上传 bytes 并只返回 key。"""
        return self.save(data, retain_until=retain_until).key

    def load(self, key: str) -> bytes:
        """下载对象；404 被分类为永久 object_missing。"""
        if not key:
            raise ValueError("key must not be empty")
        url = f"{self.api_base_url}/api/storage/download/{key}"
        response = self._request("get", url)
        return bytes(response.content)

    def extend_retention(self, key: str, retain_until: float | None) -> float | None:
        """把共享内容对象的保留边界单调延长，不缩短既有边界。"""
        if not key:
            raise ValueError("key must not be empty")
        url = f"{self.api_base_url}/api/storage/retain/{key}"
        response = self._request("post", url, json={"retain_until": retain_until})
        try:
            return self._float_or_none(response.json().get("retain_until"))
        except (ValueError, AttributeError) as exc:
            raise RemoteStorageConfigurationError(
                "storage_protocol",
                "RemoteStorage retain 响应不是合法 JSON",
                status_code=response.status_code,
            ) from exc

    def delete(self, key: str, *, missing_ok: bool = True) -> bool:
        """删除对象；默认把已不存在视为幂等成功。"""
        if not key:
            return False
        url = f"{self.api_base_url}/api/storage/delete/{key}"
        try:
            self._request("delete", url)
            return True
        except RemoteObjectNotFoundError:
            if missing_ok:
                return False
            raise
        except RemoteStorageError as exc:
            # 清理通常是 best-effort；调用方仍可通过返回值和日志观察失败。
            logger.warning(f"RemoteStorage delete failed key={key}: {exc}")
            return False

    def close(self) -> None:
        """释放 requests 连接池。"""
        self.session.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.Timeout as exc:
            raise RemoteStorageTransientError(
                "storage_timeout",
                f"RemoteStorage 请求超时: {exc}",
            ) from exc
        except requests.ConnectionError as exc:
            raise RemoteStorageTransientError(
                "storage_connection",
                f"RemoteStorage 连接失败: {exc}",
            ) from exc
        except requests.RequestException as exc:
            raise RemoteStorageTransientError(
                "storage_network",
                f"RemoteStorage 网络请求失败: {exc}",
            ) from exc

        status = response.status_code
        if 200 <= status < 300:
            return response

        message = self._response_message(response)
        retry_after = self._retry_after(response)
        if status == 404:
            raise RemoteObjectNotFoundError(
                "storage_object_missing",
                message,
                status_code=status,
            )
        if status in {401, 403}:
            raise RemoteStorageConfigurationError(
                "storage_auth",
                message,
                status_code=status,
            )
        if status in {408, 425, 429} or status >= 500:
            code = "storage_throttled" if status == 429 else "storage_transient"
            raise RemoteStorageTransientError(
                code,
                message,
                status_code=status,
                retry_after=retry_after,
            )
        raise RemoteStorageConfigurationError(
            "storage_request",
            message,
            status_code=status,
        )

    @staticmethod
    def _response_message(response: requests.Response) -> str:
        try:
            body = response.json()
            detail = body.get("detail") if isinstance(body, dict) else None
        except ValueError:
            detail = None
        return str(detail or f"RemoteStorage HTTP {response.status_code}")

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)
