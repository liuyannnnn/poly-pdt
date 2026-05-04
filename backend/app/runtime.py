"""Runtime 编排：按顺序启动组件，关闭时反向停止。"""

from collections.abc import Iterable
from typing import Protocol


class RuntimeComponent(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class Runtime:
    def __init__(self, components: Iterable[RuntimeComponent]):
        self._components = list(components)
        self.running = False

    async def start(self) -> None:
        if self.running:
            return
        for component in self._components:
            await component.start()
        self.running = True

    async def stop(self) -> None:
        if not self.running:
            return
        for component in reversed(self._components):
            await component.stop()
        self.running = False

    def status(self) -> dict[str, bool]:
        return {"running": self.running}
