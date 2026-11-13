"""Password-free SSH to the robot for package deploys (package-deploy spec).

The system `ssh-copy-id` asks for the robot's password on the terminal itself,
so this program never reads, stores or logs it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

Runner = Callable[[List[str]], int]
KEY_PREFERENCE = ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")
KEYGEN = "ssh-keygen -t ed25519"


def run_quiet(cmd: List[str]) -> int:
    return subprocess.run(cmd, check=False, capture_output=True).returncode


def run_interactive(cmd: List[str]) -> int:
    return subprocess.run(cmd, check=False).returncode


def key_login_command(host: str) -> List[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
            f"nao@{host}", "true"]


def public_key(home: Path) -> Optional[Path]:
    ssh_dir = home / ".ssh"
    for name in KEY_PREFERENCE:
        if (ssh_dir / name).is_file():
            return ssh_dir / name
    keys = sorted(ssh_dir.glob("*.pub")) if ssh_dir.is_dir() else []
    return keys[0] if keys else None


def ensure_key_login(host: str, out: Callable[[str], None], check: Runner = run_quiet,
                     interactive: Runner = run_interactive, home: Optional[Path] = None) -> Tuple[bool, str, str]:
    """Returns (passed, detail, hint)."""
    if check(key_login_command(host)) == 0:
        return True, f"key login to nao@{host} works", ""
    key = public_key(home or Path.home())
    if key is None:
        return False, "no public key in ~/.ssh", f"create one with '{KEYGEN}', then run init again"
    out(f"Installing {key} on the robot. ssh-copy-id asks for the robot's 'nao' password; "
        "this program does not see it.")
    if interactive(["ssh-copy-id", "-i", str(key), f"nao@{host}"]) != 0:
        return False, "ssh-copy-id failed", f"run 'ssh-copy-id -i {key} nao@{host}' by hand"
    if check(key_login_command(host)) != 0:
        return False, "the key was copied, but key login still fails", f"check 'ssh -v nao@{host}'"
    return True, f"installed {key.name}; key login to nao@{host} works", ""
