"""Docker Execution Sandbox for OptiLoop."""
from __future__ import annotations

import concurrent.futures
import io
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import docker as docker_sdk

logger = logging.getLogger(__name__)

DOCKER_IMAGE = "python:3.11-slim"

# Locally-built sandbox image with testing dependencies pre-installed.
# Built on first use (host-wide cache), falling back to DOCKER_IMAGE if
# image building is unavailable (offline / constrained hosts).
SANDBOX_IMAGE_NAME = "optiloop-sandbox:latest"
SANDBOX_DOCKERFILE = (
    f"FROM {DOCKER_IMAGE}\n"
    "RUN pip install --no-cache-dir pytest pytest-cov\n"
)
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
        self._image_used = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def _ensure_image(self, client):
        """Return an image tag with pytest pre-installed.

        Prefers the locally-built optiloop-sandbox:latest tag. If absent,
        builds it from self.image; if the build fails (offline / no build
        support), falls back to pulling/running the base self.image.
        """
        # 1) Already-built sandbox image exists on the host.
        try:
            client.images.get(SANDBOX_IMAGE_NAME)
            return SANDBOX_IMAGE_NAME
        except docker_sdk.errors.ImageNotFound:
            pass

        # 2) Build optiloop-sandbox:latest from python:3.11-slim + pytest.
        logger.info("Building sandbox image %s ...", SANDBOX_IMAGE_NAME)
        try:
            client.images.build(
                fileobj=io.BytesIO(SANDBOX_DOCKERFILE.encode("utf-8")),
                tag=SANDBOX_IMAGE_NAME,
                rm=True,
            )
            return SANDBOX_IMAGE_NAME
        except Exception as exc:
            logger.warning("Sandbox image build failed (%s); falling back to %s",
                           exc, self.image)

        # 3) Last resort: ensure the bare base image is present.
        try:
            client.images.get(self.image)
        except docker_sdk.errors.ImageNotFound:
            logger.info("Pulling image %s ...", self.image)
            client.images.pull(self.image)
        return self.image

    def start(self):
        """Build/prepare sandbox image, create workspace, start container."""
        client = docker_sdk.from_env()
        self.workspace.mkdir(parents=True, exist_ok=True)

        image = self._ensure_image(client)
        self._image_used = image

        self._container = client.containers.run(
            image,
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
        logger.info("Container %s started for task %s (image=%s)",
                    self._container.short_id, self.task_id, self._image_used)

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
        try:
            # NOTE: the official Docker SDK exec_run() does not accept a
            # `timeout` kwarg, so we enforce timeouts here by running the
            # call on a thread-pool and bounding future.result().
            future = self._executor.submit(
                self._container.exec_run,
                ["bash", "-c", cmd],
                workdir=CONTAINER_WORKSPACE,
                demux=True,
            )
            exit_code, output = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("Command timed out after %ss: %s", timeout, cmd)
            return {"stdout": "", "stderr": f"Command timed out after {timeout}s",
                    "exit_code": -1, "duration_sec": round(time.time() - t0, 4)}

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
        # Release executor threads. Embedded commands that are still blocked
        # on the docker socket are daemon threads owned by the executor.
        self._executor.shutdown(wait=False)
        if self.workspace.exists():
            shutil.rmtree(self.workspace, ignore_errors=True)
            logger.info("Workspace %s removed", self.workspace)

