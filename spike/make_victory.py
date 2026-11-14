# Generates animations/victory.anim.yaml (run: .venv/bin/python spike/make_victory.py animations/victory.anim.yaml)
# A short celebration gesture, every pose once and held long enough to see: wind-up, burst into a V, a lean
# overhead, a fist pump and a champion pose; back to Stand. Arms and head only, so it plays with
# the balancer on. Regenerating resets reviewed_by.
import math
import sys
from pathlib import Path

from naollm.animations.limits import builtin_limits
from naollm.animations.model import Animation

LIMITS = builtin_limits()
STAND = LIMITS.postures["Stand"]

BEAT = 0.5           # 120 BPM
START = 0.2          # first keyframe, at Stand
SPEED_SHARE = 0.6    # of the validator's speed limit (60 % of each joint's maximum speed); v3 at 0.85 was too fast to follow
FRACTION = 0.6
MIN_HOLD = 0.3       # every pose stays still this long

# Right-arm poses; the left arm mirrors them (roll, elbow yaw, elbow roll and wrist yaw negated).
POSES = {
    "stand":  dict(ShoulderPitch=STAND["RShoulderPitch"], ShoulderRoll=STAND["RShoulderRoll"],
                   ElbowYaw=STAND["RElbowYaw"], ElbowRoll=STAND["RElbowRoll"], WristYaw=0, Hand=0.3),
    # Fists drawn in front of the chest: the wind-up.
    "wind":   dict(ShoulderPitch=50, ShoulderRoll=-10, ElbowYaw=90, ElbowRoll=83, WristYaw=0, Hand=0.0),
    # Straight arms up and out, open hands.
    "v_up":   dict(ShoulderPitch=-80, ShoulderRoll=-40, ElbowYaw=70, ElbowRoll=10, WristYaw=0, Hand=1.0),
    # Fist pump: punch straight up, then pull the fist down next to the head.
    "punch":  dict(ShoulderPitch=-85, ShoulderRoll=-10, ElbowYaw=70, ElbowRoll=10, WristYaw=0, Hand=0.0),
    "pull":   dict(ShoulderPitch=-15, ShoulderRoll=-15, ElbowYaw=90, ElbowRoll=80, WristYaw=0, Hand=0.0),
    # Arms overhead, both leaning to one side: the outer arm rolls out, the inner one in.
    "sway_out": dict(ShoulderPitch=-80, ShoulderRoll=-45, ElbowYaw=70, ElbowRoll=10, WristYaw=0, Hand=1.0),
    "sway_in":  dict(ShoulderPitch=-80, ShoulderRoll=5,   ElbowYaw=70, ElbowRoll=10, WristYaw=0, Hand=1.0),
    # Hand on the hip (the Macarena "hip" pose).
    "hip":    dict(ShoulderPitch=65, ShoulderRoll=10, ElbowYaw=80, ElbowRoll=55, WristYaw=0, Hand=0.5),
}
MIRRORED = {"ShoulderRoll", "ElbowYaw", "ElbowRoll", "WristYaw"}
# The left fist sits above the right one in the wind-up, so the forearms do not meet.
SIDE_OFFSET = {("L", "wind"): {"ShoulderPitch": 30}}
HEAD_STAND = (STAND["HeadYaw"], STAND["HeadPitch"])

# (beats, right pose, left pose, head yaw, head pitch); HeadYaw > 0 looks left, HeadPitch > 0 looks down.
STEPS = [
    # wind-up, then burst into a V and look up
    (2, "wind", "wind", 0, 15),
    (2, "v_up", "v_up", 0, -25),
    # one lean to the right overhead, the head follows
    (1, "sway_out", "sway_in", -25, -15),
    # one fist pump: right fist up, left fist pulled down
    (1, "punch", "pull", -15, -15),
    # champion: right fist stays up, left hand on the hip, chin up
    (2, "punch", "hip", -20, -25),
    (2, "stand", "stand", *HEAD_STAND),
]


def arm_values(side: str, pose: str) -> dict[str, float]:
    values = {}
    for joint, v in POSES[pose].items():
        v = SIDE_OFFSET.get((side, pose), {}).get(joint, v)
        if side == "L" and joint in MIRRORED:
            v = -v
        values[side + joint] = float(v)
    return values


def frame(right: str, left: str, yaw: float, pitch: float) -> dict[str, float]:
    return {**arm_values("R", right), **arm_values("L", left), "HeadYaw": float(yaw), "HeadPitch": float(pitch)}


def move_time(a: dict[str, float], b: dict[str, float]) -> float:
    """Shortest move from a to b that stays under SPEED_SHARE of the validator's speed limit."""
    need = 0.0
    for joint in a:
        if joint.endswith("Hand"):
            continue  # an open fraction, not degrees
        top = LIMITS.max_velocity_deg_s(joint) * FRACTION * SPEED_SHARE
        need = max(need, abs(b[joint] - a[joint]) / top)
    return math.ceil(need * 100) / 100


times: list[float] = [START]
frames = [frame("stand", "stand", *HEAD_STAND)]
start = START
for beats, right, left, yaw, pitch in STEPS:
    target = frame(right, left, yaw, pitch)
    move = move_time(frames[-1], target)
    span = beats * BEAT
    if move + MIN_HOLD > span:
        span = math.ceil((move + MIN_HOLD) / BEAT) * BEAT
        print(f"step {right}/{left} needs {move} s to move; stretched to {span} s")
    times += [round(start + move, 3), round(start + span, 3)]
    frames += [target, target]
    start += span

tracks = {joint: {"times": times, "angles_deg": [round(f[joint], 2) for f in frames]} for joint in frames[0]}
end_time = times[-1]

anim = Animation.model_validate({
    "name": "victory",
    "description": "Short celebration, every pose once: wind-up, burst into a V, a lean overhead, "
                   "a fist pump and a champion pose; back to Stand.",
    "version": 4,
    "author": "agent:claude",
    "reviewed_by": "",
    "posture": "Stand",
    "duration_s": end_time,
    "uses_legs": False,
    "interpolation": "bezier",
    "tracks": tracks,
})
header = (
    "# Set after previewing on the real robot with `naollm anim preview` (operator hands near it).\n"
    "# Until then the animation is dropped from the tool enums in event mode.\n"
)
lines = anim.to_yaml().splitlines(keepends=True)
idx = next(i for i, line in enumerate(lines) if line.startswith("reviewed_by"))
Path(sys.argv[1]).write_text("".join(lines[:idx]) + header + "".join(lines[idx:]))
print("wrote", sys.argv[1], f"{end_time} s,", len(tracks), "tracks,", len(times), "keyframes")
