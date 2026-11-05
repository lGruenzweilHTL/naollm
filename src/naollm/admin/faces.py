"""The `faces` commands and guided enrolment (SPEC §18.6), adapted to motors off.

Order protects the record: a face is never kept without a record, and a record is
never lost while its face exists. Names and form IDs go to the log only through
`text=`, which the event log drops in event mode.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import TYPE_CHECKING, List, Optional

from naollm.admin.consent import ConsentRecord, ConsentRegistry, RegistryDamaged
from naollm.state import State
from naollm.tools.messages import msg
from naollm.tools.schemas import STATUS_COLOURS

if TYPE_CHECKING:
    from naollm.admin.commands import Commands
    from naollm.audio.listener import Listener
    from naollm.bridge import Bridge

LEARN_TRIES = 3
MAX_NAME = 32


def valid_name(name: str) -> bool:
    return (1 <= len(name) <= MAX_NAME and name == name.strip()
            and all(c.isalnum() or c in " -" for c in name) and any(c.isalnum() for c in name))


class _Aborted(Exception):
    """Enrolment stopped: the state changed, or the emergency stop fired."""


class Faces:
    def __init__(self, bridge: "Bridge", registry: ConsentRegistry, commands: "Commands",
                 listener: Optional["Listener"] = None) -> None:
        self.bridge = bridge
        self.registry = registry
        self.commands = commands
        self.listener = listener

    @property
    def perception(self):
        return self.bridge.robot.perception

    def out(self, text: str) -> None:
        self.commands.out(text)

    def _log(self, message: str, text: Optional[str] = None) -> None:
        self.bridge.log.log("admin", message, source="admin", text=text)

    async def _ask(self, question: str) -> str:
        return await self.commands.confirm(question)

    # -- startup ---------------------------------------------------------
    async def startup_check(self) -> None:
        """Lists overdue records and deletes only what the operator confirms."""
        try:
            overdue = self.registry.overdue()
        except RegistryDamaged as err:
            self.bridge.log.log("alert", f"CONSENT REGISTRY DAMAGED: {err}. Enrolment is blocked; the file is unchanged")
            return
        if not overdue:
            return
        self.bridge.log.log("alert", f"{len(overdue)} consent record(s) past their deletion date")
        for record in overdue:
            self.out(f"  overdue: {record.name} (form {record.consent_form_id}, delete after {record.delete_after})")
        answer = await self._ask(f"Delete these {len(overdue)} face(s) and record(s) now? [y/N] ")
        if answer.strip().lower() != "y":
            self.out("nothing deleted; this warning returns at the next start")
            self._log("overdue faces kept by the operator")
            return
        for record in overdue:
            await self._delete_one(record.name)

    # -- list ------------------------------------------------------------
    async def list(self) -> None:
        try:
            records = self.registry.load()
        except RegistryDamaged as err:
            self.bridge.log.log("alert", f"CONSENT REGISTRY DAMAGED: {err}")
            self.out(f"faces: {err}")
            return
        learned: Optional[List[str]]
        try:
            learned = await asyncio.to_thread(self.perception.learned_names)
        except Exception as err:
            learned = None
            self.out(f"  cannot read the robot's list: {err}")
        for r in records:
            mark = "  NO FACE ON ROBOT" if learned is not None and r.name not in learned else ""
            self.out(f"  {r.name}  form {r.consent_form_id}  delete after {r.delete_after}{mark}")
        known = {r.name for r in records}
        for name in learned or []:
            if name not in known:
                self.out(f"  {name}  NO CONSENT RECORD: remove it with 'faces delete {name}'")
        if not records and not learned:
            self.out("  no faces enrolled")

    # -- enrol -----------------------------------------------------------
    async def enroll(self, name: str) -> None:
        b = self.bridge
        problem = self._enrol_problem(name)
        if problem is None:
            problem = await self._registry_problem(name)
        if problem is not None:
            self.out(f"faces enroll: refused, {problem}")
            self._log("enrolment refused", text=problem)  # the reason can contain the name
            return
        form_id = (await self._ask("Consent form signed? Enter form ID (or blank to abort): ")).strip()
        if not form_id:
            self.out("faces enroll: aborted, no consent form ID")
            self._log("enrolment aborted: no consent form ID")
            return
        delete_after = await self._ask_deletion_date()
        if b.state.state != State.IDLE:
            self.out(f"faces enroll: aborted, state is {b.state.state.value}")
            self._log("enrolment aborted: state changed")
            return

        b.exclusive = "enrolment"
        if self.listener is not None:
            await asyncio.to_thread(self.listener.hold_wake)
        handed_over = False
        try:
            await self._prepare()
            handed_over = True
            job = self._run_learning(name, form_id, delete_after)
            if self.commands.spawn is not None:
                # The console prompt, and Ctrl+E with it, must stay alive while the robot learns.
                self.commands.spawn(job)
            else:
                await job
        except _Aborted as err:
            self.out(f"faces enroll: aborted, {err}; nothing stored")
            self._log("enrolment aborted: nothing stored")
        finally:
            if not handed_over:
                await self._release()

    async def _prepare(self) -> None:
        """The last step that can ask the operator something."""
        b = self.bridge
        self._check_state()
        await asyncio.to_thread(b.robot.speech.set_eyes, STATUS_COLOURS["green"])
        if b.motors_enabled:
            await asyncio.to_thread(self.perception.set_tracking, True)
        else:
            await self._ask("Head tracking is off: position the robot's head by hand so the camera "
                            "sees the face, then press Enter. ")
        self._check_state()

    async def _run_learning(self, name: str, form_id: str, delete_after: str) -> None:
        b = self.bridge
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._learn(name, form_id, delete_after))
        b.register_turn(lambda: loop.call_soon_threadsafe(task.cancel))
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                task.cancel()
                await asyncio.wait([task])
                raise
            self.out("faces enroll: stopped, nothing stored")
            self._log("enrolment stopped by the emergency stop: nothing stored")
        finally:
            b.register_turn(None)
            await self._release()

    async def _release(self) -> None:
        b = self.bridge
        b.exclusive = None
        if self.listener is not None:
            await asyncio.to_thread(self.listener.release_wake)
        if b.state.state == State.IDLE:
            try:
                await asyncio.to_thread(b.restore_eyes)
            except Exception as err:
                b.log.log("error", f"eyes failed: {err!r}")

    def _enrol_problem(self, name: str) -> Optional[str]:
        b = self.bridge
        if not b.config.privacy.face_recognition_enabled:
            return "privacy.face_recognition_enabled is false, which is the setting until the school has cleared this"
        if b.state.state != State.IDLE or b.exclusive:
            return f"needs IDLE (state {b.state.state.value})"
        if self.commands.conversation is not None and self.commands.conversation.busy:
            return "a turn is still running"
        if not self.perception.available:
            return "perception is not available on this robot"
        if not valid_name(name):
            return f"a name is 1 to {MAX_NAME} letters, digits, spaces or hyphens"
        return None

    async def _registry_problem(self, name: str) -> Optional[str]:
        try:
            if self.registry.find(name) is not None:
                return f"{name} already has a record: use 'faces delete {name}' first"
        except RegistryDamaged as err:
            self.bridge.log.log("alert", f"CONSENT REGISTRY DAMAGED: {err}")
            return "the consent registry is damaged"
        try:
            if name in await asyncio.to_thread(self.perception.learned_names):
                return f"{name} is on the robot with no record: use 'faces delete {name}' first"
        except Exception as err:
            return f"cannot read the robot's list ({err})"
        return None

    async def _ask_deletion_date(self) -> str:
        today = date.today()
        default = today + timedelta(days=1)
        while True:
            answer = (await self._ask(f"Deletion date [YYYY-MM-DD, Enter for {default.isoformat()}]: ")).strip()
            if not answer:
                return default.isoformat()
            try:
                chosen = date.fromisoformat(answer)
            except ValueError:
                self.out(f"'{answer}' is not a date")
                continue
            if chosen < today:
                self.out("that date is in the past")
                continue
            return chosen.isoformat()

    def _check_state(self) -> None:
        if self.bridge.state.state != State.IDLE:
            raise _Aborted(f"state is {self.bridge.state.state.value}")

    async def _learn(self, name: str, form_id: str, delete_after: str) -> None:
        b = self.bridge
        speech = b.robot.speech
        lang = b.language
        learned = False
        try:
            await asyncio.to_thread(speech.say, msg("enrol_look", lang), False)
            for attempt in range(1, LEARN_TRIES + 1):
                self._check_state()
                if attempt > 1:
                    await asyncio.to_thread(speech.say, msg("enrol_hint", lang), False)
                    self._check_state()
                learned = await self._learn_once(name)
                if learned:
                    break
            if not learned:
                self.out(f"faces enroll: failed after {LEARN_TRIES} tries, nothing stored")
                self._log("enrolment failed: nothing stored")
                await self._forget_if_present(name)
                return
            self._check_state()
            record = ConsentRecord(name, form_id, date.today().isoformat(), delete_after)
            try:
                await asyncio.to_thread(self.registry.add, record)
            except (OSError, RegistryDamaged) as err:
                await self._forget_if_present(name)
                self.out(f"faces enroll: the record could not be written ({err}); the face was forgotten")
                self.bridge.log.log("alert", "consent record write failed: the face was forgotten", source="admin")
                learned = False
                return
            learned = False  # kept from here on: a record now exists
            self._log("face enrolled", text=name)
            self.out(f"enrolled; delete after {delete_after}")
            await asyncio.to_thread(speech.say, msg("enrol_done", lang, name=name), False)
        except _Aborted as err:
            self.out(f"faces enroll: aborted, {err}; nothing stored")
            self._log("enrolment aborted: nothing stored")
            await self._forget_if_present(name)
        except asyncio.CancelledError:
            if learned:
                await asyncio.shield(self._forget_if_present(name))
            raise

    async def _learn_once(self, name: str) -> bool:
        """A stop waits for a try that has already started, then forgets what it stored."""
        call = asyncio.ensure_future(asyncio.to_thread(self.perception.learn_face, name))
        try:
            return bool(await asyncio.shield(call))
        except asyncio.CancelledError:
            await asyncio.wait([call])
            await asyncio.shield(self._forget_if_present(name))
            raise
        except Exception as err:
            self.bridge.log.log("warning", f"a learning try failed: {type(err).__name__}", source="admin")
            return False

    async def _forget_if_present(self, name: str) -> None:
        try:
            if name in await asyncio.to_thread(self.perception.learned_names):
                await asyncio.to_thread(self.perception.forget_face, name)
        except Exception as err:
            self.bridge.log.log("alert", f"could not check or forget a face after enrolment: {type(err).__name__}. "
                                         "Check 'faces list'", source="admin")

    # -- delete ----------------------------------------------------------
    async def delete(self, name: str) -> None:
        try:
            self.registry.load()
        except RegistryDamaged as err:
            self.bridge.log.log("alert", f"CONSENT REGISTRY DAMAGED: {err}")
            self.out("faces delete: refused, the consent registry is damaged")
            return
        await self._delete_one(name)

    async def _delete_one(self, name: str) -> None:
        """Forget first: a failure keeps the record, so the deletion can be retried."""
        try:
            learned = await asyncio.to_thread(self.perception.learned_names)
            on_robot = name in learned
            if on_robot and not await asyncio.to_thread(self.perception.forget_face, name):
                raise RuntimeError("the robot did not forget the face")
        except Exception as err:
            self.out(f"faces delete: failed on the robot ({err}); the record stays, so you can try again")
            self.bridge.log.log("alert", "face deletion failed on the robot: the record stays", source="admin")
            return
        known = self.registry.find(name) is not None
        if not on_robot and not known:
            self.out(f"faces delete: {name} is neither on the robot nor in the registry")
            return
        if known:
            await asyncio.to_thread(self.registry.remove, name)
        self._log("face deleted", text=name)
        self.out(f"deleted {name}")

    async def delete_all(self) -> None:
        try:
            self.registry.load()
        except RegistryDamaged as err:
            self.bridge.log.log("alert", f"CONSENT REGISTRY DAMAGED: {err}")
            self.out("faces delete: refused, the consent registry is damaged")
            return
        answer = await self._ask("This forgets every face on the robot and every record. Type DELETE to confirm: ")
        if answer != "DELETE":
            self.out("nothing deleted")
            return
        try:
            if not await asyncio.to_thread(self.perception.forget_all_faces):
                raise RuntimeError("the robot still lists learned faces")
        except Exception as err:
            self.out(f"faces delete --all: failed on the robot ({err}); the records stay, so you can try again")
            self.bridge.log.log("alert", "face deletion failed on the robot: the records stay", source="admin")
            return
        await asyncio.to_thread(self.registry.clear)
        self._log("all faces deleted")
        self.out("all faces and records deleted")
