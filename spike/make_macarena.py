# Generates animations/macarena.anim.yaml (run: .venv/bin/python spike/make_macarena.py animations/macarena.anim.yaml)
# Two rounds at 103 BPM: arm sequence, hip circles and head sway. Regenerating resets reviewed_by.
import sys
from pathlib import Path

from naollm.animations.limits import builtin_limits
from naollm.animations.model import Animation

STAND = builtin_limits().postures["Stand"]

BEAT = 0.58          # 103 BPM
FIRST = 1.0          # time of beat 1; the right arm leaves Stand during beat 0
END_GAP = 0.8        # from the last beat back to Stand

# Right-arm poses; the left arm mirrors them (roll, elbow yaw, elbow roll and wrist yaw negated).
POSES = {
    "stand":  dict(ShoulderPitch=85,  ShoulderRoll=-10, ElbowYaw=70, ElbowRoll=30, WristYaw=0,   Hand=0.3),
    "fwd_dn": dict(ShoulderPitch=0,   ShoulderRoll=-5,  ElbowYaw=70, ElbowRoll=8,  WristYaw=-80, Hand=0.9),
    "fwd_up": dict(ShoulderPitch=0,   ShoulderRoll=-5,  ElbowYaw=70, ElbowRoll=8,  WristYaw=80,  Hand=0.9),
    # Elbows out to the side, forearms across the chest; fully bent elbow. (v4 lowered the upper
    # arms and spread the elbows further; it did not look better and was undone.)
    "shoulder": dict(ShoulderPitch=35, ShoulderRoll=-15, ElbowYaw=90, ElbowRoll=83, WristYaw=0,  Hand=0.5),
    # head -> hip is the largest arm swing; smaller here so the body rocks less (v3 swayed).
    "head":   dict(ShoulderPitch=-45, ShoulderRoll=-35, ElbowYaw=80, ElbowRoll=75, WristYaw=0,   Hand=0.5),
    "hip":    dict(ShoulderPitch=65,  ShoulderRoll=10,  ElbowYaw=80, ElbowRoll=55, WristYaw=0,   Hand=0.5),
    "back":   dict(ShoulderPitch=100, ShoulderRoll=-20, ElbowYaw=45, ElbowRoll=40, WristYaw=0,   Hand=0.5),
}
MIRRORED = {"ShoulderRoll", "ElbowYaw", "ElbowRoll", "WristYaw"}
# The left forearm crosses above the right one, so the arms do not meet at the same height.
SIDE = {("L", "shoulder"): {"ShoulderPitch": 20}}
SWAY_DEG = 5        # hip shake: both hip rolls and both ankle rolls together, feet stay flat
ROUND = ["fwd_dn", "fwd_up", "shoulder", "head", "hip", "back"]


def t(beat: float) -> float:
    return round(FIRST + (beat - 1) * BEAT, 3)


def arm_keys(offset: int) -> list[tuple[float, str]]:
    """(beat, pose) for one arm; `offset` 0 = right (odd beats), 1 = left (even beats)."""
    keys = [(1, "stand")] if offset else [(0, "stand")]
    for start in (0, 16):                          # two rounds, beats 1-12 and 17-28
        for i, pose in enumerate(ROUND):
            beat = start + 1 + 2 * i + offset
            keys += [(beat, pose), (beat + 1, pose)]   # move in one beat, hold during the other arm's beat
        keys.append((start + 15 + (1 - offset), "back"))  # hold through the wiggle
    return sorted(set(keys))


end_beat = 31
end_time = round(t(end_beat) + END_GAP, 3)
tracks: dict[str, dict[str, list[float]]] = {}
for side, offset in (("R", 0), ("L", 1)):
    keys = arm_keys(offset)
    for joint in POSES["stand"]:
        times, values = [], []
        for beat, pose in keys:
            v = SIDE.get((side, pose), {}).get(joint, POSES[pose][joint])
            if side == "L" and joint in MIRRORED:
                v = -v
            times.append(t(beat))
            values.append(v)
        stand = POSES["stand"][joint]
        times.append(end_time)
        values.append(-stand if side == "L" and joint in MIRRORED else stand)
        tracks[side + joint] = {"times": times, "angles_deg": values}

# Head sway during the wiggle beats (13-16 and 29-31).
head_t, head_a = [t(12)], [0]
for beats in ((13, 14, 15, 16), (29, 30, 31)):
    for i, b in enumerate(beats):
        head_t.append(t(b))
        head_a.append(0 if b in (16, 31) else (20 if i % 2 == 0 else -20))
    if beats[0] == 13:
        head_t.append(t(28)); head_a.append(0)
head_t.append(end_time); head_a.append(0)
tracks["HeadYaw"] = {"times": head_t, "angles_deg": head_a}

# Hip circles on the wiggle beats: side sway (hip roll + ankle roll) and front-back sway
# (hip pitch + ankle pitch), a quarter turn apart. Hip and ankle move in opposite directions,
# so the torso stays upright and the feet flat.
# v3 with 5 and 4 degrees fell at full speed: the fast shifts swing the body with no balancer on.
SIDE_DEG, FRONT_DEG = 2.5, 2
QUARTERS = [(1, 0), (0, 1), (-1, 0), (0, -1)]
circle = [(12, 0, 0)]
circle += [(13 + q / 2, *QUARTERS[q % 4]) for q in range(7)] + [(16.5, 0, 0)]
circle += [(28.5, 0, 0)] + [(29 + q / 2, *QUARTERS[q % 4]) for q in range(5)]
circle_t = [t(b) for b, _, _ in circle] + [end_time]
side = [x * SIDE_DEG for _, x, _ in circle] + [0]
front = [y * FRONT_DEG for _, _, y in circle] + [0]
# Offsets on the measured Stand angles: a leg track must start and end where the robot stands.
for s in ("L", "R"):
    tracks[s + "HipRoll"] = {"times": circle_t, "angles_deg": [STAND[s + "HipRoll"] + a for a in side]}
    tracks[s + "AnkleRoll"] = {"times": circle_t, "angles_deg": [STAND[s + "AnkleRoll"] - a for a in side]}
    tracks[s + "HipPitch"] = {"times": circle_t, "angles_deg": [STAND[s + "HipPitch"] + a for a in front]}
    tracks[s + "AnklePitch"] = {"times": circle_t, "angles_deg": [STAND[s + "AnklePitch"] - a for a in front]}

anim = Animation.model_validate({
    "name": "macarena",
    "description": "Macarena, two rounds at 103 BPM: arm sequence, then hip circles with a head sway; back to Stand.",
    "version": 5,
    "author": "agent:claude",
    "reviewed_by": "",
    "posture": "Stand",
    "duration_s": end_time,
    "uses_legs": True,
    "interpolation": "bezier",
    "tracks": tracks,
})
header = (
    "# The hip circles move the legs (hip and ankle roll and pitch, feet flat; the jump turn is left out).\n"
    "# A leg animation validates only with reviewed_by set, by the person who watches the robot.\n"
    "# NAOqi's arm collision protection may shorten the hands-to-chest move.\n"
)
text = anim.to_yaml()
lines = text.splitlines(keepends=True)
idx = next(i for i, line in enumerate(lines) if line.startswith("reviewed_by"))
Path(sys.argv[1]).write_text("".join(lines[:idx]) + header + "".join(lines[idx:]))
print("wrote", sys.argv[1], f"{end_time} s,", len(tracks), "tracks")
