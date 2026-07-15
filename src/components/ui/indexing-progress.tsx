"use client";

import { useEffect, useState } from "react";

export interface IndexProgress {
  stage: "idle" | "parsing" | "enriching" | "embedding" | "storing" | "done" | "error";
  percent: number;
  total_files?: number;
  files_done?: number;
  current_file?: string | null;
  message: string;
  eta_seconds?: number | null;
  done: boolean;
  error?: string | null;
}

const RAG_API = process.env.NEXT_PUBLIC_RAG_API_URL || "http://localhost:8001";

const STAGE_LABEL: Record<string, string> = {
  parsing: "Reading files",
  enriching: "Enriching (slow)",
  embedding: "Embedding chunks",
  storing: "Writing index",
  done: "Complete",
  error: "Failed",
};

function fmtEta(s?: number | null) {
  if (!s || s <= 0) return null;
  if (s < 60) return `~${s}s left`;
  const m = Math.floor(s / 60);
  return `~${m}m ${s % 60}s left`;
}

/**
 * Poll-based indexing progress bar.
 *
 * The /index request blocks for the whole run, so progress can't come back on
 * that response — this polls GET /index/progress?session_id=... instead.
 * Mount it while indexing is running; call onDone when it finishes.
 */
export function IndexingProgress({
  sessionId,
  active,
  onDone,
}: {
  sessionId?: string;
  active: boolean;
  onDone?: (p: IndexProgress) => void;
}) {
  const [p, setP] = useState<IndexProgress | null>(null);

  useEffect(() => {
    if (!active) return;
    let stop = false;

    const poll = async () => {
      try {
        const url = `${RAG_API}/index/progress${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`;
        const r = await fetch(url);
        const data: IndexProgress = await r.json();
        if (stop) return;
        setP(data);
        if (data.done) {
          onDone?.(data);
          return; // stop polling
        }
      } catch {
        /* transient network error — keep polling */
      }
      if (!stop) setTimeout(poll, 700);
    };

    poll();
    return () => {
      stop = true;
    };
  }, [active, sessionId, onDone]);

  if (!active || !p || p.stage === "idle") return null;

  const failed = p.stage === "error";
  const eta = fmtEta(p.eta_seconds);

  return (
    <div className="w-full rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-white/90">
          {STAGE_LABEL[p.stage] ?? "Indexing"}
          {p.total_files ? (
            <span className="ml-2 text-xs text-white/40">
              {p.files_done ?? 0}/{p.total_files} files
            </span>
          ) : null}
        </span>
        <span className="font-mono text-xs tabular-nums text-white/60">
          {failed ? "error" : `${p.percent}%`}
          {eta && !failed ? <span className="ml-2 text-white/35">{eta}</span> : null}
        </span>
      </div>

      {/* bar */}
      <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ease-out ${
            failed
              ? "bg-red-400"
              : p.done
              ? "bg-emerald-400"
              : "bg-blue-500"
          }`}
          style={{ width: `${failed ? 100 : Math.max(2, p.percent)}%` }}
        >
          {/* subtle shimmer while working */}
          {!p.done && !failed && (
            <div className="h-full w-full animate-pulse bg-white/20" />
          )}
        </div>
      </div>

      <p
        className={`mt-2 truncate text-xs ${
          failed ? "text-red-300" : "text-white/50"
        }`}
        title={p.error || p.message}
      >
        {p.error || p.message}
      </p>
    </div>
  );
}

export default IndexingProgress;
