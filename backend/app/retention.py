"""Redis retention 清理：删除已经过期的短生命周期高频数据。"""

from typing import Any


async def cleanup_retention(store: Any) -> list[str]:
    deleted: list[str] = []
    for pattern in (
        "orderbook:*",
        "orderbook_raw:*",
        "pm:raw:*",
        "gs:raw:*",
        "ws:raw:*",
    ):
        for key in await store.keys(pattern):
            if await store.ttl(key) == -2:
                deleted.append(key)
            elif await store.ttl(key) == 0:
                await store.delete(key)
                deleted.append(key)
    return sorted(set(deleted))
