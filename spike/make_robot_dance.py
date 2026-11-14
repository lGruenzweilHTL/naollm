# Generates animations/robot.anim.yaml (run: .venv/bin/python spike/make_robot_dance.py animations/robot.anim.yaml)
# A "robot" dance at 120 BPM: stiff arm poses that snap into place and hold, head snaps, and a
# power-down and reboot at the end. Arms and head only, so it plays with the balancer on.
# Regenerating resets reviewed_by.
import math
import sys
from pathlib import Path

from naollm.animations.limits import builtin_limits
from naollm.animations.model import Animation

LIMITS = builtin_limits()
STAND = LIMITS.postures["Stand"]

BEAT = 0.5           # 120 BPM
START = 0.2          # first keyframe, at Stand
SPEED_SHARE = 0.85   # of the validator's speed limit (60 % of each joint's maximum speed)
FRACTION = 0.6
MIN_HOLD = 0.08      # every pose is held at least this long, so the moves look like snaps

# Right-arm poses; the left arm mirrors them (roll, elbow yaw, elbow roll and wrist yaw negated).
POSES = {
    "stand":  dict(ShoulderPitch=STAND["RShoulderPitch"], ShoulderRoll=STAND["RShoulderRoll"],
                   ElbowYaw=STAND["RElbowYaw"], ElbowRoll=STAND["RElbowRoll"], WristYaw=0, Hand=0.3),
    # Straight arm forward.
    "fwd":    dict(ShoulderPitch=0,  ShoulderRoll=-5,  ElbowYaw=70, ElbowRoll=8,  WristYaw=0, Hand=0.8),
    # Upper arm forward, forearm pointing up.
    "up":     dict(ShoulderPitch=0,  ShoulderRoll=-5,  ElbowYaw=90, ElbowRoll=80, WristYaw=0, Hand=0.8),
    # Elbow out, forearm across the chest (the Macarena "shoulder" pose).
    "chest":  dict(ShoulderPitch=35, ShoulderRoll=-15, ElbowYaw=90, ElbowRoll=83, WristYaw=0, Hand=0.8),
    # Straight arm out to the side.
    "side":   dict(ShoulderPitch=0,  ShoulderRoll=-70, ElbowYaw=70, ElbowRoll=8,  WristYaw=0, Hand=0.8),
    # Upper arm out to the side, forearm up: a goalpost.
    "goal":   dict(ShoulderPitch=0,  ShoulderRoll=-70, ElbowYaw=0,  ElbowRoll=80, WristYaw=0, Hand=0.8),
    "goal_half": dict(ShoulderPitch=0, ShoulderRoll=-70, ElbowYaw=0, ElbowRoll=35, WristYaw=0, Hand=0.8),
    # Arms hang straight down: powered off.
    "off":    dict(ShoulderPitch=95, ShoulderRoll=-5,  ElbowYaw=70, ElbowRoll=8,  WristYaw=0, Hand=0.2),
}
MIRRORED = {"ShoulderRoll", "ElbowYaw", "ElbowRoll", "WristYaw"}
# The left forearm crosses above the right one, so the arms do not meet at the same height.
SIDE_OFFSET = {("L", "chest"): {"ShoulderPitch": 20}}
HEAD_STAND = (STAND["HeadYaw"], STAND["HeadPitch"])

# (beats, right pose, left pose, head yaw, head pitch); HeadYaw > 0 looks left, HeadPitch > 0 looks down.
STEPS = [
    (2, "fwd", "fwd", 0, -10),
    # robot wave: one forearm up, the other arm straight
    (1, "up", "fwd", 0, -10), (1, "fwd", "up", 0, -10), (1, "up", "fwd", 0, -10), (1, "fwd", "up", 0, -10),
    # both forearms up and down
    (1, "up", "up", 0, -10), (1, "fwd", "fwd", 0, -10), (1, "up", "up", 0, -10), (1, "fwd", "fwd", 0, -10),
    # arms across the chest, head snaps
    (1, "chest", "chest", 0, -10), (1, "chest", "chest", 40, -10), (1, "chest", "chest", -40, -10),
    (1, "chest", "chest", 40, -10), (1, "chest", "chest", 0, -10),
    # point right, then left, looking at the hand
    (2, "side", "chest", -45, -5), (2, "side", "side", 45, -5),
    # goalposts, elbows pump
    (1, "goal", "goal", 0, -15), (1, "goal_half", "goal_half", 0, -15),
    (1, "goal", "goal", 0, -15), (1, "goal_half", "goal_half", 0, -15), (1, "goal", "goal", 0, -15),
    # power down, then reboot with a head snap
    (3, "off", "off", 0, 20),
    (1, "off", "off", 0, -10),
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
            continue  # an open fraction, not degrees; the hands move little
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
    "name": "robot",
    "description": "Robot dance at 120 BPM: stiff arm poses that snap and hold, head snaps, "
                   "a power-down and a reboot; back to Stand.",
    "version": 1,
    "author": "agent:claude",
    "reviewed_by": "",
    "posture": "Stand",
    "duration_s": end_time,
    "uses_legs": False,
    "interpolation": "linear",
    "tracks": tracks,
})
Path(sys.argv[1]).write_text(anim.to_yaml())
print("wrote", sys.argv[1], f"{end_time} s,", len(tracks), "tracks,", len(times), "keyframes")
