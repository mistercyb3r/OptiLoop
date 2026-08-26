"use client";

import { useEffect, useState } from "react";

interface LogEvent { step_type: string; content: string; timestamp: string; }

interface PixelOfficeProps {
  logs: LogEvent[]; status: string; totalSpent: number;
  targetBudget: number | null; activeRole?: string | null;
}

type AgentRole = "planner" | "executor" | "reviewer" | null;

const AGENT_COLORS: Record<string, { body: string; accent: string; glow: string }> = {
  planner:  { body: "#60a5fa", accent: "#2563eb", glow: "rgba(96,165,250,0.45)" },
  executor: { body: "#34d399", accent: "#059669", glow: "rgba(52,211,153,0.45)" },
  reviewer: { body: "#fbbf24", accent: "#d97706", glow: "rgba(251,191,36,0.45)" },
};

const AGENT_LABELS: Record<string, string> = {
  planner: "Architect", executor: "Developer", reviewer: "Inspector",
};

const AGENT_VERBS: Record<string, string> = {
  planner: "THINKING...", executor: "CODING...", reviewer: "TESTING...",
};




function Workstation({ role, isActive, running, bubbleText }: {
  role: string; isActive: boolean; running: boolean; bubbleText: string;
}) {
  const c = AGENT_COLORS[role] || AGENT_COLORS.planner;
  const label = AGENT_LABELS[role] || role;
  const verb = AGENT_VERBS[role] || "WORKING...";
  const [frame, setFrame] = useState(0);
  const [breathe, setBreathe] = useState(0);

  useEffect(() => {
    if (!isActive) return;
    const id = setInterval(() => setFrame((f) => (f + 1) % 4), 250);
    return () => clearInterval(id);
  }, [isActive]);

  useEffect(() => {
    const id = setInterval(() => setBreathe((b) => b + 1), 900);
    return () => clearInterval(id);
  }, []);

  const breatheScale = 1 + Math.sin(breathe * 0.6) * 0.03;

  return (
    <div className="relative flex flex-col items-center px-3 py-2">
      <div className="absolute inset-x-0 -top-2 h-16 opacity-40 pointer-events-none"
           style={{ background: `radial-gradient(ellipse, ${c.glow} 0%, transparent 70%)` }} />
      <div className="relative z-10 mb-1 px-2 py-0.5 rounded text-[8px] font-mono tracking-wider"
           style={{
             backgroundColor: isActive ? c.accent : "rgba(15,23,42,0.9)",
             color: isActive ? "#fff" : "#64748b",
             border: isActive ? `1px solid ${c.body}` : "1px solid #334155",
             boxShadow: isActive ? `0 0 12px ${c.glow}` : "none",
           }}>
        {isActive ? verb : "IDLE"}
      </div>
      {isActive && running && bubbleText && (
        <div className="absolute -top-9 left-1/2 -translate-x-1/2 z-20 whitespace-nowrap">
          <div className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-[9px] text-slate-200 max-w-[190px] truncate"
               style={{ imageRendering: "pixelated" }}>{bubbleText}</div>
          <div className="w-0 h-0 mx-auto border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-slate-800" />
        </div>
      )}
      <div className="relative z-10">
        <div className="rounded-sm border-2 border-slate-700 overflow-hidden"
             style={{ width: 54, height: 34, backgroundColor: "#0f172a",
                      boxShadow: isActive ? `0 0 16px ${c.glow}, inset 0 0 8px ${c.glow}` : "0 0 4px rgba(0,0,0,0.5)" }}>
          <div className="w-full h-full flex flex-col gap-0.5 p-1"
               style={{ transform: `scaleY(${isActive ? "1" : "0.96"})`, transition: "transform 0.3s" }}>
            <div className="h-0.5 rounded-sm" style={{ width: "85%", backgroundColor: c.body, opacity: isActive ? 0.9 : 0.25 }} />
            <div className="h-0.5 rounded-sm" style={{ width: "60%", backgroundColor: c.body, opacity: isActive ? 0.7 : 0.2 }} />
            <div className="h-0.5 rounded-sm" style={{ width: "75%", backgroundColor: c.body, opacity: isActive ? 0.5 : 0.15 }} />
          </div>
        </div>
        <div className="mx-auto" style={{ width: 8, height: 6, backgroundColor: "#334155" }} />
      </div>
      <div className="relative z-10 -mt-1 flex items-end"
           style={{ transform: `scale(${breatheScale})`, transition: "transform 0.9s ease-in-out" }}>
        <div className="rounded-sm" style={{ width: 22, height: 18, backgroundColor: "#1e293b", border: "1px solid #334155" }} />
        <div className="relative -ml-4" style={{ width: 28, height: 40 }}>
          <div className="absolute rounded-sm" style={{ width: 16, height: 12, left: 6, top: 0,
                   backgroundColor: c.body, boxShadow: isActive ? `0 0 8px ${c.glow}` : "none" }} />
          <div className="absolute" style={{ left: 9, top: 4, width: 2.5, height: 2.5, backgroundColor: "#0f172a" }} />
          <div className="absolute" style={{ left: 15, top: 4, width: 2.5, height: 2.5, backgroundColor: "#0f172a" }} />
          <div className="absolute rounded-sm" style={{ width: 18, height: 14, left: 5, top: 13, backgroundColor: c.accent }} />
          <div className="absolute rounded-sm transition-all"
               style={{ width: 7, height: 3, left: frame % 2 === 0 ? 0 : 3, top: 20, backgroundColor: c.body }} />
          <div className="absolute rounded-sm transition-all"
               style={{ width: 7, height: 3, left: frame % 2 === 0 ? 22 : 19, top: 20, backgroundColor: c.body }} />
          <div className="absolute" style={{ width: 6, height: 10, left: 7, top: 27, backgroundColor: c.accent }} />
          <div className="absolute" style={{ width: 6, height: 10, left: 15, top: 27, backgroundColor: c.accent }} />
        </div>
      </div>
      <div className="relative z-10 rounded-sm" style={{ width: 66, height: 7, backgroundColor: "#78350f",
                   boxShadow: isActive ? `0 6px 16px ${c.glow}` : "0 4px 8px rgba(0,0,0,0.4)",
                   borderBottom: "2px solid #451a03" }} />
      <div className="mt-1 text-[10px] font-mono text-slate-400">{label}</div>
    </div>
  );
}



function BudgetIndicator({ spent, target }: { spent: number; target: number | null }) {
  if (!target || target <= 0) {
    return (<div className="flex items-center gap-2 text-[10px] font-mono text-slate-500">
      <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />NO BUDGET LIMIT</div>);
  }
  const pct = Math.min(100, (spent / target) * 100);
  const color = pct >= 80 ? "#ef4444" : pct >= 50 ? "#f59e0b" : "#22c55e";
  const label = pct >= 80 ? "CRITICAL" : pct >= 50 ? "WARNING" : "OK";
  return (
    <div className="flex items-center gap-2 text-[10px] font-mono">
      <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color,
        boxShadow: pct >= 80 ? `0 0 6px ${color}` : "none",
        animation: pct >= 80 ? "pulse 0.5s infinite" : "none" }} />
      <span style={{ color }}>${spent.toFixed(4)} / ${target}</span>
      <div className="w-20 h-2 bg-slate-800 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span style={{ color }}>{label}</span>
    </div>
  );
}

export default function PixelOffice({ logs, status, totalSpent, targetBudget, activeRole: activeRoleProp }: PixelOfficeProps) {
  const latestLog = logs.length > 0 ? logs[logs.length - 1] : null;
  const activeRole: AgentRole = (activeRoleProp as AgentRole) || (latestLog?.step_type as AgentRole) || null;
  const isRunning = status === "running";
  const bubbleText = latestLog ? latestLog.content.slice(0, 60) : "";
  const isDone = status === "completed" || status === "failed" || status === "cancelled";

  return (
    <div className="relative bg-slate-900/60 backdrop-blur-xl border border-white/5 rounded-xl p-4 overflow-hidden">
      <div className="absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-slate-950/60 to-transparent pointer-events-none" />
      <div className="flex items-center justify-between mb-3 relative z-10">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Agent Studio</h2>
        <BudgetIndicator spent={totalSpent} target={targetBudget} />
      </div>
      <div className="flex items-end justify-center gap-4 sm:gap-8 py-3 px-2 relative z-10">
        <div className="absolute bottom-3 left-0 right-0 h-px bg-slate-800/60" />
        {["planner", "executor", "reviewer"].map((role) => (
          <Workstation key={role} role={role} isActive={isRunning && activeRole === role}
            running={isRunning} bubbleText={activeRole === role ? bubbleText : ""} />
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between text-[10px] font-mono text-slate-500 relative z-10">
        <span>{isDone ? (status === "completed" ? "All agents finished" : `Task ${status}`) : isRunning ? "Processing..." : "Waiting to start"}</span>
        <span>{logs.length} log entries</span>
      </div>
    </div>
  );
}
