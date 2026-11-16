"""One long-lived AsyncClient on ONE dedicated loop; thread-safe process resource."""
from __future__ import annotations
import asyncio
import atexit
from concurrent.futures import Future
from dataclasses import dataclass
import threading
import httpx

@dataclass(frozen=True)
class ApiResponse:
    status: int
    data: object

class ApiPool:
    def __init__(self, base_url, *, transport=None, capacity=64):
        if not base_url.startswith(("http://", "https://")): raise ValueError("Fixed API base URL required")
        self.base_url, self.transport = base_url.rstrip("/"), transport
        self.slots = threading.BoundedSemaphore(capacity)
        self.ready = threading.Event()
        self.closed = False
        self.error = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run,daemon=True,name="edu-http-pool")
        self.thread.start()
        if not self.ready.wait(5) or self.error: raise RuntimeError("API pool startup failed")
        atexit.register(self.close)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        async def initialize():
            self.client = httpx.AsyncClient(base_url=self.base_url,transport=self.transport,
                trust_env=False,follow_redirects=False,
                timeout=httpx.Timeout(15.0,connect=2.0,pool=.5),
                limits=httpx.Limits(max_connections=32,max_keepalive_connections=16,keepalive_expiry=60))
        try: self.loop.run_until_complete(initialize())
        except BaseException as exc: self.error = type(exc).__name__
        finally: self.ready.set()
        if self.error: self.loop.close(); return
        try: self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending: task.cancel()
            if pending: self.loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
            self.loop.run_until_complete(self.client.aclose())
            self.loop.close()

    async def _request(self, method, path, token, payload):
        # Path cannot redirect the shared pool to attacker-controlled hosts.
        if not path.startswith("/v1/") or "://" in path or "\\" in path:
            raise ValueError("Only fixed backend /v1/ API routes are permitted")
        request = self.client.build_request(method,path,json=payload if payload is not None else None,
            headers={"Authorization":"Bearer "+token})
        request.headers.pop("cookie",None)  # Never share a cookie across users of this pooled client.
        try:
            response = await self.client.send(request)
            try: data = response.json()
            except ValueError: data = {"detail":"Invalid API response"}
            return ApiResponse(response.status_code,data)
        except httpx.HTTPError:
            return ApiResponse(503,{"detail":"Backend is temporarily unreachable"})
        finally:
            self.client.cookies.clear()

    def submit(self, method, path, token, payload=None) -> Future:
        if self.closed or not self.slots.acquire(blocking=False):
            future = Future(); future.set_result(ApiResponse(429,{"detail":"UI request capacity reached"})); return future
        try: future = asyncio.run_coroutine_threadsafe(self._request(method,path,token,payload),self.loop)
        except BaseException:
            self.slots.release(); raise
        future.add_done_callback(lambda _: self.slots.release())
        return future

    def close(self):
        if self.closed: return
        self.closed = True
        if self.loop.is_running(): self.loop.call_soon_threadsafe(self.loop.stop)
        if threading.current_thread() is not self.thread: self.thread.join(timeout=5)
