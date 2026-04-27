import asyncio
from typing import Callable

async def periodic_task(interval: int, func: Callable, *args, **kwargs):
    """Run a synchronous or asynchronous function periodically."""
    while True:
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(interval)
