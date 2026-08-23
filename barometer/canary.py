"""The canary runner: the ONLY component that ever touches a model, and it
takes a pulse, not an exam. One fixed benign text, scored for logprob
distribution shape, at most once per model per day. The budget guard is not
an optimisation — it is the no-specimens ethic as a rate limiter."""
from __future__ import annotations
import time
from .detect import CanaryReading
from .store import Store

CANARY_TEXT = ("The rain over the fen that evening was the thin, "
               "administrative kind, and the lamplighter went out into it "
               "at the usual hour, with a crow overhead pretending, as "
               "always, to be going the same way by coincidence.")

DAY = 86400.0

class BudgetRefusal(RuntimeError):
    """Raised when a canary run would exceed one reading per model per day."""

class CanaryRunner:
    def __init__(self, store: Store, providers: dict):
        """providers: {model_name: callable(text) ->
                       (logprobs: list[float], fingerprint: str | None)}
        Each callable is a provider transport, injected. Nothing in this
        module constructs one; live transports live with the keys, outside."""
        self.store = store
        self.providers = providers

    def due(self, model: str, now: float | None = None) -> bool:
        now = now or time.time()
        last = self.store.last_reading_ts(model)
        return last is None or (now - last) >= DAY

    def run(self, model: str, now: float | None = None,
            force: bool = False) -> CanaryReading:
        now = now or time.time()
        if not force and not self.due(model, now):
            raise BudgetRefusal(
                f"canary for {model!r} already taken within 24h — "
                "one pulse per day; the wrist is not a keyboard")
        logprobs, fingerprint = self.providers[model](CANARY_TEXT)
        reading = CanaryReading(ts=now, model=model,
                                logprobs=list(logprobs), fingerprint=fingerprint)
        self.store.add_reading(reading)
        return reading

    def run_all_due(self, now: float | None = None) -> list[CanaryReading]:
        now = now or time.time()
        return [self.run(m, now) for m in self.providers if self.due(m, now)]
