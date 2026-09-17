// The only thing the static site needs to know about the backend.
//
// Localhost gets the local API; anything else (Vercel) gets the deployed one.
// Set PROD_API_BASE once, after the API is up on Render.
const PROD_API_BASE = "https://neon-agent-api.onrender.com";

const local = ["localhost", "127.0.0.1", ""].includes(location.hostname);
window.NEON_API_BASE = local ? "http://127.0.0.1:8000" : PROD_API_BASE;