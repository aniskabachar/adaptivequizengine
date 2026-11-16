"""Simple in-memory, per-client sliding-window rate limiting.

This protects login/register from password guessing and the LLM endpoints
from burning through the Groq quota. It is per-process: with several workers
or instances each one keeps its own counters, which is fine as a first line of
defence. Swap for a Redis-backed limiter if you scale out horizontally.
"""
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() not in ("0", "false", "no")


def _client_key(request: Request) -> str:
    # uvicorn --proxy-headers sets request.client from X-Forwarded-For
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, name: str, max_requests: int, window_seconds: int):
        self.name = name
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def __call__(self, request: Request) -> None:
        if not _ENABLED:
            return
        key = _client_key(request)
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.max_requests:
                retry_after = int(self.window - (now - hits[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please slow down and try again shortly.",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
            if len(self._hits) > 10_000:  # keep memory bounded
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]


def _limit(env_name: str, default: int) -> int:
    return int(os.getenv(env_name, str(default)))


# Login/register: 10 attempts per minute per client
auth_rate_limit = RateLimiter("auth", _limit("RATE_LIMIT_AUTH_PER_MIN", 10), 60)

# Endpoints that call the LLM: 30 requests per minute per client
llm_rate_limit = RateLimiter("llm", _limit("RATE_LIMIT_LLM_PER_MIN", 30), 60)
