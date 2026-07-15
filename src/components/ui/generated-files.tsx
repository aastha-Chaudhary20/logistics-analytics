"use client";

import { useState } from "react";

export interface GeneratedFile {
  name: string;
  url: string;           // e.g. "/files/PO-EVN3503-20260711.pdf"
  type?: string;         // "pdf" | "markdown"
  size_bytes?: number;
}

const RAG_API = process.env.NEXT_PUBLIC_RAG_API_URL || "http://localhost:8001";

function humanSize(b?: number) {
  if (!b) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

function FileIcon({ type }: { type?: string }) {
  const isPdf = type === "pdf";
  return (
    <div
      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[10px] font-bold tracking-wide ${
        isPdf ? "bg-red-500/15 text-red-300" : "bg-blue-500/15 text-blue-300"
      }`}
    >
      {isPdf ? "PDF" : "MD"}
    </div>
  );
}

/**
 * Renders download buttons for files the assistant generated (purchase order
 * PDFs, spend reports). Fetches as a blob so the browser saves the file
 * instead of navigating away from the chat.
 */
export function GeneratedFiles({ files }: { files?: GeneratedFile[] }) {
  const [busy, setBusy] = useState<string | null>(null);

  if (!files || files.length === 0) return null;

  const download = async (f: GeneratedFile) => {
    setBusy(f.name);
    try {
      const res = await fetch(`${RAG_API}${f.url}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = f.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
    } catch (e) {
      console.error("Download failed", e);
      alert(`Could not download ${f.name}. Is the RAG API running?`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-2">
      {files.map((f) => (
        <div
          key={f.name}
          className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-2.5"
        >
          <FileIcon type={f.type} />

          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-white/90">{f.name}</div>
            <div className="text-xs text-white/40">
              {f.type === "pdf" ? "Purchase order" : "Report"}
              {f.size_bytes ? ` · ${humanSize(f.size_bytes)}` : ""}
            </div>
          </div>

          {f.type === "pdf" && (
            <a
              href={`${RAG_API}${f.url}`}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/70 transition hover:border-white/25 hover:text-white"
            >
              Preview
            </a>
          )}

          <button
            onClick={() => download(f)}
            disabled={busy === f.name}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-blue-500 disabled:opacity-50"
          >
            {busy === f.name ? (
              "Saving…"
            ) : (
              <>
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Download
              </>
            )}
          </button>
        </div>
      ))}
    </div>
  );
}

export default GeneratedFiles;
