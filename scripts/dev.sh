#!/usr/bin/env bash
#
# Start the local dev stack: verifies Ollama + model, then runs backend and frontend.
#
# Usage: ./scripts/dev.sh [--model <tag>] [--skip-ollama]
#   --model <tag>   Ollama model tag to use. Defaults to $OLLAMA_MODEL, then qwen3:8b.
#   --skip-ollama   Skip the Ollama checks (e.g. when running with LLM_PROVIDER=fake).

set -euo pipefail

# ---------------------------------------------------------------------------
# Setup: arguments + paths
# ---------------------------------------------------------------------------

MODEL="${OLLAMA_MODEL:-qwen3:8b}"
SKIP_OLLAMA=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            MODEL="${2:?--model requires a value}"
            shift 2
            ;;
        --skip-ollama)
            SKIP_OLLAMA=true
            shift
            ;;
        -h | --help)
            sed -n '3,7p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1 (see --help)" >&2
            exit 1
            ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"

# ---------------------------------------------------------------------------
# Ollama: ensure server is reachable and the model is pulled
# ---------------------------------------------------------------------------

ollama_server_up() {
    curl -fsS --max-time 2 'http://localhost:11434/api/version' >/dev/null 2>&1
}

if [[ "$SKIP_OLLAMA" == false ]]; then
    if ! command -v ollama >/dev/null 2>&1; then
        echo 'ollama is not installed. Install from https://ollama.com or rerun with --skip-ollama.' >&2
        exit 1
    fi

    if ! ollama_server_up; then
        echo 'Starting ollama serve...'
        # Detach so the server outlives this script (matches the Ollama desktop app behavior).
        nohup ollama serve >/dev/null 2>&1 &
        disown
        # Poll for up to ~15s while the server boots.
        for _ in $(seq 1 30); do
            ollama_server_up && break
            sleep 0.5
        done
        if ! ollama_server_up; then
            echo 'ollama serve did not become ready on :11434.' >&2
            exit 1
        fi
    fi

    # `ollama list` prints a header row then "NAME ID SIZE MODIFIED"; match the NAME column.
    if ! ollama list | tail -n +2 | awk '{print $1}' | grep -qxF "$MODEL"; then
        echo "Pulling model $MODEL (first run only)..."
        ollama pull "$MODEL"
    fi
    echo "Ollama ready with $MODEL."
fi

# ---------------------------------------------------------------------------
# Backend + frontend
# ---------------------------------------------------------------------------

export OLLAMA_MODEL="$MODEL"
BACKEND_PID=""
FRONTEND_PID=""

# Stop a background process and everything it spawned.
stop_tree() {
    local pid="$1"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    if [[ -r "/proc/$pid/winpid" ]]; then
        # Git Bash/MSYS: $! is an MSYS pid and `kill` won't reach native child processes
        # (uvicorn's reload worker, node), so kill the Windows process tree instead.
        # `//` stops MSYS from rewriting the flags as paths.
        taskkill //PID "$(cat "/proc/$pid/winpid")" //T //F >/dev/null 2>&1 || true
    else
        # uv and npm both forward SIGTERM to their child process.
        kill "$pid" 2>/dev/null || true
    fi
}

cleanup() {
    trap - EXIT INT TERM
    echo 'Stopping backend + frontend...'
    stop_tree "$FRONTEND_PID"
    stop_tree "$BACKEND_PID"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo 'Starting backend on http://localhost:8000 ...'
uv --directory "$BACKEND_DIR" run uvicorn main:app --app-dir src --reload --port 8000 &
BACKEND_PID=$!

echo 'Starting frontend on http://localhost:5173 ...'
# Backgrounded (not run in the foreground) so `wait` below returns as soon as a signal arrives;
# bash defers traps until a foreground command exits, which would leave the servers running.
npm --prefix "$FRONTEND_DIR" run dev &
FRONTEND_PID=$!

# Exit (and clean up the other server) when either one stops.
wait -n "$BACKEND_PID" "$FRONTEND_PID"
