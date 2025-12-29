"""Rate limiting for WebSocket input."""

import asyncio

# Default rate limits
DEFAULT_RATE = 100  # tokens per second
DEFAULT_BURST = 500


class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(
        self,
        rate: float = DEFAULT_RATE,
        burst: int = DEFAULT_BURST,
    ) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_update = asyncio.get_running_loop().time()

    async def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens.

        Args:
            tokens: Number of tokens to acquire.

        Returns:
            True if tokens were acquired, False if rate limited.
        """
        now = asyncio.get_running_loop().time()
        elapsed = now - self.last_update
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_update = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
