"""HTTP front door. Thin: it validates input, calls tools/, and serialises the result.

All planning logic lives below this layer (see ARCHITECTURE.md). Nothing here
computes geometry or decides a verdict.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

VERSION = "0.1.0"

# The static site is served from another origin (Vercel), so the API must allow it.
# Comma-separated list; "*" in local development.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(
    title="neon-agent API",
    version=VERSION,
    description=(
        "Deterministic infrastructure planning tools: real open data layers, "
        "cited constraints, generators and checks."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe, and what the static site uses to prove it reached the API."""
    return {
        "status": "ok",
        "version": VERSION,
        "service": "neon-agent",
    }
