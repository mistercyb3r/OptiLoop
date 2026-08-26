"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import PixelOffice from "@/components/PixelOffice";

const API = typeof window !== "undefined"
  ? (process.env.NEXT_PUBLIC_API_URL || `http://${window.location.hostname}:8050/api`)
  : "http://localhost:8050/api";

interface TaskSummary {
  id: string; prompt: string; status: string;
  target_budget_usd: number | null; total_spent_usd: number;
  model_used: string; created_at: string; updated_at: string;
}

interface LogEvent { step_type: string; content: string; timestamp: string; }

interface TaskDetail extends TaskSummary {
  agent_runs: { id: string; agent_role: string; model_name: string; iteration: number; status: string; timestamp: string }[];
  cost_metrics: { model_name: string; prompt_tokens: number; completion_tokens: number; cost_usd: number }[];
  token_breakdown: { model_name: string; prompt_tokens: number; completion_tokens: number; prompt_cost_usd: number; completion_cost_usd: number; total_cost_usd: number }[];
  total_prompt_tokens: number; total_completion_tokens: number;
  total_input_cost: number; total_output_cost: number;
  execution_logs: LogEvent[];
}

const STATUS_COLORS: Record<string, string> = {
  pending: "text-gray-400", running: "text-blue-400 animate-pulse",
  completed: "text-green-400", failed: "text-red-400", cancelled: "text-yellow-400",
};

function detectActiveRole(logs: LogEvent[]): string | null {
  for (let i = logs.length - 1; i >= 0; i--) {
    const c = logs[i].content || "";
    if (c.includes("[Planner")) return "planner";
    if (c.includes("[Executor")) return "executor";
    if (c.includes("[Reviewer")) return "reviewer";
  }
  return logs.length > 0 ? (logs[logs.length - 1].step_type as any) : null;
}

export default function Home() {
  const [prompt, setPrompt] = useState("");
  const [budget, setBudget] = useState(0.5);
  const [model, setModel] = useState("auto");
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  const fetchTasks = useCallback(async () => {
    try { const r = await fetch(`${API}/tasks`); if (r.ok) setTasks(await r.json()); } catch {}
  }, []);

  const fetchDetail = useCallback(async (id: string) => {
    try { const r = await fetch(`${API}/tasks/${id}`); if (r.ok) {
      const d: TaskDetail = await r.json(); setDetail(d); setLogs(d.execution_logs || []);
    }} catch {}
  }, []);

  useEffect(() => { fetchTasks(); const t = setInterval(fetchTasks, 3000); return () => clearInterval(t); }, [fetchTasks]);
  useEffect(() => {
    if (!selectedTask) return; fetchDetail(selectedTask);
    const t = setInterval(() => fetchDetail(selectedTask!), 2000); return () => clearInterval(t);
  }, [selectedTask, fetchDetail]);
  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);
  useEffect(() => {
    if (!selectedTask) return;
    const es = new EventSource(`${API}/tasks/${selectedTask}/stream`);
    es.addEventListener("log", (e) => { try { setLogs((p) => [...p, JSON.parse(e.data)]); } catch {} });
    es.onerror = () => es.close();
    return () => es.close();
  }, [selectedTask]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); if (!prompt.trim()) return; setSubmitting(true);
    try {
      const r = await fetch(`${API}/tasks`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, target_budget_usd: budget, model }),
      });
      if (r.ok) { const t: TaskSummary = await r.json(); setPrompt(""); setSelectedTask(t.id); fetchTasks(); }
    } catch {} setSubmitting(false);
  };

  const handleStop = async (id: string) => {
    await fetch(`${API}/tasks/${id}/stop`, { method: "POST" }); fetchTasks();
    if (selectedTask === id) fetchDetail(id);
  };

  const activeRole = detectActiveRole(logs);
  const budgetPct = detail && detail.target_budget_usd && detail.target_budget_usd > 0
    ? Math.min(100, (detail.total_spent_usd / detail.target_budget_usd) * 100) : 0;
  const budgetBarColor = budgetPct >= 80 ? "bg-red-500" : budgetPct >= 50 ? "bg-yellow-500" : "bg-green-500";

  return (
    <div className="flex flex-1 flex-col bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-3 flex items-center gap-4">
        <span className="text-lg font-bold text-white tracking-tight">OptiLoop</span>
        <span className="text-xs text-slate-500">Autonomous Multi-Agent Coding</span>
      </header>
      <div className="flex flex-1 overflow-hidden">
        <aside className="w-80 border-r border-slate-800 flex flex-col overflow-hidden">
          <form onSubmit={handleSubmit} className="p-4 border-b border-slate-800 flex flex-col gap-3">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">New Task</h2>
            <textarea className="bg-slate-800 border border-slate-700 rounded p-3 text-sm resize-none h-24 focus:outline-none focus:border-blue-500 placeholder-slate-600"
              placeholder="Describe your coding task..." value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            <div className="flex gap-2 items-center">
              <select className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-blue-500"
                value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="auto">Auto-Route</option>
                <option value="openai/gpt-4o-mini">GPT-4o Mini</option>
                <option value="deepseek/deepseek-chat">DeepSeek Chat</option>
                <option value="deepseek/deepseek-v4-flash">DeepSeek V4 Flash</option>
                <option value="qwen/qwen-2.5-coder-32b-instruct">Qwen 2.5 Coder</option>
                <option value="anthropic/claude-3.5-sonnet">Claude 3.5 Sonnet</option>
                <option value="openai/gpt-4o">GPT-4o</option>
              </select>
              <div className="relative">
                <span className="absolute left-2 top-1.5 text-slate-500 text-sm">$</span>
                <input type="number" step="0.10" min="0"
                  className="bg-slate-800 border border-slate-700 rounded pl-5 pr-2 py-1.5 text-sm w-20 focus:outline-none focus:border-blue-500"
                  value={budget} onChange={(e) => setBudget(parseFloat(e.target.value) || 0)} />
              </div>
              <button type="submit" disabled={submitting || !prompt.trim()}
                className="bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 text-white text-sm font-medium px-4 py-1.5 rounded transition whitespace-nowrap">
                {submitting ? "Starting..." : "Run"}
              </button>
            </div>
          </form>
          <div className="flex-1 overflow-y-auto p-3">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Tasks</h2>
            <div className="flex flex-col gap-1.5">
              {tasks.map((t) => (
                <div key={t.id} onClick={() => setSelectedTask(t.id)}
                  className={`p-2.5 rounded border cursor-pointer transition ${selectedTask === t.id ? "border-blue-500 bg-slate-800/80" : "border-slate-800 hover:border-slate-700"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm truncate flex-1">{t.prompt}</span>
                    <span className={`text-xs font-medium whitespace-nowrap ${STATUS_COLORS[t.status] || "text-gray-400"}`}>{t.status}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xs text-slate-500">${t.total_spent_usd.toFixed(4)}</span>
                    {t.model_used && <span className="text-[9px] font-mono px-1 bg-slate-800 rounded text-blue-400">{t.model_used.split("/").pop()}</span>}
                    {(t.status === "running" || t.status === "pending") && (
                      <button onClick={(e) => { e.stopPropagation(); handleStop(t.id); }}
                        className="ml-auto text-[9px] text-red-400 hover:text-red-300">Stop</button>
                    )}
                  </div>
                </div>
              ))}
              {tasks.length === 0 && <p className="text-sm text-slate-600">No tasks yet.</p>}
            </div>
          </div>
        </aside>

        <main className="flex-1 flex flex-col overflow-y-auto p-6 gap-4">
          {detail && (
            <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Cost Metrics</h2>
                <div className="flex items-center gap-2">
                  {detail.model_used && <span className="text-[10px] font-mono px-1.5 py-0.5 bg-blue-900/50 border border-blue-700/50 rounded text-blue-300">{detail.model_used}</span>}
                  <span className="text-xs text-slate-500">In: ${detail.total_input_cost?.toFixed(6) || "0"} | Out: ${detail.total_output_cost?.toFixed(6) || "0"}</span>
                  <span className="text-xs text-slate-500">{detail.total_prompt_tokens.toLocaleString()} in / {detail.total_completion_tokens.toLocaleString()} out</span>
                </div>
              </div>
              <div className="w-full bg-slate-800 rounded-full h-2 mb-2">
                <div className={`${budgetBarColor} h-2 rounded-full transition-all`} style={{ width: `${budgetPct}%` }} />
              </div>
              <div className="flex items-center justify-between text-[10px] text-slate-500">
                <span>${detail.total_spent_usd.toFixed(6)} spent</span>
                <span>{detail.target_budget_usd ? `$${detail.target_budget_usd} (${budgetPct.toFixed(1)}%)` : "No limit"}</span>
              </div>
            </div>
          )}
          <PixelOffice logs={logs} status={detail?.status || "pending"}
            totalSpent={detail?.total_spent_usd || 0} targetBudget={detail?.target_budget_usd || null}
            activeRole={activeRole} />
          <div className="bg-slate-900 rounded-lg border border-slate-800 flex-1 flex flex-col min-h-[300px]">
            <div className="flex items-center justify-between p-3 border-b border-slate-800">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Live Output</h2>
              {detail && <span className={`text-xs font-medium ${STATUS_COLORS[detail.status] || "text-gray-400"}`}>{detail.status}</span>}
            </div>
            <div className="flex-1 overflow-y-auto p-3 font-mono text-xs space-y-1 max-h-[500px]">
              {logs.map((log, i) => (
                <div key={i} className="flex gap-2">
                  <span className="text-slate-600 shrink-0">[{log.step_type}]</span>
                  <span className="text-slate-300 whitespace-pre-wrap break-words">{log.content}</span>
                </div>
              ))}
              {logs.length === 0 && <div className="text-slate-600">{detail ? "Waiting for output..." : "Select a task to view output."}</div>}
              <div ref={logEndRef} />
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
