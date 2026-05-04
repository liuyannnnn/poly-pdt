import pytest

from app.runtime import Runtime


class DummyComponent:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1


@pytest.mark.asyncio
async def test_runtime_starts_and_stops_empty_components_once():
    components = [DummyComponent(), DummyComponent(), DummyComponent()]
    runtime = Runtime(components=components)

    await runtime.start()
    await runtime.start()
    await runtime.stop()
    await runtime.stop()

    assert runtime.running is False
    assert [component.started for component in components] == [1, 1, 1]
    assert [component.stopped for component in components] == [1, 1, 1]
