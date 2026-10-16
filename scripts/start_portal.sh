#!/usr/bin/env bash
set -e

echo "================================================================================"
echo "  STARTING ATLAS INSTITUTIONAL CRYPTO PORTAL (FULLSTACK)"
echo "================================================================================"
echo "  1. Starting FastAPI Backend on http://127.0.0.1:8000/api..."
echo "  2. Starting Next.js Frontend on http://localhost:3000..."
echo "================================================================================"

trap 'kill $(jobs -p)' EXIT

uv run python -m algotrader.api.portal_app &
BACKEND_PID=$!

cd frontend && npm run dev &
FRONTEND_PID=$!

sleep 3
open http://localhost:3000 || true

wait $BACKEND_PID $FRONTEND_PID
