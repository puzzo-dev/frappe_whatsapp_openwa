"""A sliding window that behaves like the Redis one, for tests.

The limiter tests used to assert which Redis commands were issued — zadd called,
zremrangebyscore called once. That pinned the tests to one implementation, so
moving the same logic into an atomic Lua script broke every one of them without
any behaviour changing. What matters is the behaviour: a refused call takes
nothing, an allowed call takes exactly one, and entries leave the window as it
slides. This double provides that, and the tests assert it.
"""


class FakeWindow:
	"""Stands in for utils.sliding_window, keeping real per-key state."""

	def __init__(self, now: float = 1000.0):
		self.entries: dict[str, list[float]] = {}
		self.now = now

	def _trim(self, key: str, window: int) -> list[float]:
		kept = [t for t in self.entries.get(key, []) if t > self.now - window]
		self.entries[key] = kept
		return kept

	def take_slot(self, key: str, allowance: int, window_seconds: int):
		if allowance <= 0:
			return True, 0
		used = self._trim(key, window_seconds)
		if len(used) >= allowance:
			return False, len(used)
		used.append(self.now)
		return True, len(used)

	def calls_in_window(self, key: str, window_seconds: int) -> int:
		return len(self._trim(key, window_seconds))

	def give_back(self, key: str) -> bool:
		entries = self.entries.get(key) or []
		if not entries:
			return False
		entries.pop()
		return True

	def preload(self, key: str, count: int) -> None:
		self.entries[key] = [self.now] * count
