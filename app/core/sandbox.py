"""Docker Execution Sandbox for OptiLoop."""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import docker as docker_sdk

logger = logging.getLogger(__name__)

DOCKER_IMAGE = "python:3.11-slim"
WORKSPACE_BASE = Path(tempfile.gettempdir()) / "optiloop_workspaces"
CONTAINER_WORKSPACE = "/workspace"
KEEPALIVE_CMD = ["tail", "-f", "/dev/null"]
DEFAULT_MEM_LIMIT = "512m"
DEFAULT_CPU_QUOTA = 50000
DEFAULT_TIMEOUT = 60

BLOCKED_PATTERNS: list[str] = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "dd of=",
    "> /dev/sd",
    "chmod -R 777 /",
    "wget ",
    "curl ",
    " nc ",
    "ncat ",
    "eval(",
    "exec(",
    "subprocess",
    "import ctypes",
    "docker ",
    "kubectl ",
    "mount ",
    "umount ",
    "fdisk",
    "parted",
    "cryptsetup",
    "chroot /",
]


def _is_command_safe(cmd: str) -> tuple[bool, str]:
    """Check cmd against the safety blocklist. Returns (is_safe, reason)."""
    lower = cmd.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in lower:
            return False, f"Blocked pattern detected: '{pattern}'"
    return True, ""


class DockerSandbox:
    """Manages an isolated Docker container for code execution."""

    def __init__(self, task_id, image=None, mem_limit=None, cpu_quota=None):
        self.task_id = task_id
        self.image = image or DOCKER_IMAGE
        self.mem_limit = mem_limit or DEFAULT_MEM_LIMIT
        self.cpu_quota = cpu_quota if cpu_quota is not None else DEFAULT_CPU_QUOTA
        self.workspace = WORKSPACE_BASE / task_id
        self._container = None

    def start(self):
        """Pull image if needed, create workspace, start container."""
        client = docker_sdk.from_env()
        self.workspace.mkdir(parents=True, exist_ok=True)

        try:
            client.images.get(self.image)
        except docker_sdk.errors.ImageNotFound:
            logger.info("Pulling image %s ...", self.image)
            client.images.pull(self.image)

        self._container = client.containers.run(
            self.image,
            command=KEEPALIVE_CMD,
            detach=True,
            volumes={str(self.workspace): {"bind": CONTAINER_WORKSPACE, "mode": "rw"}},
            working_dir=CONTAINER_WORKSPACE,
            mem_limit=self.mem_limit,
            cpu_quota=self.cpu_quota,
            privileged=False,
            security_opt=["no-new-privileges"],
            auto_remove=False,
        )
        logger.info("Container %s started for task %s", self._container.short_id, self.task_id)

    def run_command(self, cmd, timeout=None):
        """Execute cmd inside the container. Returns dict with output details."""
        if self._container is None:
            raise RuntimeError("Sandbox not started. Call start() first.")
        if timeout is None:
            timeout = DEFAULT_TIMEOUT

        is_safe, reason = _is_command_safe(cmd)
        if not is_safe:
            return {"stdout": "", "stderr": f"BLOCKED: {reason}",
                    "exit_code": -1, "duration_sec": 0.0}

        t0 = time.time()
        exit_code, output = self._container.exec_run(
            ["bash", "-c", cmd],
            workdir=CONTAINER_WORKSPACE,
            demux=True,
            timeout=timeout,
        )
        duration = round(time.time() - t0, 4)
        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
        return {"stdout": stdout, "stderr": stderr,
                "exit_code": exit_code, "duration_sec": duration}

    def write_file(self, relative_path, content):
        """Write content to a file inside the workspace."""
        dest = self.workspace / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    def read_file(self, relative_path):
        """Read and return the contents of a workspace file."""
        src = self.workspace / relative_path
        if not src.exists():
            raise FileNotFoundError(f"File not found: {relative_path}")
        return src.read_text(encoding="utf-8")

    def get_diff(self):
        """Return git diff of the workspace. Initialise git if needed."""
        if not (self.workspace / ".git").exists():
            self.run_command("git init")
            self.run_command('git config user.email "optiloop@local"')
            self.run_command('git config user.name "OptiLoop"')
            self.run_command("git add -A")
            self.run_command('git commit -m "initial" --allow-empty')
        result = self.run_command("git diff HEAD")
        return result["stdout"]

    def stop(self):
        """Force-remove the container and clean up workspace files."""
        if self._container is not None:
            try:
                self._container.remove(force=True)
            except Exception as exc:
                logger.warning("Container removal failed: %s", exc)
            self._container = None
        if self.workspace.exists():
            shutil.rmtree(self.workspace, ignore_errors=True)
            logger.info("Workspace %s removed", self.workspace)

