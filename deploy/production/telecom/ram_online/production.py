"""Bounded-latency adapter. Unknown policy permits the call and hides the badge.

The original OnlineGate boolean/OnlineUnavailable contract is preserved. No fail-open
is applied to authentication, policy writes or Kafka commits.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time

from .core import OnlineUnavailable, normalize_phone


@dataclass(frozen=True)
class Permit:
    epoch: int
    probe: bool


class CircuitBreaker:
    """One event loop/process owns this object; no lock held across network I/O."""
    def __init__(self, failure_threshold=2, cooldown=1.0, clock=time.monotonic):
        if type(failure_threshold) is not int or failure_threshold < 1 or not math.isfinite(cooldown) or cooldown <= 0:
            raise ValueError("positive breaker limits required")
        self.threshold, self.cooldown, self.clock = failure_threshold, cooldown, clock
        self.state, self.epoch, self.failures, self.retry_at = "closed", 0, 0, 0.0

    def try_enter(self):
        if self.state == "open":
            if self.clock() < self.retry_at:
                return None
            self.state = "half_open"
            self.epoch += 1
            return Permit(self.epoch, True)
        if self.state == "half_open":
            return None
        return Permit(self.epoch, False)

    def _open(self):
        self.state, self.failures = "open", 0
        self.epoch += 1
        self.retry_at = self.clock() + self.cooldown

    def success(self, permit):
        if permit.epoch != self.epoch:
            return  # Old in-flight success must not close a newer OPEN circuit.
        if permit.probe:
            self.state = "closed"
            self.epoch += 1
        self.failures = 0

    def failure(self, permit):
        if permit.epoch != self.epoch:
            return
        self.failures += 1
        if permit.probe or self.failures >= self.threshold:
            self._open()

    def cancelled(self, permit):
        if permit.probe and permit.epoch == self.epoch:
            self._open()  # Release the sole probe, without counting caller cancellation as a dependency failure.


@dataclass(frozen=True)
class CallDecision:
    allowed: bool
    policy_status: str
    display_status: str
    reason: str


class FailOpenGate:
    def __init__(self, gate, breaker=None):
        self.gate, self.breaker = gate, breaker or CircuitBreaker()

    async def check_call_allowed(self, phone_number: str) -> CallDecision:
        phone = normalize_phone(phone_number)  # Never skip validation when the circuit is OPEN.
        permit = self.breaker.try_enter()
        if permit is None:
            return CallDecision(True, "unavailable", "hidden", "redis_circuit_open")
        try:
            allowed = await self.gate.check_call_allowed(phone)
            if type(allowed) is not bool:
                raise OnlineUnavailable("invalid decision contract")
        except asyncio.CancelledError:
            self.breaker.cancelled(permit)
            raise
        except OnlineUnavailable:
            self.breaker.failure(permit)
            return CallDecision(True, "unavailable", "hidden", "policy_unavailable")
        except BaseException:
            self.breaker.cancelled(permit)
            raise
        self.breaker.success(permit)
        return CallDecision(allowed, "known", "hidden" if allowed else "blocked", "exact_policy")
