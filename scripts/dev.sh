#!/usr/bin/env bash
# Start everything and print the one URL that matters.
#
# The product is two processes — a JSON API and a static site on its own origin —
# which is right for deployment (Render + Vercel) and a nuisance locally. This
# hides that.
#
#   ./scripts/dev.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

# uv may be on PATH or only in ~/.local/bin (which a non-interactive shell does
# not add). `command -v` resolves both a bare name and a path; `[ -x ]` does not
# resolve a bare name against PATH, which is what broke this before.
if [ -z "${UV:-}" ]; then
  if command -v uv >/dev/null 2>&1; then
    UV=uv
  elif [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
  fi
fi
command -v "${UV:-uv}" >/dev/null 2>&1 || {
  echo "uv not found on PATH or in ~/.local/bin. Install it, or set UV=/path/to/uv"
  exit 1
}
UV="${UV:-uv}"

API_PORT="${API_PORT:-8000}"
SITE_PORT="${SITE_PORT:-5173}"

# Only pass --env-file when there is one; uvicorn errors if the file is missing.
ENV_ARGS=()
[ -f .env ] && ENV_ARGS=(--env-file .env)

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "starting API on :$API_PORT"
"$UV" run uvicorn api.main:app --port "$API_PORT" "${ENV_ARGS[@]}" --reload &

echo "starting site on :$SITE_PORT"
python3 -m http.server -d web "$SITE_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &

# Wait for the API before announcing anything, so the URL works when printed.
for _ in $(seq 1 40); do
  if curl -fsS -m 1 "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

CHAT=$(curl -fsS -m 2 "http://127.0.0.1:$API_PORT/api/chat/status" 2>/dev/null || echo '{}')
case "$CHAT" in
  *'"available":true'*) CHAT_MSG="chat: on" ;;
  *)                    CHAT_MSG="chat: off (no credentials — everything else still works)" ;;
esac

cat <<BANNER

  ──────────────────────────────────────────────
   Open  http://127.0.0.1:$SITE_PORT
  ──────────────────────────────────────────────
   API   http://127.0.0.1:$API_PORT/docs
   $CHAT_MSG

   Ctrl-C stops both.

BANNER

wait
