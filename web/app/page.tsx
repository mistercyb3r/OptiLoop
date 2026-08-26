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

interface FileDoc { path: string; size: number; code: string; }

const STATUS_COLORS: Record<string, string> = {
  pending: "text-gray-400", running: "text-blue-400 animate-pulse",
  completed: "text-green-400", failed: "text-red-400", cancelled: "text-yellow-400",
};

type Tab = "files" | "planner" | "terminal";

function detectActiveRole(logs: LogEvent[]): string | null {
  for (let i = logs.length - 1; i >= 0; i--) {
    const c = logs[i].content || "";
    if (c.includes("[Planner")) return "planner";
    if (c.includes("[Executor")) return "executor";
    if (c.includes("[Reviewer")) return "reviewer";
  }
  return logs.length > 0 ? (logs[logs.length - 1].step_type as string) : null;
}

function parseFileManifest(content: string): FileDoc | null {
  const idx = content.indexOf("```\n");
  if (idx === -1) return null;
  const head = content.slice(0, idx).trim().split("\n");
  const path = head[0] || "unknown";
  const size = parseInt((head[1] || "0").split(" ")[0], 10) || 0;
  const code = content.slice(idx + 4, content.lastIndexOf("```")).trimEnd();
  return { path, size, code };
}



export default function Home() {
  const [prompt, setPrompt] = useState("");
  const [budget, setBudget] = useState(0.5);
  const [model, setModel] = useState("auto");
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [tab, setTab] = useState<Tab>("terminal");
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
      if (r.ok) { const t: TaskSummary = await r.json(); setPrompt(""); setSelectedTask(t.id); fetchTasks(); setTab("terminal"); }
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

  const files = logs.filter(l => l.step_type === "file").map(l => parseFileManifest(l.content)).filter((f): f is FileDoc => !!f);
  const plannerLogs = logs.filter(l => l.step_type === "reasoning");
  const termLogs = logs.filter(l => l.step_type === "command" || l.step_type === "diff");


  return (
    <div className="flex flex-1 flex-col bg-slate-950 text-slate-100 min-h-screen">
      <header className="sticky top-0 z-30 border-b border-white/5 bg-slate-950/70 backdrop-blur-xl px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 shadow-[0_0_16px_rgba(59,130,246,0.5)] flex items-center justify-center text-xs font-black text-white">O</div>
          <span className="text-lg font-bold text-white tracking-tight">OptiLoop</span>
          <span className="text-xs text-slate-500 hidden sm:inline">Autonomous Multi-Agent Coding Studio</span>
        </div>
        {detail && (
          <div className="flex items-center gap-3">
            <span className="text-[10px] font-mono px-1.5 py-0.5 bg-blue-900/40 border border-blue-700/40 rounded text-blue-300">{detail.model_used || "auto"}</span>
            <span className={`text-xs font-medium ${STATUS_COLORS[detail.status] || "text-gray-400"}`}>{detail.status}</span>
            {(detail.status === "running" || detail.status === "pending") && (
              <button onClick={() => handleStop(detail.id)}
                className="bg-red-600/80 hover:bg-red-500 text-white text-xs font-medium px-3 py-1 rounded transition">
                Emergency Stop
              </button>
            )}
          </div>
        )}
      </header>

      <form onSubmit={handleSubmit} className="sticky top-14 z-20 border-b border-white/5 bg-slate-900/60 backdrop-blur-xl px-6 py-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex-1 flex items-center gap-2 bg-slate-800/60 border border-white/10 rounded-lg px-3 py-1 focus-within:border-blue-500/60 focus-within:shadow-[0_0_0_3px_rgba(59,130,246,0.1)] transition">
          <svg className="w-4 h-4 text-slate-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
          <input value={prompt} onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe your coding task..."
            className="flex-1 bg-transparent outline-none text-sm py-1.5 placeholder-slate-600" />
        </div>
        <select value={model} onChange={(e) => setModel(e.target.value)}
          className="bg-slate-800/60 border border-white/10 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:border-blue-500/60">
          <option value="auto">Auto-Route</option>
          <option value="openai/gpt-4o-mini">GPT-4o Mini</option>
          <option value="deepseek/deepseek-chat">DeepSeek Chat</option>
          <option value="deepseek/deepseek-v4-flash">DeepSeek V4 Flash</option>
          <option value="qwen/qwen-2.5-coder-32b-instruct">Qwen 2.5 Coder</option>
          <option value="anthropic/claude-3.5-sonnet">Claude 3.5 Sonnet</option>
          <option value="openai/gpt-4o">GPT-4o</option>
        </select>
        <div className="relative">
          <span className="absolute left-3 top-2.5 text-slate-500 text-sm">$</span>
          <input type="number" step="0.10" min="0" value={budget}
            onChange={(e) => setBudget(parseFloat(e.target.value) || 0)}
            className="bg-slate-800/60 border border-white/10 rounded-lg pl-7 pr-3 py-2.5 text-sm w-24 focus:outline-none focus:border-blue-500/60" />
        </div>
        <button type="submit" disabled={submitting || !prompt.trim()}
          className="bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 disabled:from-slate-700 disabled:to-slate-700 text-white text-sm font-semibold px-6 py-2.5 rounded-lg transition shadow-[0_0_20px_rgba(59,130,246,0.4)] hover:shadow-[0_0_28px_rgba(59,130,246,0.6)] whitespace-nowrap">
          {submitting ? "Starting..." : "Execute"}
        </button>
      </form>


      <div className="flex flex-1 overflow-hidden">
        <aside className="w-64 lg:w-72 border-r border-white/5 bg-slate-950/40 flex flex-col overflow-hidden shrink-0">
          <div className="flex-1 overflow-y-auto p-3">
            <h2 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Tasks</h2>
            <div className="flex flex-col gap-1.5">
              {tasks.map((t) => (
                <div key={t.id} onClick={() => setSelectedTask(t.id)}
                  className={`p-2.5 rounded-lg border cursor-pointer transition ${selectedTask === t.id ? "border-blue-500/60 bg-slate-800/70 shadow-[0_0_12px_rgba(59,130,246,0.15)]" : "border-white/5 hover:border-white/10 bg-slate-900/40"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm truncate flex-1">{t.prompt}</span>
                    <span className={`text-[10px] font-medium whitespace-nowrap ${STATUS_COLORS[t.status] || "text-gray-400"}`}>{t.status}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-[10px] text-slate-500">${t.total_spent_usd.toFixed(4)}</span>
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

        <main className="flex-1 flex flex-col overflow-y-auto p-5 gap-4">
          {detail && (
            <div className="relative bg-slate-900/50 backdrop-blur-xl border border-white/5 rounded-xl p-4 overflow-hidden">
              <div className="absolute -top-16 -right-16 w-48 h-48 rounded-full bg-blue-600/10 blur-2xl pointer-events-none" />
              <div className="relative z-10 flex flex-wrap items-center justify-between gap-2 mb-3">
                <h2 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Cost Metrics</h2>
                <div className="flex items-center gap-2 text-[10px]">
                  {detail.model_used && <span className="font-mono px-1.5 py-0.5 bg-blue-900/40 border border-blue-700/40 rounded text-blue-300">{detail.model_used}</span>}
                  <span className="text-slate-500">In: ${detail.total_input_cost?.toFixed(6) || "0"}</span>
                  <span className="text-slate-500">Out: ${detail.total_output_cost?.toFixed(6) || "0"}</span>
                  <span className="text-slate-500">{detail.total_prompt_tokens.toLocaleString()} in / {detail.total_completion_tokens.toLocaleString()} out</span>
                </div>
              </div>
              <div className="w-full bg-slate-800/60 rounded-full h-1.5 mb-2 relative z-10">
                <div className={`${budgetBarColor} h-1.5 rounded-full transition-all`} style={{ width: `${budgetPct}%`, boxShadow: budgetPct >= 80 ? "0 0 10px rgba(239,68,68,0.6)" : "none" }} />
              </div>
              <div className="flex items-center justify-between text-[10px] text-slate-500 relative z-10">
                <span>${detail.total_spent_usd.toFixed(6)} spent</span>
                <span>{detail.target_budget_usd ? `$${detail.target_budget_usd} (${budgetPct.toFixed(1)}%)` : "No limit"}</span>
              </div>
            </div>
          )}

          <PixelOffice logs={logs} status={detail?.status || "pending"}
            totalSpent={detail?.total_spent_usd || 0} targetBudget={detail?.target_budget_usd || null}
            activeRole={activeRole} />


          <div className="bg-slate-900/50 backdrop-blur-xl border border-white/5 rounded-xl overflow-hidden flex-1 flex flex-col min-h-[320px]">
            <div className="flex items-center gap-1 px-2 pt-2 border-b border-white/5">
              {([
                ["files", "Generated Files"],
                ["planner", "Planner Execution"],
                ["terminal", "Sandbox Terminal"],
              ] as [Tab, string][]).map(([key, label]) => (
                <button key={key} onClick={() => setTab(key)}
                  className={`px-3 py-2 text-xs font-medium rounded-t-lg transition ${tab === key ? "bg-slate-800/80 text-white border-b-2 border-blue-500" : "text-slate-500 hover:text-slate-300"}`}>
                  {label}
                </button>
              ))}
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              {tab === "files" && (
                <div className="flex flex-col gap-3">
                  {files.map((f, i) => (
                    <div key={i} className="bg-slate-950/70 border border-white/5 rounded-lg overflow-hidden">
                      <div className="flex items-center justify-between px-3 py-1.5 bg-slate-800/40 border-b border-white/5">
                        <span className="text-xs font-mono text-blue-300">{f.path}</span>
                        <span className="text-[10px] text-slate-500">{f.size} bytes</span>
                      </div>
                      <pre className="p-3 text-xs font-mono text-slate-300 overflow-x-auto whitespace-pre-wrap break-words leading-relaxed">{f.code}</pre>
                    </div>
                  ))}
                  {files.length === 0 && <p className="text-sm text-slate-600">No generated files yet.</p>}
                </div>
              )}

              {tab === "planner" && (
                <div className="flex flex-col gap-2">
                  {plannerLogs.map((log, i) => (
                    <div key={i} className="px-3 py-2 rounded-lg border border-white/5 bg-slate-950/40">
                      <div className="text-[10px] text-slate-600 mb-1">{log.timestamp ? log.timestamp.slice(0,19) : ""}</div>
                      <div className="text-xs text-slate-300 whitespace-pre-wrap break-words leading-relaxed">{log.content}</div>
                    </div>
                  ))}
                  {plannerLogs.length === 0 && <p className="text-sm text-slate-600">No planner output yet.</p>}
                </div>
              )}

              {tab === "terminal" && (
                <div className="flex flex-col">
                  {termLogs.map((log, i) => (
                    <div key={i} className="font-mono text-xs leading-relaxed">
                      <div className="flex items-start gap-2 py-0.5">
                        <span className="text-slate-600 shrink-0 select-none">›</span>
                        <div className="whitespace-pre-wrap break-words flex-1 flex flex-col">
                          <span className={log.step_type === "diff" ? "text-yellow-300/80" : "text-slate-300"}>{log.content.split("\n")[0]}</span>
                          {log.content.split("\n").slice(1).map((line, j) => (
                            <span key={j} className="text-slate-400">{line}</span>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                  {termLogs.length === 0 && <p className="text-sm text-slate-600">No terminal output yet.</p>}
                  <div ref={logEndRef} />
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
