"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import PixelOffice from "@/components/PixelOffice";

// In Docker / production: use NEXT_PUBLIC_API_URL (set via env var).
// In local dev or when unset: fall back to current hostname on port 8050.
const API = typeof window !== "undefined"
  ? (process.env.NEXT_PUBLIC_API_URL || `http://${window.location.hostname}:8050/api`)
  : "http://localhost:8050/api";

interface TaskSummary {
  id: string;
  prompt: string;
  status: string;
  target_budget_usd: number | null;
  total_spent_usd: number;
  created_at: string;
  updated_at: string;
}

interface LogEvent {
  step_type: string;
  content: string;
  timestamp: string;
}

interface TaskDetail extends TaskSummary {
  agent_runs: { id: string; agent_role: string; model_name: string; iteration: number; status: string; timestamp: string }[];
  cost_metrics: { model_name: string; prompt_tokens: number; completion_tokens: number; cost_usd: number }[];
  total_prompt_tokens: number;
  total_completion_tokens: number;
  execution_logs: LogEvent[];
}

const STATUS_COLORS: Record<string, string> = {
  pending: "text-gray-400",
  running: "text-blue-400 animate-pulse",
  completed: "text-green-400",
  failed: "text-red-400",
  cancelled: "text-yellow-400",
};

export default function Home() {
  const [prompt, setPrompt] = useState("");
  const [budget, setBudget] = useState(0.5);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Fetch task list
  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch(`${API}/tasks`);
      if (res.ok) setTasks(await res.json());
    } catch { /* ignore */ }
  }, []);

  // Fetch task detail
  const fetchDetail = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${API}/tasks/${id}`);
      if (res.ok) {
        const d: TaskDetail = await res.json();
        setDetail(d);
        setLogs(d.execution_logs || []);
      }
    } catch { /* ignore */ }
  }, []);

  // Polling
  useEffect(() => {
    fetchTasks();
    const t = setInterval(fetchTasks, 3000);
    return () => clearInterval(t);
  }, [fetchTasks]);

  useEffect(() => {
    if (!selectedTask) return;
    fetchDetail(selectedTask);
    const t = setInterval(() => fetchDetail(selectedTask!), 2000);
    return () => clearInterval(t);
  }, [selectedTask, fetchDetail]);

  // Auto-scroll logs
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // SSE stream
  useEffect(() => {
    if (!selectedTask) return;
    const es = new EventSource(`${API}/tasks/${selectedTask}/stream`);
    es.addEventListener("log", (e) => {
      try {
        const data = JSON.parse(e.data);
        setLogs((prev) => [...prev, data]);
      } catch { /* ignore */ }
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [selectedTask]);

  // Submit task
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(`${API}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, target_budget_usd: budget }),
      });
      if (res.ok) {
        const t: TaskSummary = await res.json();
        setPrompt("");
        setSelectedTask(t.id);
        fetchTasks();
      }
    } catch { /* ignore */ }
    setSubmitting(false);
  };

  // Stop task
  const handleStop = async (id: string) => {
    await fetch(`${API}/tasks/${id}/stop`, { method: "POST" });
    fetchTasks();
    if (selectedTask === id) fetchDetail(id);
  };

  const budgetPct = detail && detail.target_budget_usd && detail.target_budget_usd > 0
    ? Math.min(100, (detail.total_spent_usd / detail.target_budget_usd) * 100) : 0;
  const budgetBarColor = budgetPct >= 80 ? "bg-red-500" : budgetPct >= 50 ? "bg-yellow-500" : "bg-green-500";

  return (
    <div className="flex flex-1 flex-col p-6 max-w-7xl mx-auto w-full gap-6">
      {/* Header */}
      <header className="flex items-center gap-4">
        <div className="text-2xl font-bold tracking-tight">OptiLoop</div>
        <span className="text-sm text-gray-500">Autonomous Multi-Agent Coding</span>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1">
        {/* Left panel: form + task list */}
        <div className="flex flex-col gap-4">
          {/* Task Form */}
          <form onSubmit={handleSubmit} className="bg-gray-900 rounded-lg p-4 border border-gray-800 flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">New Task</h2>
            <textarea
              className="bg-gray-800 border border-gray-700 rounded p-3 text-sm resize-none h-24 focus:outline-none focus:border-blue-500"
              placeholder="Describe your coding task..."
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <div className="flex items-center gap-3">
              <label className="text-sm text-gray-400">Budget $</label>
              <input
                type="number"
                step="0.10"
                min="0"
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-24 focus:outline-none focus:border-blue-500"
                value={budget}
                onChange={(e) => setBudget(parseFloat(e.target.value) || 0)}
              />
              <button
                type="submit"
                disabled={submitting || !prompt.trim()}
                className="ml-auto bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white text-sm font-medium px-4 py-2 rounded transition"
              >
                {submitting ? "Starting..." : "Run Task"}
              </button>
            </div>
          </form>

          {/* Task List */}
          <div className="bg-gray-900 rounded-lg p-4 border border-gray-800 flex-1 overflow-y-auto max-h-[500px]">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Tasks</h2>
            <div className="flex flex-col gap-2">
              {tasks.map((t) => (
                <div
                  key={t.id}
                  className={`p-3 rounded border cursor-pointer transition ${
                    selectedTask === t.id ? "border-blue-500 bg-gray-800" : "border-gray-800 hover:border-gray-700"
                  }`}
                  onClick={() => setSelectedTask(t.id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm truncate flex-1">{t.prompt}</span>
                    <span className={`text-xs font-medium whitespace-nowrap ${STATUS_COLORS[t.status] || "text-gray-400"}`}>
                      {t.status}
                    </span>
                  </div>
                  <div className="flex items-center justify-between mt-1">
                    <span className="text-xs text-gray-500">${t.total_spent_usd.toFixed(4)}</span>
                    {(t.status === "running" || t.status === "pending") && (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleStop(t.id); }}
                        className="text-xs text-red-400 hover:text-red-300"
                      >
                        Stop
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {tasks.length === 0 && <p className="text-sm text-gray-600">No tasks yet.</p>}
            </div>
          </div>
        </div>

        {/* Right panel: detail + logs */}
        <div className="lg:col-span-2 flex flex-col gap-4">
          {/* Cost Metric Card */}
          {detail && (
            <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Cost Metrics</h2>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-500">
                    {detail.total_prompt_tokens.toLocaleString()} prompt / {detail.total_completion_tokens.toLocaleString()} completion tokens
                  </span>
                  {(detail.status === "running" || detail.status === "pending") && (
                    <button
                      onClick={() => handleStop(detail.id)}
                      className="bg-red-600 hover:bg-red-500 text-white text-xs font-medium px-3 py-1 rounded transition"
                    >
                      Emergency Stop
                    </button>
                  )}
                </div>
              </div>
              {/* Budget bar */}
              <div className="w-full bg-gray-800 rounded-full h-3 mb-2">
                <div className={`${budgetBarColor} h-3 rounded-full transition-all`} style={{ width: `${budgetPct}%` }} />
              </div>
              <div className="flex items-center justify-between text-xs text-gray-500">
                <span>${detail.total_spent_usd.toFixed(6)} spent</span>
                <span>{detail.target_budget_usd ? `$${detail.target_budget_usd} budget (${budgetPct.toFixed(1)}%)` : "No budget limit"}</span>
              </div>
              {/* Per-model breakdown */}
              {detail.cost_metrics.length > 0 && (
                <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {(() => {
                    const byModel: Record<string, { tokens: number; cost: number }> = {};
                    for (const m of detail.cost_metrics) {
                      if (!byModel[m.model_name]) byModel[m.model_name] = { tokens: 0, cost: 0 };
                      byModel[m.model_name].tokens += m.prompt_tokens + m.completion_tokens;
                      byModel[m.model_name].cost += m.cost_usd;
                    }
                    return Object.entries(byModel).map(([model, v]) => (
                      <div key={model} className="bg-gray-800 rounded p-2">
                        <div className="text-xs text-gray-400 truncate">{model.split("/").pop()}</div>
                        <div className="text-sm font-medium">{v.tokens.toLocaleString()} tk</div>
                        <div className="text-xs text-gray-500">${v.cost.toFixed(6)}</div>
                      </div>
                    ));
                  })()}
                </div>
              )}
            </div>
          )}

          {/* Pixel Art Visualizer */}
          <PixelOffice
            logs={logs}
            status={detail?.status || "pending"}
            totalSpent={detail?.total_spent_usd || 0}
            targetBudget={detail?.target_budget_usd || null}
          />

          {/* Active Task Stream / Terminal */}
          <div className="bg-gray-900 rounded-lg border border-gray-800 flex-1 flex flex-col min-h-[300px]">
            <div className="flex items-center justify-between p-3 border-b border-gray-800">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Live Output</h2>
              {detail && (
                <span className={`text-xs font-medium ${STATUS_COLORS[detail.status] || "text-gray-400"}`}>
                  {detail.status}
                </span>
              )}
            </div>
            <div className="flex-1 overflow-y-auto p-3 font-mono text-xs space-y-1 max-h-[500px]">
              {logs.map((log, i) => (
                <div key={i} className="flex gap-2">
                  <span className="text-gray-600 shrink-0">[{log.step_type}]</span>
                  <span className="text-gray-300 whitespace-pre-wrap break-words">{log.content}</span>
                </div>
              ))}
              {logs.length === 0 && (
                <div className="text-gray-600">
                  {detail ? "Waiting for output..." : "Select a task to view output."}
                </div>
              )}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
