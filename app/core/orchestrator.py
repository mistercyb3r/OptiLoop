"""Tri-Agent Orchestrator Loop for OptiLoop.

Coordinates Planner, Executor, and Reviewer agents in an iterative
loop inside a Docker sandbox, with budget enforcement.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlmodel import Session

from app.core.cost_calculator import CostCalculator
from app.core.router import ModelRouter
from app.core.sandbox import DockerSandbox
from app.core.prompts import PLANNER_PROMPT, EXECUTOR_PROMPT, REVIEWER_PROMPT
from app.models.db_models import Task, AgentRun, ExecutionLog

logger = logging.getLogger(__name__)

_MAX_TOKENS_DEFAULT = 4096


def _parse_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from LLM text output."""
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    for marker in ("```json", "```"):
        if marker in text:
            start = text.index(marker) + len(marker)
            end = text.rfind("```")
            if end > start:
                try:
                    return json.loads(text[start:end].strip())
                except json.JSONDecodeError:
                    pass
    return {}


class Orchestrator:
    """Coordinates the Planner-Executor-Reviewer loop for a single task.

    Parameters
    ----------
    db_session:
        SQLModel session for database operations.
    router:
        ModelRouter instance (or creates a default one).
    cost_calculator:
        CostCalculator instance (or creates a default one).
    """

    def __init__(
        self,
        db_session: Session,
        router: ModelRouter | None = None,
        cost_calculator: CostCalculator | None = None,
    ) -> None:
        self.db = db_session
        self.calculator = cost_calculator or CostCalculator()
        self.router = router or ModelRouter(cost_calculator=self.calculator)
        self.sandbox: DockerSandbox | None = None

    async def run_task(self, task_id: str, max_iterations: int = 10,
                       model_override: str = "") -> Task:
        """Run the full Planner-Executor-Reviewer loop for a task.

        If model_override is set, all agents use that specific model.
        Returns the updated Task object.
        """
        task = self.db.get(Task, task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        # Store override for step methods
        self._model_override = model_override

        task.status = "running"
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        self.sandbox = DockerSandbox(task_id=task.id)

        try:
            self.sandbox.start()

            for iteration in range(1, max_iterations + 1):
                logger.info("=== Iteration %d / %d for task %s ===",
                            iteration, max_iterations, task.id)

                # --- PLANNER ---
                plan_result = await self._planner_step(task, iteration)

                # --- EXECUTOR ---
                exec_result = await self._executor_step(task, iteration)

                # --- REVIEWER ---
                review = await self._reviewer_step(task, iteration)

                # --- Budget & state check ---
                self.db.refresh(task)
                if (task.target_budget_usd is not None
                        and self.calculator.is_over_budget(
                            task.total_spent_usd, task.target_budget_usd)):
                    task.status = "failed"
                    self._log(task.id, "reasoning",
                              "Budget exceeded - stopping")
                    self.db.add(task)
                    self.db.commit()
                    logger.warning("Task %s failed: budget exceeded", task.id)
                    break

                if review.get("status") == "APPROVED":
                    task.status = "completed"
                    self.db.add(task)
                    self.db.commit()
                    logger.info("Task %s completed at iteration %d",
                                task.id, iteration)
                    break
            else:
                # Exhausted all iterations
                task.status = "failed"
                self._log(task.id, "reasoning",
                          f"Max iterations ({max_iterations}) reached")
                self.db.add(task)
                self.db.commit()
                logger.warning("Task %s failed: max iterations reached", task.id)

        finally:
            if self.sandbox:
                self.sandbox.stop()

        self.db.refresh(task)
        return task

    # --- Step helpers ------------------------------------------------------

    async def _planner_step(self, task, iteration):
        """Execute the planner agent and return parsed plan."""
        model = self.router.select_model(
            "planner",
            target_budget_usd=task.target_budget_usd or 0.0,
            total_spent_usd=task.total_spent_usd,
        )
        run = self._create_run(task.id, "planner", model, iteration)

        context = f"Task: {task.prompt}"
        if iteration > 1:
            last_review = self._get_last_reviewer_feedback(task.id)
            if last_review:
                context += f"\n\nReviewer feedback: {last_review}"
        messages = [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": context},
        ]

        result = await self.router.call_llm(messages, model, run.id, self.db, override_model=self._model_override or None)
        self._update_task_cost(task, result["cost_usd"])
        self._log(task.id, "reasoning",
                  f"[Planner iter={iteration}] {result['text'][:500]}")

        run.status = "completed"
        self.db.add(run)
        self.db.commit()
        return _parse_json(result["text"])

    async def _executor_step(self, task, iteration):
        """Execute the executor agent and perform sandbox actions."""
        model = self.router.select_model(
            "executor",
            target_budget_usd=task.target_budget_usd or 0.0,
            total_spent_usd=task.total_spent_usd,
        )
        run = self._create_run(task.id, "executor", model, iteration)

        workspace_state = ""
        try:
            diff = self.sandbox.get_diff()
            workspace_state = f"Current diff:\n{diff}"
        except Exception:
            workspace_state = "Workspace is empty."

        context = (f"Task: {task.prompt}\n"
                   f"Workspace state:\n{workspace_state}")
        messages = [
            {"role": "system", "content": EXECUTOR_PROMPT},
            {"role": "user", "content": context},
        ]

        result = await self.router.call_llm(messages, model, run.id, self.db, override_model=self._model_override or None)
        self._update_task_cost(task, result["cost_usd"])

        plan = _parse_json(result["text"])
        exec_results = plan.get("results", [])

        for item in exec_results:
            action = item.get("action", "")
            if action == "write_file":
                p = item.get("path", "")
                c = item.get("content", "")
                if p and c:
                    self.sandbox.write_file(p, c)
                    self._log(task.id, "diff", f"Wrote file: {p}")
            elif action == "run_command":
                cmd = item.get("command", "")
                if cmd:
                    cr = self.sandbox.run_command(cmd)
                    self._log(task.id, "command",
                              f"$ {cmd}\nexit={cr['exit_code']}"
                              f"\nstdout={cr['stdout'][:500]}"
                              f"\nstderr={cr['stderr'][:500]}")

        self._log(task.id, "diff",
                  f"[Executor iter={iteration}] {plan.get('diff_summary', '')[:500]}")
        run.status = "completed"
        self.db.add(run)
        self.db.commit()
        return plan

    async def _reviewer_step(self, task, iteration):
        """Execute the reviewer agent and return approval/revision status."""
        model = self.router.select_model(
            "reviewer",
            target_budget_usd=task.target_budget_usd or 0.0,
            total_spent_usd=task.total_spent_usd,
        )
        run = self._create_run(task.id, "reviewer", model, iteration)

        test_output = ""
        try:
            tr = self.sandbox.run_command("pytest tests/ -v --tb=short",
                                          timeout=120)
            test_output = (f"exit_code={tr['exit_code']}"
                           f"\n{tr['stdout']}"
                           f"\n{tr['stderr']}")
        except Exception as exc:
            test_output = f"Test execution error: {exc}"

        diff = ""
        try:
            diff = self.sandbox.get_diff()
        except Exception:
            pass

        context = (f"Task: {task.prompt}\n"
                   f"Test output:\n{test_output}\n\n"
                   f"File diffs:\n{diff[:2000]}")
        messages = [
            {"role": "system", "content": REVIEWER_PROMPT},
            {"role": "user", "content": context},
        ]

        result = await self.router.call_llm(messages, model, run.id, self.db, override_model=self._model_override or None)
        self._update_task_cost(task, result["cost_usd"])

        review = _parse_json(result["text"])
        status_str = review.get("status", "NEEDS_REVISION")
        feedback = review.get("feedback", "No feedback provided")

        self._log(task.id, "reasoning",
                  f"[Reviewer iter={iteration}] status={status_str} "
                  f"feedback={feedback[:500]}")

        run.status = "completed"
        self.db.add(run)
        self.db.commit()
        return review

    # --- Internal helpers --------------------------------------------------

    def _create_run(self, task_id, role, model, iteration):
        """Create and persist an AgentRun record."""
        run = AgentRun(task_id=task_id, agent_role=role,
                       model_name=model, iteration=iteration,
                       status="running")
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _update_task_cost(self, task, cost_usd):
        """Accumulate cost on the task."""
        task.total_spent_usd = round(task.total_spent_usd + cost_usd, 6)
        self.db.add(task)
        self.db.commit()

    def _log(self, task_id, step_type, content):
        """Write an ExecutionLog entry."""
        log = ExecutionLog(task_id=task_id, step_type=step_type,
                           content=content)
        self.db.add(log)
        self.db.commit()

    def _get_last_reviewer_feedback(self, task_id):
        """Return content of the most recent reviewer ExecutionLog."""
        from sqlmodel import select
        stmt = (
            select(ExecutionLog)
            .where(ExecutionLog.task_id == task_id)
            .where(ExecutionLog.step_type == "reasoning")
            .order_by(ExecutionLog.timestamp.desc())
        )
        logs = self.db.exec(stmt).all()
        for log in logs:
            if "[Reviewer" in log.content:
                return log.content
        return ""
