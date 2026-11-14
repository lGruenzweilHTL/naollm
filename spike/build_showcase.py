# Ports the Gangnam (Kürnberghalle V6) and PowerDays Disco behaviors into choregraphe/showcase.
# Run: python3 spike/build_showcase.py choregraphe/showcase (needs ../Robotics-Choreograph-Challenges)
# Both start and end in Stand and stop on posture failure; Gangnam keeps its sound clip, volume and Rest boxes are dropped.
# Overwrites both behavior.xar files: Choregraphe edits made to them since are lost.
import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SRC = Path("/home/lukas/Github/Robotics-Choreograph-Challenges")
OUT = Path(sys.argv[1])
NS_URI = "http://www.ald.softbankrobotics.com/schema/choregraphe/project.xsd"
NS = "{" + NS_URI + "}"
ET.register_namespace("", NS_URI)


def diagram(box):
    return box.find(f"{NS}Timeline/{NS}BehaviorLayer/{NS}BehaviorKeyframe/{NS}Diagram")


def child(diag, box_id):
    return next(b for b in diag.findall(NS + "Box") if b.get("id") == box_id)


def link(diag, out_owner, out_index, in_owner, in_index):
    ET.SubElement(diag, NS + "Link", {"inputowner": in_owner, "indexofinput": in_index,
                                       "outputowner": out_owner, "indexofoutput": out_index})


def end_stand_copy(stand_up, new_id):
    box = copy.deepcopy(stand_up)
    box.set("id", new_id)
    box.set("name", "Stand Up (end)")
    box.set("x", str(int(float(box.get("x", "0"))) + 600))
    return box


def write(tree, name):
    """Box scripts go back into CDATA. ElementTree drops CDATA and merges the code with the
    XML indentation around it, which Choregraphe then reads as an IndentationError."""
    path = OUT / name / "behavior.xar"
    path.parent.mkdir(parents=True, exist_ok=True)
    codes = []
    for content in tree.getroot().iter(NS + "content"):
        code = (content.text or "").strip()
        if code:
            assert "]]>" not in code
            content.text = f"@@CDATA{len(codes)}@@"
            codes.append(code)
    text = ET.tostring(tree.getroot(), encoding="unicode")
    for i, code in enumerate(codes):
        text = text.replace(f"@@CDATA{i}@@", f"<![CDATA[{code}]]>", 1)
    path.write_text("<?xml version='1.0' encoding='UTF-8'?>\n" + text, encoding="utf-8")
    print("wrote", path, f"({len(codes)} scripts)")


# --- Gangnam: keep one copy of the chain, drop volume, rest, comment and sound boxes.
tree = ET.parse(SRC / "Kürnberghalle_Programme/Tanz/V6/GangnamStyle/behavior.xar")
root = tree.getroot().find(NS + "Box")
rdiag = diagram(root)
gangnam = child(rdiag, "5")
for b in list(rdiag):
    if b is not gangnam:
        rdiag.remove(b)
link(rdiag, "0", "2", "5", "2")      # root onStart -> GangnamStyle onStart
link(rdiag, "5", "4", "0", "4")      # GangnamStyle onStopped -> root onStopped
gdiag = diagram(gangnam)
for l in list(gdiag.findall(NS + "Link")):
    if l.get("inputowner") == "0":   # stand -> parent onStopped: replaced by the end posture below
        gdiag.remove(l)
gdiag.append(end_stand_copy(child(gdiag, "4"), "5"))
link(gdiag, "3", "4", "5", "2")      # stand pose -> Stand Up (end)
link(gdiag, "5", "4", "0", "4")      # success -> stopped
link(gdiag, "5", "5", "0", "4")      # failure -> stopped
link(gdiag, "4", "5", "0", "4")      # first Stand Up failure -> stopped, so the bridge is not left waiting
(OUT / "dance_gangnam").mkdir(parents=True, exist_ok=True)
write(tree, "dance_gangnam")
import shutil
shutil.copy(SRC / "Kürnberghalle_Programme/Tanz/V6/GangnamStyle/court-boom.wav", OUT / "dance_gangnam" / "court-boom.wav")

# --- Disco: Stand Up -> Disco -> Stand Up (end) instead of Sit Down; stop at the end.
tree = ET.parse(SRC / "PowerDays_Dance/behavior_1/behavior.xar")
root = tree.getroot().find(NS + "Box")
rdiag = diagram(root)
sit = child(rdiag, "2")
idx = list(rdiag).index(sit)
rdiag.remove(sit)
rdiag.insert(idx, end_stand_copy(child(rdiag, "1"), "2"))
link(rdiag, "2", "4", "0", "4")
link(rdiag, "2", "5", "0", "4")
link(rdiag, "1", "5", "0", "4")
write(tree, "dance_disco")
