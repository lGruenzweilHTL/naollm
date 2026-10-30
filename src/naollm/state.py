"""Bridge state machine (SPEC §10.1)."""

from __future__ import annotations

import threading
from enum import Enum
from typing import Callable, Dict, FrozenSet, List


class State(str, Enum):
    OFFLINE = "OFFLINE"
    RESTING = "RESTING"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    ACTING = "ACTING"
    STOPPED = "STOPPED"
    FALLEN = "FALLEN"


# Normal transitions. STOPPED, FALLEN and OFFLINE are reachable from every
# state via `force`; leaving STOPPED/FALLEN needs `resume`.
_ALLOWED: Dict[State, FrozenSet[State]] = {
    State.OFFLINE: frozenset({State.RESTING, State.IDLE}),
    State.RESTING: frozenset({State.IDLE}),
    State.IDLE: frozenset({State.LISTENING, State.THINKING, State.ACTING, State.RESTING}),
    State.LISTENING: frozenset({State.THINKING, State.ACTING, State.IDLE}),
    State.THINKING: frozenset({State.ACTING, State.IDLE}),
    State.ACTING: frozenset({State.THINKING, State.IDLE}),
    State.STOPPED: frozenset(),
    State.FALLEN: frozenset(),
}

BLOCKING = frozenset({State.OFFLINE, State.RESTING, State.STOPPED, State.FALLEN})

Listener = Callable[[State, State], None]


class StateMachine:
    def __init__(self, initial: State = State.OFFLINE) -> None:
        self.lock = threading.RLock()
        self._state = initial
        self._listeners: List[Listener] = []

    @property
    def state(self) -> State:
        with self.lock:
            return self._state

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _set(self, new: State) -> None:
        old = self._state
        self._state = new
        if old != new:
            for listener in list(self._listeners):
                listener(old, new)

    def transition(self, new: State) -> bool:
        """Normal transition; returns False (no change) when not allowed."""
        with self.lock:
            if new == self._state:
                return True
            if new not in _ALLOWED[self._state]:
                return False
            self._set(new)
            return True

    def transition_from(self, expected: State, new: State) -> bool:
        """Transition only if the current state is `expected`."""
        with self.lock:
            if self._state != expected:
                return False
            return self.transition(new)

    def force(self, new: State) -> None:
        """Enter STOPPED, FALLEN, OFFLINE or RESTING from any state."""
        if new not in (State.STOPPED, State.FALLEN, State.OFFLINE, State.RESTING):
            raise ValueError(f"cannot force {new}")
        with self.lock:
            self._set(new)

    def resume(self) -> bool:
        """Operator resume: STOPPED/FALLEN/OFFLINE/RESTING -> IDLE."""
        with self.lock:
            if self._state not in BLOCKING:
                return False
            self._set(State.IDLE)
            return True
