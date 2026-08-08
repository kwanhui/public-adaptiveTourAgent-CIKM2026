"""Per-IP rate limiter middleware.

In-memory sliding window. Resets on process restart, which is fine for the
demo: HF Spaces restart on git push, and abuse traffic doesn't usually
care about persistence anyway.

`/plan` and `/find-accommodation` are rate-limited: both spend on the LLM
without an existing session, so nothing else bounds them. `/replan/{sid}`
and friends already require a session, which transitively requires having
gone through `/plan` once, and are bounded by the per-session cost cap.
`/healthz`, `/`, `/static/*`, and `/events/{sid}` are always free.

Note the X-Forwarded-For read below trusts the leftmost value, which a
caller can set. That is fine against accidental or casual overuse, which
is what this is for, but it is not an anti-abuse control.
"""

from collections import defaultdict, deque
from datetime import datetime, timedelta

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class PerIPRateLimiter(BaseHTTPMiddleware):
    """Caps how often a single IP can hit the LLM-spending endpoints.

    Set `max_per_hour=0` (or pass an empty `paths` tuple) to disable.
    """

    def __init__(
        self,
        app,
        *,
        max_per_hour: int = 10,
        paths: tuple[str, ...] = ("/plan",),
        window_seconds: float = 3600.0,
    ) -> None:
        super().__init__(app)
        self.max_per_hour = max_per_hour
        self.paths = paths
        self.window = timedelta(seconds=window_seconds)
        self._hits: dict[str, deque[datetime]] = defaultdict(deque)

    def _client_ip(self, request: Request) -> str:
        # Behind nginx/HF, X-Forwarded-For carries the real client IP.
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if self.max_per_hour <= 0 or not self.paths:
            return await call_next(request)

        if request.method == "POST" and any(request.url.path.startswith(p) for p in self.paths):
            ip = self._client_ip(request)
            now = datetime.now()
            cutoff = now - self.window

            hits = self._hits[ip]
            while hits and hits[0] < cutoff:
                hits.popleft()

            if len(hits) >= self.max_per_hour:
                retry_after = int((hits[0] + self.window - now).total_seconds()) + 1
                return JSONResponse(
                    status_code=429,
                    headers={"retry-after": str(retry_after)},
                    content={
                        "detail": (
                            f"rate limit: {self.max_per_hour} plans per "
                            f"{int(self.window.total_seconds() // 60)} minutes per IP"
                        ),
                    },
                )

            hits.append(now)

        return await call_next(request)
