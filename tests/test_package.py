"""`naollm deploy` with stubbed qipkg, scp, ssh and PackageManager (package-deploy spec)."""

import re
import shutil
from pathlib import Path

import pytest

from conftest import make_config
from naollm.deploy.package import (
    Deploy,
    DeployError,
    QiPackageTarget,
    auto_start_behaviors,
    bump_patch,
    parse_manifest,
    naoqi_url,
    robot_host,
    with_version,
)
from naollm.main import main

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "choregraphe" / "showcase"

AUTO_START = """<?xml version='1.0' encoding='UTF-8'?>
<package version="0.0.3" uuid="showcase">
    <contents>
        <behaviorContent path="dance_robot">
            <name lang="en_US">dance_robot</name>
            <autonomous>
                <condition purpose="launchTrigger"><![CDATA[('Launchpad/WavingDetection' == 1)]]></condition>
            </autonomous>
        </behaviorContent>
    </contents>
</package>
"""


@pytest.fixture
def project(tmp_path):
    target = tmp_path / "showcase"
    shutil.copytree(PROJECT, target)
    # The tests count from 0.0.1, whatever version the real project is at.
    manifest = target / "manifest.xml"
    manifest.write_text(re.sub(r'version="[0-9.]+"', 'version="0.0.1"', manifest.read_text(), count=1))
    return target


SHOWCASE = ("showcase/dance_macarena", "showcase/dance_robot", "showcase/gesture_bow")


class Qipkg:
    """Records commands; `make-package` writes the package file unless told to fail.

    Also stands in for `scp` and `ssh`, which return `copy` and `remove`.
    """

    def __init__(self, build=0, copy=0, remove=0):
        self.build, self.copy, self.remove = build, copy, remove
        self.commands = []

    def __call__(self, cmd):
        self.commands.append(cmd)
        if cmd[0] == "scp":
            return self.copy
        if cmd[0] == "ssh":
            return self.remove
        if cmd[1] == "make-package":
            if self.build == 0:
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"pkg")
            return self.build
        raise AssertionError(f"unexpected command {cmd}")

    def names(self):
        return [c[1] if c[0] == "qipkg" else c[0] for c in self.commands]


class Target:
    """PackageManager and ALBehaviorManager on a fake robot."""

    def __init__(self, installed=SHOWCASE, result=True, error=None):
        self.behaviors, self.result, self.error = list(installed), result, error
        self.installs, self.closed = [], 0

    def install(self, remote_path):
        self.installs.append(remote_path)
        if self.error:
            raise self.error
        return self.result

    def installed(self):
        return self.behaviors

    def close(self):
        self.closed += 1


def motors_config(**extra):
    return make_config(motion={"motors_enabled": True}, **extra)


def deploy(project, qipkg, target=None, config=None):
    lines = []
    d = Deploy(config or motors_config(), "10.0.0.2", lines.append, run=qipkg, qipkg="qipkg",
               target=target or Target(), project_dir=project)
    return d, d.run(), lines


def version(project):
    return parse_manifest((project / "manifest.xml").read_text()).get("version")


def test_repository_project_is_a_valid_skeleton():
    root = parse_manifest((PROJECT / "manifest.xml").read_text())
    assert root.get("uuid") == "showcase"
    assert auto_start_behaviors(root) == []
    assert 'name="showcase"' in (PROJECT / "showcase.pml").read_text()


def test_successful_deploy_bumps_the_patch(project):
    qipkg, target = Qipkg(), Target()
    d, code, lines = deploy(project, qipkg, target)
    assert code == 0, lines
    assert version(project) == "0.0.2"
    make, copy, remove = qipkg.commands
    assert make[:3] == ["qipkg", "make-package", str(project / "showcase.pml")]
    assert make[-1].endswith("showcase-0.0.2.pkg")
    assert copy == ["scp", "-o", "BatchMode=yes", make[-1], "nao@10.0.0.2:"]
    assert target.installs == ["/home/nao/showcase-0.0.2.pkg"]
    assert remove == ["ssh", "-o", "BatchMode=yes", "nao@10.0.0.2", "rm", "-f", "showcase-0.0.2.pkg"]
    assert target.closed == 1
    assert [line[0] for line in lines] == ["✓"] * 5


def test_failed_copy_installs_nothing_and_undoes_the_bump(project):
    qipkg, target = Qipkg(copy=1), Target()
    _, code, lines = deploy(project, qipkg, target)
    assert code != 0 and version(project) == "0.0.1"
    assert qipkg.names() == ["make-package", "scp"] and target.installs == []
    assert any(line.startswith("✗ install") and "scp" in line for line in lines)
    assert not any(line.startswith("✓ verify") for line in lines)


def test_refused_connection_fails_the_install_and_undoes_the_bump(project):
    qipkg = Qipkg()
    target = Target(error=RuntimeError("cannot connect to tcp://10.0.0.2:9561: Connect error: 111"))
    _, code, lines = deploy(project, qipkg, target)
    assert code != 0 and version(project) == "0.0.1"
    assert any(line.startswith("✗ install") and "111" in line for line in lines)
    assert qipkg.names() == ["make-package", "scp", "ssh"], "the copy is removed after a failed install"
    assert not any("verify" in line for line in lines)
    assert target.closed == 1


def test_install_returning_false_fails_the_step(project):
    _, code, lines = deploy(project, Qipkg(), Target(result=False))
    assert code != 0 and version(project) == "0.0.1"
    assert any(line.startswith("✗ install") and "returned False" in line for line in lines)


def test_failed_cleanup_is_reported_but_not_a_failure(project):
    _, code, lines = deploy(project, Qipkg(remove=255))
    assert code == 0, lines
    assert any("could not remove /home/nao/showcase-0.0.2.pkg" in line for line in lines)


def test_install_connects_to_the_configured_port(project):
    config = motors_config(robot={"url": "tcp://10.0.0.2:9561"})
    d = Deploy(config, "10.0.0.2", lambda line: None, run=Qipkg(), qipkg="qipkg", target=Target(),
               project_dir=project)
    assert d.url == "tcp://10.0.0.2:9561"
    assert Deploy(config, "10.0.0.9", lambda line: None, target=Target()).url == "tcp://10.0.0.9:9561"


class FakeManager:
    def __init__(self, result=True, missing=False):
        self.calls, self.result, self.missing = [], result, missing

    def removePkg(self, uuid):
        self.calls.append(("removePkg", uuid))
        if self.missing:
            raise RuntimeError("package not found")
        return True

    def install(self, path):
        self.calls.append(("install", path))
        return self.result


class FakeRobot:
    def __init__(self, manager):
        self.manager, self.disconnected = manager, 0

    def service(self, name):
        if name == "PackageManager":
            return self.manager
        assert name == "ALBehaviorManager"
        return type("Behaviors", (), {"getInstalledBehaviors": lambda self: ["showcase/dance_robot"]})()

    def disconnect(self):
        self.disconnected += 1


def test_qi_target_removes_the_old_package_then_installs():
    manager = FakeManager()
    robot = FakeRobot(manager)
    target = QiPackageTarget("tcp://10.0.0.2:9561", robot=robot)
    assert target.install("/home/nao/showcase-0.0.2.pkg") is True
    assert manager.calls == [("removePkg", "showcase"), ("install", "/home/nao/showcase-0.0.2.pkg")]
    assert target.installed() == ["showcase/dance_robot"]
    target.close()
    assert robot.disconnected == 0, "a borrowed connection stays open"


def test_qi_target_installs_when_nothing_was_installed_before():
    assert QiPackageTarget("u", robot=FakeRobot(FakeManager(missing=True))).install("/home/nao/p.pkg") is True


def test_qi_target_needs_a_true_result():
    assert QiPackageTarget("u", robot=FakeRobot(FakeManager(result=False))).install("/home/nao/p.pkg") is False
    assert QiPackageTarget("u", robot=FakeRobot(FakeManager(result="ok"))).install("/home/nao/p.pkg") is False


def test_bump_keeps_the_rest_of_the_file(project):
    before = (project / "manifest.xml").read_text()
    deploy(project, Qipkg())
    after = (project / "manifest.xml").read_text()
    assert after == before.replace('version="0.0.1"', 'version="0.0.2"')


def test_failed_build_sends_nothing_and_undoes_the_bump(project):
    qipkg = Qipkg(build=1)
    before = (project / "manifest.xml").read_text()
    _, code, lines = deploy(project, qipkg)
    assert code != 0
    assert (project / "manifest.xml").read_text() == before
    assert qipkg.names() == ["make-package"]
    assert any("nothing was sent" in line for line in lines)


def test_failed_install_undoes_the_bump(project):
    _, code, _ = deploy(project, Qipkg(), Target(error=OSError("broken pipe")))
    assert code != 0 and version(project) == "0.0.1"


def test_missing_behavior_after_deploy_is_named(project):
    _, code, lines = deploy(project, Qipkg(), Target(installed=["showcase/dance_macarena", "showcase/gesture_bow"]))
    assert code != 0
    assert any(line.startswith("✗ verify") and "showcase/dance_robot" in line for line in lines)


def test_auto_start_behavior_stops_before_building(project):
    (project / "manifest.xml").write_text(AUTO_START)
    qipkg = Qipkg()
    _, code, lines = deploy(project, qipkg)
    assert code != 0 and qipkg.commands == []
    assert any("dance_robot" in line and "auto-start" in line for line in lines)
    assert version(project) == "0.0.3"


def test_motors_off_refuses_before_building(project):
    qipkg = Qipkg()
    _, code, lines = deploy(project, qipkg, config=make_config(motion={"motors_enabled": False}))
    assert code != 0 and qipkg.commands == []
    assert "motors are off" in lines[0]
    assert version(project) == "0.0.1"


def test_deploy_command_refuses_with_motors_off(tmp_path, capsys):
    path = tmp_path / "c.yaml"
    path.write_text("robot: {url: 'tcp://10.0.0.2:9559'}\nmotion: {motors_enabled: false}\n")
    assert main(["deploy", "--config", str(path)]) != 0
    assert "motors are off" in capsys.readouterr().out


def test_robot_defaults_to_the_host_in_the_url():
    config = make_config()
    assert robot_host(None, config) == "10.0.0.2"
    assert robot_host("nao.local", config) == "nao.local"
    assert robot_host("tcp://10.0.0.9:9559", config) == "10.0.0.9"


def test_naoqi_url_keeps_the_configured_port_for_the_configured_robot():
    config = make_config(robot={"url": "tcp://10.0.0.2:9559"})
    assert naoqi_url("10.0.0.2", config) == "tcp://10.0.0.2:9559"
    assert naoqi_url("10.0.0.9", config) == "tcp://10.0.0.9:9561"


def test_version_helpers():
    assert bump_patch("1.2.9") == "1.2.10"
    with pytest.raises(DeployError):
        bump_patch("1.2")
    with pytest.raises(DeployError):
        with_version("<package uuid='showcase'/>", "0.0.2")
