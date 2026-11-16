// Single source of truth for where the backend lives.
// NEXT_PUBLIC_API_URL is the backend origin WITHOUT the /api suffix,
// e.g. https://api.example.com (defaults to the local dev server).
const RAW_BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const BACKEND_URL = RAW_BACKEND_URL.replace(/\/+$/, "").replace(/\/api$/, "");

export const API_BASE_URL = `${BACKEND_URL}/api`;
