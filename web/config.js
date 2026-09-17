// The only thing the site needs to know about the backend.
//
// Served by the API itself (Vercel, or `uvicorn api.main:app` locally) the API
// is on the same origin, so the base is empty. The dev script serves the site
// on its own port for live editing; then the API is on :8000.
const servedByApi = location.port !== "5173";
window.NEON_API_BASE = servedByApi ? "" : "http://127.0.0.1:8000";
