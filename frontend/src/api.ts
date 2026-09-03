import type { Case, Summary } from "./types";

// Change this if your FastAPI server runs somewhere other than the
// uvicorn default.
const API_BASE = "http://127.0.0.1:8000";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function fetchSummary(): Promise<Summary> {
  return getJSON<Summary>("/summary");
}

export function fetchCases(status?: string): Promise<Case[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return getJSON<Case[]>(`/cases${query}`);
}
