#!/usr/bin/env bash
set -euo pipefail

artifact_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="$artifact_root/runtime"
mkdir -p "$runtime_dir/uploads"

if [[ ! -f "$runtime_dir/agent.db" ]]; then
  cp "$artifact_root/demo/showcase.db" "$runtime_dir/agent.db"
fi
if [[ ! -f "$runtime_dir/token-secret" ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$runtime_dir/token-secret"
  chmod 600 "$runtime_dir/token-secret"
fi
if [[ -z "${EXTERNAL_LLM_API_KEY:-}" ]]; then
  echo 'Set EXTERNAL_LLM_API_KEY to your own DeepSeek API key before starting.' >&2
  exit 1
fi
if [[ ! -x "$artifact_root/.venv/bin/uvicorn" ]]; then
  echo 'Install the backend first: python3.11 -m venv .venv && .venv/bin/pip install -e ./backend' >&2
  exit 1
fi

export APP_ENV=prod
export DEMO_MODE=true
export DEMO_AGENT_READY=false
export DATABASE_URL="sqlite:///$runtime_dir/agent.db"
export TOKEN_SECRET="$(cat "$runtime_dir/token-secret")"
export EXTERNAL_LLM_BASE_URL="${EXTERNAL_LLM_BASE_URL:-https://api.deepseek.com}"
export EXTERNAL_LLM_MODEL="${EXTERNAL_LLM_MODEL:-deepseek-flash}"
export EXTERNAL_LLM_TIMEOUT_SECONDS=45
export EXTERNAL_LLM_MAX_RETRIES=1
export TEACHING_AGENT_PROVIDERS_ENABLED=true
export TEACHING_AGENT_PROVIDER_BACKEND=agno
export TEACHING_AGENT_PROVIDER_MAX_TOKENS=4096
export TEACHING_AGENT_PROVIDER_AGENTS=MainDialogAgent,TeachingSupervisor,ProblemIdentifierAgent,AlgorithmIdentifierAgent,AlgorithmPrimerAgent,PlanRevisionAgent,PlanDecomposerAgent,HintPolicyAgent,EncouragementAgent,TutorAgent,DiagramAgent,BoardworkAgent,CodingAgent,DebugAgent,MemoryManagerAgent,MemoryConsolidatorAgent
export MCP_ENABLED=false
export SUBMISSION_DEMO_MODE=true
export MEDIA_UPLOAD_DIR="$runtime_dir/uploads"
export FRONTEND_ORIGIN="${DEMO_FRONTEND_ORIGIN:-http://127.0.0.1:3010}"
export DEMO_SHOWCASE_SESSION_UUID=35b6e05f-dabd-4cc8-a909-9059f6d03c0b

cd "$artifact_root/backend"
exec "$artifact_root/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "${DEMO_BACKEND_PORT:-9002}"
