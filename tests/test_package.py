"""`naollm deploy` with a stubbed qipkg (package-deploy spec)."""

import re
import shutil
from pathlib import Path

import pytest

from conftest import make_config
from naollm.deploy.package import (
    Deploy,
    DeployError,
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


class Qipkg:
    """Records commands; `make-package` writes the package file unless told to fail."""

    def __init__(self, build=0, install=0):
        self.build, self.install = build, install
        self.commands = []

    def __call__(self, cmd):
        self.commands.append(cmd)
        if cmd[1] == "make-package":
            if self.build == 0:
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"pkg")
            return self.build
        return self.install


def motors_config(**extra):
    return make_config(motion={"motors_enabled": True}, **extra)


def deploy(project, qipkg, installed=("showcase/dance_macarena", "showcase/dance_robot", "showcase/gesture_bow"),
           config=None):
    lines = []
    d = Deploy(config or motors_config(), "10.0.0.2", lines.append, run=qipkg, qipkg="qipkg",
               installed=lambda: list(installed), project_dir=project)
    return d, d.run(), lines


def version(project):
    return parse_manifest((project / "manifest.xml").read_text()).get("version")


def test_repository_project_is_a_valid_skeleton():
    root = parse_manifest((PROJECT / "manifest.xml").read_text())
    assert root.get("uuid") == "showcase"
    assert auto_start_behaviors(root) == []
    assert 'name="showcase"' in (PROJECT / "showcase.pml").read_text()


def test_successful_deploy_bumps_the_patch(project):
    qipkg = Qipkg()
    d, code, lines = deploy(project, qipkg)
    assert code == 0, lines
    assert version(project) == "0.0.2"
    make, install = qipkg.commands
    assert make[:3] == ["qipkg", "make-package", str(project / "showcase.pml")]
    assert make[-1].endswith("showcase-0.0.2.pkg")
    assert install[1:2] == ["deploy-package"] and install[-2:] == ["--url", "nao@10.0.0.2"]
    assert [line[0] for line in lines] == ["✓"] * 5


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
    assert [c[1] for c in qipkg.commands] == ["make-package"]
    assert any("nothing was sent" in line for line in lines)


def test_failed_install_undoes_the_bump(project):
    _, code, _ = deploy(project, Qipkg(install=2))
    assert code != 0 and version(project) == "0.0.1"


def test_missing_behavior_after_deploy_is_named(project):
    _, code, lines = deploy(project, Qipkg(), installed=["showcase/dance_macarena", "showcase/gesture_bow"])
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
