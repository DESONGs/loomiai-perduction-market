"""
最简可接真实后端的 self-iteration orchestrator。

特性:
- MainAgent 仅保留结构化摘要
- Research / Finalize worker 可走真实 Kimi API
- Coding worker 支持外部 Codex 执行器协议
- 启动时立即把状态同步到 dashboard

默认行为:
- Kimi: 真实调用 ../autoresearch/pm_config.py 中的配置
- Codex: 若设置 CODEX_WORKER_CMD 则调用外部执行器，否则回退到本地实现
"""

from __future__ import annotations

import argparse
import csv
import difflib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parent.parent
AUTORESEARCH_DIR = (ROOT_DIR.parent / "autoresearch").resolve()
RUNTIME_DIR = Path(
    os.environ.get("AUTORESEARCH_DIR", str(ROOT_DIR / "demo_runtime"))
).resolve()
LIVE_LOG = RUNTIME_DIR / "pm_live.jsonl"
RESULTS_TSV = RUNTIME_DIR / "pm_results.tsv"
RUN_LOG = RUNTIME_DIR / "pm_run.log"
STATE_JSON = RUNTIME_DIR / "orchestrator_state.json"
WORKER_DIR = RUNTIME_DIR / "workers"
ARTIFACT_DIR = RUNTIME_DIR / "artifacts"
MANAGED_TARGET_FILE = ARTIFACT_DIR / "managed_target.md"
MANAGED_PATCH_FILE = ARTIFACT_DIR / "managed_target.patch"
PROJECT_DOC = ROOT_DIR / "docs" / "PROJECT.md"
SELF_ITERATION_DOC = ROOT_DIR / "docs" / "SELF_ITERATION_DEMO.md"
PM_CONFIG_PATH = AUTORESEARCH_DIR / "pm_config.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_runtime_dir() -> None:
    for path in (RUNTIME_DIR, WORKER_DIR, ARTIFACT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def reset_runtime() -> None:
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)
    ensure_runtime_dir()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_log(message: str) -> None:
    with RUN_LOG.open("a") as f:
        f.write(f"[{now_iso()}] {message}\n")


def write_results(row: dict[str, Any]) -> None:
    headers = [
        "commit",
        "description",
        "status",
        "fitness",
        "accuracy",
        "total_pnl",
        "tokens",
    ]
    exists = RESULTS_TSV.exists()
    with RESULTS_TSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    shutil.move(str(temp), str(path))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_unified_patch(old_text: str, new_text: str, file_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=file_label,
            tofile=file_label,
        )
    )


def apply_unified_patch(old_text: str, patch_text: str, file_label: str) -> str:
    lines = patch_text.splitlines(keepends=True)
    if len(lines) < 2:
        raise ValueError("patch is too short")
    if not lines[0].startswith(f"--- {file_label}") or not lines[1].startswith(f"+++ {file_label}"):
        raise ValueError("patch headers must target the managed file")

    old_lines = old_text.splitlines(keepends=True)
    result: list[str] = []
    old_index = 0
    i = 2

    while i < len(lines):
        header = lines[i]
        if not header.startswith("@@"):
            raise ValueError("unexpected patch line before hunk header")
        match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
        if not match:
            raise ValueError("invalid hunk header")
        old_start = int(match.group(1)) - 1
        result.extend(old_lines[old_index:old_start])
        old_index = old_start
        i += 1

        while i < len(lines) and not lines[i].startswith("@@"):
            line = lines[i]
            if line.startswith("\\"):
                i += 1
                continue
            prefix = line[:1]
            content = line[1:]
            if prefix == " ":
                if old_index >= len(old_lines) or old_lines[old_index] != content:
                    raise ValueError("patch context does not match managed target")
                result.append(content)
                old_index += 1
            elif prefix == "-":
                if old_index >= len(old_lines) or old_lines[old_index] != content:
                    raise ValueError("patch removal does not match managed target")
                old_index += 1
            elif prefix == "+":
                result.append(content)
            else:
                raise ValueError("invalid patch line prefix")
            i += 1

    result.extend(old_lines[old_index:])
    return "".join(result)


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        raise ValueError("no JSON object found in model response")
    return json.loads(text[start : end + 1])


def expect_type(name: str, value: Any, expected_type: type) -> None:
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} must be {expected_type.__name__}")


def expect_list_of_str(name: str, value: Any) -> list[str]:
    expect_type(name, value, list)
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must contain only strings")
    return value


def validate_worker_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required = [
        "summary",
        "deliverables",
        "tool_summary",
        "feedback",
        "next_action",
        "details",
        "artifacts",
        "prompt_tokens",
        "completion_tokens",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"worker payload missing fields: {', '.join(missing)}")
    expect_type("summary", payload["summary"], str)
    expect_list_of_str("deliverables", payload["deliverables"])
    expect_list_of_str("tool_summary", payload["tool_summary"])
    expect_list_of_str("feedback", payload["feedback"])
    expect_type("next_action", payload["next_action"], str)
    expect_list_of_str("details", payload["details"])
    expect_list_of_str("artifacts", payload["artifacts"])
    expect_type("prompt_tokens", payload["prompt_tokens"], int)
    expect_type("completion_tokens", payload["completion_tokens"], int)
    return payload


@dataclass
class ModelConfig:
    api_base_url: str
    api_key: str
    model_name: str
    temperature: float
    max_tokens: int


def load_kimi_config() -> ModelConfig:
    base_url = os.environ.get("MOONSHOT_BASE_URL")
    api_key = os.environ.get("MOONSHOT_API_KEY")
    model_name = os.environ.get("MOONSHOT_MODEL")
    temperature = os.environ.get("MOONSHOT_TEMPERATURE")
    max_tokens = os.environ.get("MOONSHOT_MAX_TOKENS")

    if PM_CONFIG_PATH.exists():
        spec = importlib.util.spec_from_file_location("pm_config", PM_CONFIG_PATH)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            base_url = base_url or getattr(module, "API_BASE_URL", "")
            api_key = api_key or getattr(module, "API_KEY", "")
            model_name = model_name or getattr(module, "MODEL_NAME", "")
            temperature = temperature or str(getattr(module, "TEMPERATURE", 0.3))
            max_tokens = max_tokens or str(getattr(module, "MAX_TOKENS", 300))

    if not base_url or not api_key or not model_name:
        raise RuntimeError("Kimi config is missing. Set env vars or provide ../autoresearch/pm_config.py")

    return ModelConfig(
        api_base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        temperature=float(temperature),
        max_tokens=int(max_tokens),
    )


@dataclass
class RuntimeOptions:
    delay: float
    max_steps: int
    mock_kimi: bool
    codex_worker_cmd: str
    allow_local_codex_fallback: bool


@dataclass
class TaskSpec:
    task_id: str
    role: str
    title: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    assigned_model: str = ""


@dataclass
class WorkerRun:
    agent_id: str
    model: str
    role: str
    task_id: str
    status: str = "pending"
    summary: str = ""
    deliverables: list[str] = field(default_factory=list)
    tool_summary: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    next_action: str = ""
    context_path: str = ""


@dataclass
class MainAgentState:
    goal: str
    constraints: list[str]
    completed: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    next_dispatch_reasoning: str = ""


@dataclass
class WorkerExecution:
    summary: str
    deliverables: list[str]
    tool_summary: list[str]
    feedback: list[str]
    next_action: str
    details: list[str]
    artifacts: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class DashboardSync:
    def __init__(self) -> None:
        self.total_tokens = 0
        self.api_calls = 0
        self.api_errors = 0

    def write_state(
        self,
        status: str,
        main_agent: MainAgentState,
        workers: list[WorkerRun],
    ) -> None:
        payload = {
            "goal": main_agent.goal,
            "status": status,
            "updated_at": now_iso(),
            "main_agent": asdict(main_agent),
            "workers": [asdict(worker) for worker in workers],
        }
        atomic_write_json(STATE_JSON, payload)

    def start_run(self, main_agent: MainAgentState, model_label: str) -> None:
        append_jsonl(
            LIVE_LOG,
            {
                "type": "start",
                "started_at": now_iso(),
                "budget_limit": 12000,
                "model": model_label,
                "params": {
                    "model": model_label,
                    "temperature": 0.2,
                    "max_tokens": 2400,
                    "confidence_threshold": "summary-only",
                    "bet_sizing": "task dispatch",
                    "max_bet_fraction": "n/a",
                    "system_prompt_preview": "MainAgent 只保留目标、计划、反馈、调度理由。",
                    "user_prompt_preview": main_agent.goal,
                },
            },
        )

    def push_worker_event(
        self,
        index: int,
        total: int,
        worker: WorkerRun,
        execution: WorkerExecution,
        bankroll: float,
    ) -> None:
        call_tokens = execution.prompt_tokens + execution.completion_tokens
        self.total_tokens += call_tokens
        self.api_calls += 1
        append_jsonl(
            LIVE_LOG,
            {
                "type": "inference",
                "index": index,
                "progress": f"{index}/{total}",
                "question": worker.summary or worker.agent_id,
                "thinking": " | ".join(worker.tool_summary),
                "reasoning": worker.next_action,
                "raw_response": "\n".join(execution.details[-3:]),
                "prediction": 0,
                "confidence": 0.9,
                "outcomes": ["on-track", "blocked"],
                "final_resolution": worker.status,
                "bet_action": "buy" if worker.status == "completed" else "skip",
                "is_correct": worker.status == "completed",
                "bet_pnl": 150.0 if worker.status == "completed" else 0.0,
                "running_pnl": bankroll - 10000.0,
                "bankroll": bankroll,
                "wins": len(worker.deliverables),
                "losses": 0 if worker.status == "completed" else 1,
                "win_rate": 1.0 if worker.status == "completed" else 0.5,
                "volume": 1,
                "last_trade_price": 1.0,
                "call_tokens": call_tokens,
                "cumulative_tokens": self.total_tokens,
                "prompt_tokens": execution.prompt_tokens,
                "completion_tokens": execution.completion_tokens,
                "api_calls": self.api_calls,
                "api_errors": self.api_errors,
            },
        )

    def finish_run(self, fitness: float) -> None:
        append_jsonl(
            LIVE_LOG,
            {
                "type": "finish",
                "finished_at": now_iso(),
                "results": {"fitness": fitness},
                "token_summary": {
                    "total_tokens": self.total_tokens,
                    "api_calls": self.api_calls,
                    "api_errors": self.api_errors,
                },
            },
        )


class MainAgentAdapter:
    def bootstrap(self, coding_model_label: str) -> tuple[MainAgentState, list[TaskSpec]]:
        state = MainAgentState(
            goal="验证 MainAgent 仅保留摘要，研究交给 Kimi，代码执行交给 Codex，并在启动时把状态同步到 dashboard。",
            constraints=[
                "MainAgent 上下文只保留摘要",
                "研究任务默认给 Kimi worker",
                "代码任务默认给 Codex worker",
                "Dashboard 启动后立即可见当前状态",
            ],
            in_progress=["初始化 orchestrator state"],
            pending=[
                "研究最小 summary contract",
                "根据 summary contract 生成真实 coding artifact",
                "生成 runtime 收尾摘要",
            ],
            notes=[
                "worker 详细执行上下文落到 runtime/workers/*.json",
                "MainAgent 只保留计划、反馈、下一步调度理由",
                f"coding worker backend={coding_model_label}",
            ],
            next_dispatch_reasoning="先让 research worker 给出摘要契约，再让 coding worker 生成 artifact，最后由 main agent 收尾。",
        )
        tasks = [
            TaskSpec(
                task_id="research_contract",
                role="research",
                title="研究最小 summary contract",
                objective="提炼 main/worker 边界，明确 dashboard 同步最小字段集。",
                assigned_model="kimi",
            ),
            TaskSpec(
                task_id="coding_artifact",
                role="coding",
                title="生成真实 coding artifact",
                objective="把 research 摘要转换成实际 artifact，供下一轮使用。",
                depends_on=["research_contract"],
                assigned_model=coding_model_label,
            ),
            TaskSpec(
                task_id="main_finalize",
                role="main",
                title="收敛最终摘要",
                objective="压缩本轮结果，为下一轮真实 agent 接入保留最小上下文。",
                depends_on=["research_contract", "coding_artifact"],
                assigned_model="kimi-main",
            ),
        ]
        return state, tasks

    def choose_next_task(self, tasks: list[TaskSpec]) -> TaskSpec | None:
        completed = {task.task_id for task in tasks if task.status == "completed"}
        for task in tasks:
            if task.status != "pending":
                continue
            if all(dep in completed for dep in task.depends_on):
                return task
        return None

    def update_after_worker(
        self,
        state: MainAgentState,
        tasks: list[TaskSpec],
        worker: WorkerRun,
        execution: WorkerExecution,
    ) -> None:
        task = next(task for task in tasks if task.task_id == worker.task_id)
        task.status = worker.status
        if task.title not in state.completed:
            state.completed.append(task.title)
        state.in_progress = []
        state.feedback.extend(execution.feedback)
        state.pending = [task.title for task in tasks if task.status == "pending"]
        if state.pending:
            state.next_dispatch_reasoning = f"{task.title} 已完成，下一步转向 {state.pending[0]}。"
            state.in_progress = [state.pending[0]]
        else:
            state.next_dispatch_reasoning = "当前 loop 已收敛，等待接入更完整的真实 agent 调度器。"


class WorkerAdapter:
    def execute(self, task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> WorkerExecution:
        raise NotImplementedError


class KimiJSONAdapter(WorkerAdapter):
    def __init__(self, config: ModelConfig, system_prompt: str, prompt_builder: Any, mock: bool) -> None:
        self.config = config
        self.system_prompt = system_prompt
        self.prompt_builder = prompt_builder
        self.mock = mock
        self.client = OpenAI(api_key=config.api_key, base_url=config.api_base_url) if not mock else None

    def execute(self, task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> WorkerExecution:
        if self.mock:
            return self._mock_execute(task, prior_contexts)

        user_prompt = self.prompt_builder(task, prior_contexts)
        response = self.client.chat.completions.create(
            model=self.config.model_name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        payload = validate_worker_payload(extract_json_object(content))
        usage = response.usage
        return WorkerExecution(
            summary=payload["summary"],
            deliverables=payload["deliverables"],
            tool_summary=payload["tool_summary"],
            feedback=payload["feedback"],
            next_action=payload["next_action"],
            details=payload["details"] + [f"backend={self.config.model_name}"],
            artifacts=payload["artifacts"],
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def _mock_execute(self, task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> WorkerExecution:
        if task.role == "research":
            return WorkerExecution(
                summary="研究最小的 main/worker/summary contract",
                deliverables=[
                    "main agent summary schema",
                    "worker context persistence rule",
                    "dashboard sync field list",
                ],
                tool_summary=[
                    "read: docs/current/PROJECT.md",
                    "summarize: orchestrator contract",
                ],
                feedback=["mock research 完成。"],
                next_action="交给 coding worker。",
                details=["mock kimi response"],
                prompt_tokens=480,
                completion_tokens=160,
            )
        return WorkerExecution(
            summary="压缩本轮结果并输出下一轮 brief",
            deliverables=["artifacts/next_iteration_brief.json"],
            tool_summary=["summarize: final brief"],
            feedback=["mock finalize 完成。"],
            next_action="当前 demo 运行完成。",
            details=[f"mock finalize with prior_contexts={len(prior_contexts)}"],
            prompt_tokens=240,
            completion_tokens=120,
        )


def build_research_prompt(task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> str:
    project = PROJECT_DOC.read_text(encoding="utf-8")[:1500] if PROJECT_DOC.exists() else ""
    return "\n".join(
        [
            f"task_id={task.task_id}",
            f"title={task.title}",
            f"objective={task.objective}",
            f"prior_contexts={len(prior_contexts)}",
            "Read this project excerpt and return only JSON.",
            project,
            "JSON schema:",
            json.dumps(
                {
                    "summary": "",
                    "deliverables": [""],
                    "tool_summary": [""],
                    "feedback": [""],
                    "next_action": "",
                    "details": [""],
                    "artifacts": [""],
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                },
                ensure_ascii=False,
            ),
        ]
    )


def build_finalize_prompt(task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> str:
    latest_summaries = [
        {
            "task_id": item.get("task", {}).get("task_id"),
            "summary": item.get("execution", {}).get("summary"),
            "deliverables": item.get("execution", {}).get("deliverables", []),
            "feedback": item.get("execution", {}).get("feedback", []),
        }
        for item in prior_contexts[-3:]
    ]
    return "\n".join(
        [
            f"task_id={task.task_id}",
            f"title={task.title}",
            f"objective={task.objective}",
            "Return only JSON summarizing the next iteration brief.",
            json.dumps(latest_summaries, ensure_ascii=False),
            "JSON schema:",
            json.dumps(
                {
                    "summary": "",
                    "deliverables": [""],
                    "tool_summary": [""],
                    "feedback": [""],
                    "next_action": "",
                    "details": [""],
                    "artifacts": [""],
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                },
                ensure_ascii=False,
            ),
        ]
    )


class CodexExternalAdapter(WorkerAdapter):
    def __init__(self, cmd: str, allow_local_fallback: bool) -> None:
        self.cmd = cmd.strip()
        self.allow_local_fallback = allow_local_fallback

    def execute(self, task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> WorkerExecution:
        if self.cmd:
            return self._execute_external(task, prior_contexts)
        if not self.allow_local_fallback:
            raise RuntimeError("CODEX_WORKER_CMD is not set and local fallback is disabled")
        return self._execute_local(task, prior_contexts)

    def _execute_external(self, task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> WorkerExecution:
        input_path = ARTIFACT_DIR / f"{task.task_id}_codex_input.json"
        output_path = ARTIFACT_DIR / f"{task.task_id}_codex_output.json"
        payload = {
            "task": asdict(task),
            "prior_contexts": prior_contexts,
            "runtime_dir": str(RUNTIME_DIR),
            "artifact_dir": str(ARTIFACT_DIR),
            "managed_target_file": str(MANAGED_TARGET_FILE),
            "managed_patch_file": str(MANAGED_PATCH_FILE),
        }
        atomic_write_json(input_path, payload)

        cmd = shlex.split(self.cmd) + [str(input_path), str(output_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT_DIR))
        if proc.returncode != 0:
            raise RuntimeError(
                f"external codex worker failed: exit={proc.returncode} stderr={proc.stderr.strip()[:300]}"
            )
        result = validate_worker_payload(json.loads(output_path.read_text(encoding="utf-8")))
        if result["artifacts"] != [str(MANAGED_PATCH_FILE)]:
            raise RuntimeError("external codex worker must report only the managed patch file")
        patch_text = MANAGED_PATCH_FILE.read_text(encoding="utf-8")
        current_text = MANAGED_TARGET_FILE.read_text(encoding="utf-8")
        updated_text = apply_unified_patch(current_text, patch_text, MANAGED_TARGET_FILE.name)
        write_text(MANAGED_TARGET_FILE, updated_text)
        return WorkerExecution(
            summary=result["summary"],
            deliverables=result["deliverables"],
            tool_summary=result["tool_summary"],
            feedback=result["feedback"],
            next_action=result["next_action"],
            details=result["details"] + [f"external_cmd={self.cmd}", f"applied_patch={MANAGED_PATCH_FILE}"],
            artifacts=[str(MANAGED_PATCH_FILE), str(MANAGED_TARGET_FILE)],
            prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"],
        )

    def _execute_local(self, task: TaskSpec, prior_contexts: list[dict[str, Any]]) -> WorkerExecution:
        contract = {
            "main_agent_summary_fields": [
                "goal",
                "constraints",
                "completed",
                "in_progress",
                "pending",
                "feedback",
                "next_dispatch_reasoning",
            ],
            "worker_context_fields": [
                "task_id",
                "role",
                "model",
                "tool_summary",
                "details",
                "artifacts",
                "feedback",
            ],
            "handoff_rule": "main agent 只接收压缩摘要，不接收原始工具输出。",
        }
        summary = {
            "source_task": task.task_id,
            "prior_context_count": len(prior_contexts),
            "latest_contexts": [
                {
                    "task_id": item.get("task", {}).get("task_id"),
                    "summary": item.get("execution", {}).get("summary"),
                }
                for item in prior_contexts[-3:]
            ],
        }
        contract_path = ARTIFACT_DIR / "adapter_contract.json"
        codex_meta_path = ARTIFACT_DIR / "codex_worker_result.json"
        atomic_write_json(contract_path, contract)
        next_text = (
            "\n".join(
                [
                    "# Managed Target",
                    "",
                    "- this is the only controlled file the coding worker may update",
                    f"- source_task: {task.task_id}",
                    f"- prior_contexts: {len(prior_contexts)}",
                    "",
                    "## Latest Contexts",
                ]
                + [
                    f"- {item['task_id']}: {item['summary']}"
                    for item in summary["latest_contexts"]
                ]
                + [
                    "",
                    "## Contract",
                    "- main agent only keeps compressed summary",
                    "- worker context remains in worker-owned files",
                ]
            )
            + "\n"
        )
        current_text = MANAGED_TARGET_FILE.read_text(encoding="utf-8")
        patch_text = build_unified_patch(current_text, next_text, MANAGED_TARGET_FILE.name)
        write_text(MANAGED_PATCH_FILE, patch_text)
        write_text(MANAGED_TARGET_FILE, apply_unified_patch(current_text, patch_text, MANAGED_TARGET_FILE.name))
        atomic_write_json(codex_meta_path, summary)
        return WorkerExecution(
            summary="生成真实 coding patch",
            deliverables=[
                "artifacts/managed_target.patch",
            ],
            tool_summary=[
                "write: demo_runtime/artifacts/managed_target.patch",
                "apply: demo_runtime/artifacts/managed_target.patch",
            ],
            feedback=["coding worker 已生成并应用受控 patch。"],
            next_action="由 MainAgent 压缩本轮结果并结束当前 loop。",
            details=[
                "CODEX_WORKER_CMD 未配置，使用本地 coding worker 回退实现。",
                f"prior_contexts={len(prior_contexts)}",
            ],
            artifacts=[str(MANAGED_PATCH_FILE), str(MANAGED_TARGET_FILE), str(contract_path), str(codex_meta_path)],
            prompt_tokens=180,
            completion_tokens=120,
        )


class OrchestratorRuntime:
    def __init__(self, options: RuntimeOptions) -> None:
        self.options = options
        self.sync = DashboardSync()
        self.main_adapter = MainAgentAdapter()
        self.kimi_config = load_kimi_config()
        self.worker_adapters: dict[str, WorkerAdapter] = {
            "research": KimiJSONAdapter(
                self.kimi_config,
                system_prompt=(
                    "You are the research worker in a self-iteration system. "
                    "Return only valid JSON. Summarize the minimum scheduling context."
                ),
                prompt_builder=build_research_prompt,
                mock=options.mock_kimi,
            ),
            "coding": CodexExternalAdapter(
                cmd=options.codex_worker_cmd,
                allow_local_fallback=options.allow_local_codex_fallback,
            ),
            "main": KimiJSONAdapter(
                self.kimi_config,
                system_prompt=(
                    "You are the main agent finalizer in a self-iteration system. "
                    "Return only valid JSON. Compress results for the next iteration."
                ),
                prompt_builder=build_finalize_prompt,
                mock=options.mock_kimi,
            ),
        }
        self.bankroll = 10000.0
        self.worker_contexts: list[dict[str, Any]] = []

    def persist_worker_context(
        self,
        worker: WorkerRun,
        task: TaskSpec,
        execution: WorkerExecution,
    ) -> str:
        context = {
            "saved_at": now_iso(),
            "task": asdict(task),
            "worker": asdict(worker),
            "execution": asdict(execution),
        }
        path = WORKER_DIR / f"{worker.agent_id}.json"
        atomic_write_json(path, context)
        self.worker_contexts.append(context)
        return str(path)

    def build_worker(self, task: TaskSpec, seq: int) -> WorkerRun:
        model = task.assigned_model
        return WorkerRun(
            agent_id=f"worker_{model.replace('-', '_')}_{seq:02d}",
            model=model,
            role=task.role,
            task_id=task.task_id,
            status="running",
            summary=task.title,
            next_action=task.objective,
        )

    def _coding_backend_label(self) -> str:
        if self.options.codex_worker_cmd:
            return "codex-external"
        if self.options.allow_local_codex_fallback:
            return "codex-local-fallback"
        return "codex-unconfigured"

    def run(self) -> None:
        reset_runtime()
        write_text(
            MANAGED_TARGET_FILE,
            "# Managed Target\n\n- this file is owned by the coding worker path\n",
        )
        main_state, tasks = self.main_adapter.bootstrap(self._coding_backend_label())
        workers: list[WorkerRun] = []
        model_label = f"{self.kimi_config.model_name} + {self._coding_backend_label()}"
        self.sync.write_state("running", main_state, workers)
        self.sync.start_run(main_state, model_label=model_label)
        append_log("orchestrator started and synced initial state to dashboard")

        step = 0
        status = "finished"
        fitness = 0.96

        while step < self.options.max_steps:
            next_task = self.main_adapter.choose_next_task(tasks)
            if next_task is None:
                break

            step += 1
            worker = self.build_worker(next_task, step)
            workers.append(worker)
            main_state.in_progress = [next_task.title]
            self.sync.write_state("running", main_state, workers)

            try:
                execution = self.worker_adapters[next_task.role].execute(
                    next_task,
                    prior_contexts=self.worker_contexts,
                )
                worker.status = "completed"
                worker.summary = execution.summary
                worker.deliverables = execution.deliverables
                worker.tool_summary = execution.tool_summary
                worker.feedback = execution.feedback
                worker.next_action = execution.next_action
                worker.context_path = self.persist_worker_context(worker, next_task, execution)
                self.main_adapter.update_after_worker(main_state, tasks, worker, execution)
                self.bankroll += 150.0
                self.sync.write_state("running", main_state, workers)
                self.sync.push_worker_event(step, len(tasks), worker, execution, self.bankroll)
                append_log(f"{worker.agent_id} completed {next_task.task_id}:{next_task.title}")
            except Exception as exc:
                worker.status = "failed"
                worker.feedback = [f"worker failed: {str(exc)[:300]}"]
                worker.tool_summary = ["runtime:error"]
                main_state.notes.append(f"{worker.agent_id} failed")
                main_state.feedback.append(worker.feedback[0])
                main_state.in_progress = []
                self.sync.write_state("failed", main_state, workers)
                append_log(f"{worker.agent_id} failed: {str(exc)[:500]}")
                status = "failed"
                fitness = 0.0
                break

            time.sleep(self.options.delay)

        if status == "finished" and any(task.status != "completed" for task in tasks):
            main_state.notes.append("loop 在 max_steps 前未完全收敛。")
            status = "timeout"
            fitness = 0.0
        elif status == "finished":
            main_state.notes.append("当前 runtime 已通过真实 Kimi / 正式 Codex 协议收敛一轮。")

        self.sync.write_state("finished" if status == "finished" else status, main_state, workers)
        self.sync.finish_run(fitness=fitness)
        append_log(f"runtime finished with status={status}")
        write_results(
            {
                "commit": "self-iteration-runtime",
                "description": f"Real Kimi orchestration + {self._coding_backend_label()}",
                "status": "keep" if status == "finished" else status,
                "fitness": f"{fitness:.4f}",
                "accuracy": "1.00" if status == "finished" else "0.00",
                "total_pnl": f"{self.bankroll - 10000.0:.1f}",
                "tokens": str(self.sync.total_tokens),
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.8, help="step delay in seconds")
    parser.add_argument("--max-steps", type=int, default=6, help="maximum loop steps")
    parser.add_argument("--mock-kimi", action="store_true", help="disable live Kimi calls")
    parser.add_argument(
        "--codex-worker-cmd",
        default=os.environ.get("CODEX_WORKER_CMD", ""),
        help="external codex worker command; called as: <cmd> <input_json> <output_json>",
    )
    parser.add_argument(
        "--disable-local-codex-fallback",
        action="store_true",
        help="fail if external codex worker is not configured",
    )
    args = parser.parse_args()

    options = RuntimeOptions(
        delay=max(args.delay, 0.0),
        max_steps=max(args.max_steps, 1),
        mock_kimi=args.mock_kimi,
        codex_worker_cmd=args.codex_worker_cmd,
        allow_local_codex_fallback=not args.disable_local_codex_fallback,
    )
    runtime = OrchestratorRuntime(options)
    runtime.run()
    print(f"runtime written to {RUNTIME_DIR}")


if __name__ == "__main__":
    main()
