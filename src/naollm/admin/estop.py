"""Emergency stop (SPEC §18.3). Synchronous and callable from any thread."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Tuple

from naollm.state import State

if TYPE_CHECKING:
    from naollm.bridge import Bridge


class EStop:
    def __init__(self, bridge: "Bridge") -> None:
        self.bridge = bridge

    def steps(self) -> List[Tuple[str, Callable[[], object]]]:
        b = self.bridge
        motion = b.robot.motion
        moving = [
            ("stop_behaviors", motion.stop_all_behaviors),
            ("stop_move", motion.stop_move),
            ("balancer_off", lambda: motion.set_balancer(False)),
            ("crouch", lambda: motion.go_to_posture("Crouch")),
        ] if b.motors_enabled else []
        return [
            ("set_stopped", lambda: b.state.force(State.STOPPED)),
            ("cancel_turn", b.cancel_turn),
            ("stop_speech", b.robot.speech.stop_speech),
            *moving,
            ("eyes_red", b.set_status_eyes_red),
        ]

    def __call__(self, source: str) -> None:
        failed = []
        for name, step in self.steps():
            try:
                step()
            except Exception as err:  # one failure must not skip the rest
                failed.append(name)
                self.bridge.log.log("error", f"estop step {name} failed: {err!r}", source=source)
        self.bridge.log.log("estop", f"ESTOP (source={source})", source=source,
                            **({"failed": ",".join(failed)} if failed else {}))
