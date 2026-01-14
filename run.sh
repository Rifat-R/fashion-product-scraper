#!/bin/bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  python -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

if [ ! -d "frontend/node_modules" ]; then
  (cd frontend && npm install)
fi

uvicorn app.main:app --reload &
API_PID=$!

trap "kill $API_PID" EXIT

(cd frontend && npm run dev)
