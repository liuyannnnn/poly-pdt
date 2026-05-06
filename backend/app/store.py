"""Redis/内存存储适配层：统一做 JSON 序列化、TTL 和敏感字段脱敏。"""

import json
import time
from fnmatch import fnmatch
from typing import Any

from redis.asyncio import Redis

from .config import Settings


SENSITIVE_FIELD_MARKERS = (
    "private_key",
    "secret",
    "api_token",
    "access_token",
    "auth_token",
    "bearer_token",
    "passphrase",
    "signature",
    "api_key",
)
SENSITIVE_FIELD_EXACT = {"token", "password"}
DEFAULT_STREAM_MAX_LEN = 10_000


def redact_sensitive(value: Any) -> Any:
    # 所有写入 Redis/stream 的 payload 都先过这里，防止 key/token/signature 落库。
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in SENSITIVE_FIELD_EXACT or any(marker in normalized for marker in SENSITIVE_FIELD_MARKERS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


class RedisStore:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Redis | None = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            timeout = self._settings.redis_socket_timeout_seconds
            self._client = Redis.from_url(
                self._settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
            )
        return self._client

    async def ping(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            return False

    async def get_json(self, key: str) -> Any | None:
        value = await self.client.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def get_many_json(self, keys: list[str]) -> list[Any | None]:
        # 列表页会一次读取多场比赛；MGET 避免对每个 key 单独走一次 Redis 往返。
        if not keys:
            return []
        values = await self.client.mget(keys)
        return [json.loads(value) if value is not None else None for value in values]

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        payload = json.dumps(redact_sensitive(value), ensure_ascii=False, separators=(",", ":"))
        await self.client.set(key, payload, ex=ttl_seconds)

    async def append_json_list_item(self, key: str, value: Any, *, ttl_seconds: int, max_rows: int | None) -> None:
        # 高频 tick 只能追加，不能每条都读出整段 JSON 再写回 Redis。
        key_type = await self.client.type(key)
        if key_type not in {"none", "list"}:
            raise TypeError(f"append_json_list_item expected list key, got {key_type} for {key}")
        payload = json.dumps(redact_sensitive(value), ensure_ascii=False, separators=(",", ":"))
        pipe = self.client.pipeline()
        pipe.rpush(key, payload)
        if max_rows is not None and max_rows > 0:
            pipe.ltrim(key, -max_rows, -1)
        pipe.expire(key, ttl_seconds)
        await pipe.execute()

    async def get_json_list(self, key: str, limit: int | None = None) -> list[Any]:
        key_type = await self.client.type(key)
        if key_type == "none":
            return []
        if key_type != "list":
            value = await self.get_json(key)
            return value if isinstance(value, list) else []
        if limit is not None and limit > 0:
            values = await self.client.lrange(key, -limit, -1)
        else:
            values = await self.client.lrange(key, 0, -1)
        return [json.loads(value) for value in values]

    async def set_json_list(self, key: str, values: list[Any], ttl_seconds: int | None = None) -> None:
        pipe = self.client.pipeline()
        pipe.delete(key)
        if values:
            pipe.rpush(
                key,
                *[
                    json.dumps(redact_sensitive(value), ensure_ascii=False, separators=(",", ":"))
                    for value in values
                ],
            )
        if ttl_seconds is not None:
            pipe.expire(key, ttl_seconds)
        await pipe.execute()

    async def get_text(self, key: str) -> str | None:
        return await self.client.get(key)

    async def set_text(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self.client.set(key, value, ex=ttl_seconds)

    async def add_stream(
        self,
        key: str,
        value: dict[str, Any],
        max_len: int | None = DEFAULT_STREAM_MAX_LEN,
        ttl_seconds: int | None = None,
    ) -> None:
        payload = {
            field: json.dumps(redact_sensitive(item), ensure_ascii=False)
            for field, item in redact_sensitive(value).items()
        }
        kwargs = {"maxlen": max_len, "approximate": True} if max_len is not None and max_len > 0 else {}
        await self.client.xadd(key, payload, **kwargs)
        if ttl_seconds is not None:
            await self.client.expire(key, ttl_seconds)

    async def stream(self, key: str, limit: int | None = None) -> list[dict[str, Any]]:
        # 大 stream 不能全量 XRANGE；默认保留旧语义，调用方可指定 limit 只取最新 N 条。
        if limit is not None and limit > 0:
            rows = list(reversed(await self.client.xrevrange(key, count=limit)))
        else:
            rows = await self.client.xrange(key)
        result: list[dict[str, Any]] = []
        for _row_id, fields in rows:
            result.append({field: json.loads(value) for field, value in fields.items()})
        return result

    async def keys(self, pattern: str) -> list[str]:
        return [key async for key in self.client.scan_iter(pattern)]

    async def ttl(self, key: str) -> int:
        return int(await self.client.ttl(key))

    async def delete(self, key: str) -> None:
        await self.client.delete(key)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class MemoryStore:
    def __init__(self):
        self._values: dict[str, str] = {}
        self._expires: dict[str, tuple[float, int]] = {}
        self._streams: dict[str, list[dict[str, Any]]] = {}

    async def ping(self) -> bool:
        return True

    async def get_json(self, key: str) -> Any | None:
        value = await self.get_text(key)
        if value is None:
            return None
        return json.loads(value)

    async def get_many_json(self, keys: list[str]) -> list[Any | None]:
        return [await self.get_json(key) for key in keys]

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        await self.set_text(
            key,
            json.dumps(redact_sensitive(value), ensure_ascii=False, separators=(",", ":")),
            ttl_seconds=ttl_seconds,
        )

    async def append_json_list_item(self, key: str, value: Any, *, ttl_seconds: int, max_rows: int | None) -> None:
        existing = await self.get_json(key)
        if existing is not None and not isinstance(existing, list):
            raise TypeError(f"append_json_list_item expected list key, got value for {key}")
        rows = existing or []
        rows.append(redact_sensitive(value))
        if max_rows is not None and max_rows > 0:
            rows = rows[-max_rows:]
        await self.set_json_list(key, rows, ttl_seconds=ttl_seconds)

    async def get_json_list(self, key: str, limit: int | None = None) -> list[Any]:
        value = await self.get_json(key)
        if not isinstance(value, list):
            return []
        if limit is not None and limit > 0:
            return value[-limit:]
        return value

    async def set_json_list(self, key: str, values: list[Any], ttl_seconds: int | None = None) -> None:
        await self.set_json(key, values, ttl_seconds=ttl_seconds)

    async def get_text(self, key: str) -> str | None:
        self._drop_expired(key)
        return self._values.get(key)

    async def set_text(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        self._values[key] = value
        if ttl_seconds is not None:
            self._expires[key] = (time.time() + ttl_seconds, ttl_seconds)
        else:
            self._expires.pop(key, None)

    async def add_stream(
        self,
        key: str,
        value: dict[str, Any],
        max_len: int | None = DEFAULT_STREAM_MAX_LEN,
        ttl_seconds: int | None = None,
    ) -> None:
        self._drop_expired(key)
        rows = self._streams.setdefault(key, [])
        rows.append(redact_sensitive(value))
        if max_len is not None and max_len > 0 and len(rows) > max_len:
            del rows[:-max_len]
        if ttl_seconds is not None:
            self._expires[key] = (time.time() + ttl_seconds, ttl_seconds)

    async def stream(self, key: str, limit: int | None = None) -> list[dict[str, Any]]:
        self._drop_expired(key)
        rows = list(self._streams.get(key, []))
        if limit is not None and limit > 0:
            return rows[-limit:]
        return rows

    async def keys(self, pattern: str) -> list[str]:
        all_keys = set(self._values) | set(self._streams)
        if "*" in pattern:
            return sorted(key for key in all_keys if fnmatch(key, pattern))
        return [pattern] if pattern in all_keys else []

    async def ttl(self, key: str) -> int:
        self._drop_expired(key)
        if key not in self._values and key not in self._streams:
            return -2
        expires = self._expires.get(key)
        if expires is None:
            return -1
        return expires[1]

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)
        self._streams.pop(key, None)
        self._expires.pop(key, None)

    def force_expire(self, key: str) -> None:
        if key in self._values:
            self._expires[key] = (time.time() - 1, 0)

    async def close(self) -> None:
        return None

    def _drop_expired(self, key: str) -> None:
        expires = self._expires.get(key)
        if expires is not None and expires[0] <= time.time():
            self._values.pop(key, None)
            self._streams.pop(key, None)
            self._expires.pop(key, None)
