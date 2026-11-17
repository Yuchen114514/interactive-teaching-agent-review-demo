# ACCoding Agent: Interactive Program Comprehension Demo

Companion implementation for **ACCoding Agent: A Persistent Workspace for Interactive Program Comprehension**.

**Authors:** Yuchen Zhang, Jiaqi Zhong, Jiaxu Min, Jingsa Xue, Wenshuai Xu, Yilong Yang, and You Song.  
**Affiliation:** School of Software, Beihang University, Beijing, China.  
**Maintainer:** [Yuchen Zhang](https://github.com/Yuchen114514).

[Hosted English demo](http://39.103.85.225:3000/en-demo/home) · [Demonstration video](https://youtu.be/oEcuqJ-JNj4)

This repository contains the English research demo frontend, its FastAPI agent backend, a five-problem catalog, and one read-only example conversation. The original production online judge and other users' conversations are not included. New conversations belong to each browser's demo visitor identity and persist in the local SQLite database.

## Requirements

- Python 3.11
- Node.js 20.19+ (or 22.13+ / 24+) and npm 10+
- A DeepSeek API key for new AI responses and diagrams

## Start locally

From the repository root, install dependencies once:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
cd frontend && npm ci && cd ..
```

In terminal 1, provide **your own** key and start the backend:

```bash
export EXTERNAL_LLM_API_KEY='your-own-key'
bash scripts/run-backend.sh
```

In terminal 2, start the frontend:

```bash
cd frontend
npm run dev:en-demo
```

Open [http://127.0.0.1:3010/en-demo/home](http://127.0.0.1:3010/en-demo/home). The API health check is [http://127.0.0.1:9002/healthz](http://127.0.0.1:9002/healthz).

The first backend run copies `demo/showcase.db` into `runtime/agent.db` and creates a private signing secret in `runtime/token-secret`. Restarting preserves conversations and browser recovery; delete `runtime/` only if you intentionally want to reset the demo. No credential or live visitor database is committed. The shared example conversation is read-only for all visitors.

## Build and deployment

`cd frontend && npm run type-check && npm run build:en-demo` creates `frontend/dist-en-demo/`. Serve those files at `/en-demo/`. Reverse-proxy `/en-demo/api/` to the FastAPI server after removing the `/en-demo` prefix. The Vite development server does this proxy automatically. Keep the signing secret and SQLite database on persistent storage, and supply the DeepSeek key through your deployment's secret manager.

The English demo uses its own catalog and guest accounts. It does not require the original OJ service, its judging system, or MCP. Model outputs are generated on demand and can differ between runs.

## Checks

```bash
cd frontend
npm run type-check
npm run build:en-demo
npm run test:demo
curl -fsS http://127.0.0.1:9002/healthz
cd ..
.venv/bin/python -m pytest backend/tests/test_en_demo.py backend/tests/test_demo_showcase_access.py -q
```

No `.env` file or API key is needed to build the frontend. The backend requires a valid key only when started for live generation.
