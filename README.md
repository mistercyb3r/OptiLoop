# OptiLoop

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-14+-black.svg)](https://nextjs.org/)
[![Docker](https://img.shields.io/badge/Docker-24+-2496ED.svg)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/tests-111%20passing-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Autonomous Multi-Agent Coding System** — An AI-driven coding assistant that uses a **Planner → Executor → Reviewer** tri-agent loop to autonomously implement, test, and refine code changes inside isolated Docker sandboxes with real-time cost tracking.

![Dashboard Screenshot](web/public/screenshot.png)

## How It Works

OptiLoop spins up three AI agents that collaborate in an iterative loop:

1. **Planner** (High-Reasoning) analyzes the task and produces a structured execution plan
2. **Executor** (Standard) carries out the plan — writing files, running commands in a Docker sandbox
3. **Reviewer** (Standard) runs tests, inspects diffs, and either approves or requests revisions

Each agent is powered by a dynamically selected LLM via [OpenRouter](https://openrouter.ai/), with automatic budget enforcement and model downgrading when costs approach limits.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     OptiLoop Backend (FastAPI)                   │
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │ Planner  │───▶│ Executor │───▶│ Reviewer │───▶│  Budget  │  │
│  │ (Tier 3) │    │ (Tier 2) │    │ (Tier 2) │    │  Guard   │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘  │
│       │               │               │                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                  │
│  │  Model   │    │  Docker  │    │  Cost    │                  │
│  │  Router  │    │ Sandbox  │    │Calculator│                  │
│  └──────────┘    └──────────┘    └──────────┘                  │
│       │                                                        │
│  ┌──────────────────────────────────────────┐                  │
│  │         OpenRouter API (LLM calls)        │                  │
│  └──────────────────────────────────────────┘                  │
└──────────────────┬──────────────────────────┬───────────────────┘
                   │                          │
          ┌────────▼────────┐      ┌──────────▼──────────┐
          │  Web Dashboard  │      │    Terminal CLI      │
          │  (Next.js:3000) │      │    (Typer + Rich)    │
          │  SSE Live Stream│      │  submit/status/logs  │
          │  Pixel Art View │      │  metrics/stop        │
          └─────────────────┘      └──────────────────────┘
```

### Core Modules

| Module | Description |
|---|---|
| **Cost Calculator** | Per-token pricing for 4 OpenRouter models (DeepSeek, Xiaomi, Claude, GPT-4o-mini) |
| **Model Router** | Dynamic 3-tier selection with automatic budget downgrade at 80% spend |
| **Docker Sandbox** | Isolated container execution with command blocklist & 512MB memory limits |
| **Orchestrator** | Async Planner→Executor→Reviewer loop with configurable max iterations |
| **REST API** | FastAPI endpoints with SSE streaming for real-time log updates |
| **Web Dashboard** | Next.js + Tailwind with pixel-art agent visualizer and cost tracking |
| **CLI** | Typer + Rich terminal interface for task submission and monitoring |

## Quickstart — Linux Home Server (Docker Compose)

### Prerequisites
- Docker and Docker Compose v2+
- Git

### Deploy

```bash
git clone https://github.com/yourusername/optiloop.git
cd optiloop

# Run interactive setup (checks Docker, prompts for API key)
chmod +x setup.sh
./setup.sh
```

Or manually:
```bash
cp .env.example .env
nano .env  # Set your OPENROUTER_API_KEY (get one free at openrouter.ai/keys)

docker compose up -d --build
```

### Access
- **Dashboard:** `http://your-server:3000`
- **API:** `http://your-server:8000`

The `docker-compose.yml` automatically:
- Mounts `/var/run/docker.sock` so OptiLoop can spawn worker containers
- Persists SQLite data in a named Docker volume
- Links the Next.js frontend to the FastAPI backend

### Verify
```bash
docker compose ps                          # Check services
docker compose exec backend pytest tests/ -v  # Run tests
docker compose logs -f backend             # Watch logs
docker compose down                        # Stop
```

## Quickstart — Windows 11 / WSL2

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker Desktop (running)
- Git

### Option A: Native Windows
```powershell
git clone https://github.com/yourusername/optiloop.git
cd optiloop
setup.bat
```

### Option B: WSL2 (Recommended for Docker)
```bash
git clone https://github.com/yourusername/optiloop.git
cd optiloop
chmod +x setup.sh
./setup.sh
```

### Manual Setup
```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set your OPENROUTER_API_KEY

# Run all 111 tests
pytest tests/ -v

# Start backend
uvicorn app.api.server:app --reload --port 8000

# Start dashboard (in another terminal)
cd web && npm install && npm run dev
```

Open **http://localhost:3000** for the dashboard.

## Using the Web UI

1. Enter a coding prompt in the **New Task** form
2. Set a budget (default $0.50) — the system stops if costs approach this limit
3. Click **Run Task** — the pixel-art agents animate as work progresses
4. Watch live output in the terminal scrollbox (SSE streaming)
5. Monitor costs in real-time on the **Cost Metrics** card
6. Click **Emergency Stop** to cancel at any time

### Pixel Art Visualizer
The dashboard includes a retro pixel-art office showing three agent desks:
- **Architect** (blue) — plans the approach
- **Developer** (green) — writes code and runs commands
- **Inspector** (amber) — reviews and tests

Each desk glows when active, with animated speech bubbles showing the agent's current thought.

## Using the Terminal CLI

```bash
# Submit a task
python -m cli.main submit "Build a REST API with CRUD endpoints" --budget 1.0

# Check status
python -m cli.main status <task-id>

# View logs
python -m cli.main logs <task-id>

# Stream live logs (like tail -f)
python -m cli.main logs <task-id> --follow

# View cost breakdown per agent role
python -m cli.main metrics <task-id>

# Cancel a running task
python -m cli.main stop <task-id>
```

The CLI talks to the FastAPI backend via REST. Set `OPTILOOP_API_URL` to point to a remote server.

## Cost Optimization & Dynamic Routing

OptiLoop's **ModelRouter** automatically selects the cheapest capable model for each agent role:

| Tier | Models | Used For |
|---|---|---|
| **Tier 1** (Fast/Cheap) | DeepSeek V4 Flash, GPT-4o-mini | Fallback when budget is tight |
| **Tier 2** (Standard) | DeepSeek V4 Flash, Xiaomi MiMo v2.5 | Executor, Reviewer (default) |
| **Tier 3** (High-Reasoning) | Xiaomi MiMo v2.5, Claude 3.5 Sonnet | Planner (default), high-complexity tasks |

### Budget Guard
When `total_spent / target_budget >= 80%`, the system **automatically downgrades** all agents to Tier 1 (cheapest) models, regardless of role. If the budget is fully exceeded, the task is marked `FAILED` and execution stops.

### Live Model Catalogue
The router fetches live model data from `openrouter.ai/api/v1/models` every 5 minutes, with automatic fallback to local pricing if the API is unreachable.

## Architecture & Security

### Container Isolation
Every code execution happens inside isolated Docker containers with:
- **Memory limit:** 512MB per container
- **CPU quota:** 50% of one CPU core
- **Privileged mode:** Disabled
- **Security options:** `no-new-privileges`
- **Network:** Enabled (for package installs)

### Command Safety Blocklist
The sandbox blocks dangerous commands before they reach the container:
```
rm -rf /, mkfs, dd, wget, curl, docker, kubectl, mount,
fdisk, chroot, eval(), exec(), subprocess, import ctypes, ...
```

### Workspace Isolation
Each task gets its own temporary directory (using `tempfile.gettempdir()`) that is:
- Created when the task starts
- Mounted read-write into the container
- Deleted when the task completes or is cancelled

### Data Privacy
- All execution happens locally — no code leaves your machine except to the OpenRouter API for LLM inference
- SQLite database stays on your server
- Docker socket access is required only for worker container management

## Testing

```bash
# Run all 111 tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_cost_engine.py -v

# Run with coverage report
pytest tests/ --cov=app --cov-report=term-missing
```

### Test Coverage

| Test File | Tests | What's Tested |
|---|---|---|
| `test_cost_engine.py` | 23 | Database models, cost calculation, budget checks |
| `test_router.py` | 26 | Catalogue fetching, tier classification, model selection, budget guard, LLM calls |
| `test_sandbox.py` | 30 | Container lifecycle, safety blocklist, file I/O, git diff |
| `test_orchestrator.py` | 13 | Task completion, revision retry, max iterations, budget cutoff, cleanup |
| `test_api.py` | 9 | REST endpoints (create, list, detail, stop) |
| `test_cli.py` | 10 | CLI commands (submit, status, logs, metrics, stop) |

## Project Structure

```
optiloop/
├── app/
│   ├── api/server.py           # FastAPI REST API + SSE streaming
│   ├── core/
│   │   ├── cost_calculator.py  # Token pricing & budget enforcement
│   │   ├── orchestrator.py     # Planner-Executor-Reviewer loop
│   │   ├── prompts.py          # Structured agent system prompts
│   │   ├── router.py           # Dynamic model selection (3 tiers)
│   │   └── sandbox.py          # Docker execution sandbox
│   └── models/db_models.py     # SQLModel database tables
├── cli/main.py                 # Typer + Rich CLI
├── web/
│   ├── app/page.tsx            # Dashboard page
│   ├── components/PixelOffice.tsx  # Pixel art agent visualizer
│   └── ...
├── tests/                      # 111 pytest tests
├── Dockerfile                  # Backend container
├── Dockerfile.web              # Frontend container (multi-stage)
├── docker-compose.yml          # Full stack orchestration
├── setup.sh                    # Linux/macOS setup script
├── setup.bat                   # Windows setup script
├── .env.example                # Environment config template
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

## Contributing

1. **Fork** the repository
2. **Create** a feature branch: `git checkout -b feature/my-feature`
3. **Make** changes and add tests
4. **Run** the full test suite: `pytest tests/ -v`
5. **Commit** with a clear message
6. **Push** and open a **Pull Request**

### Guidelines
- All new features must include tests
- Mock external services (Docker, OpenRouter API) in tests
- Follow existing code patterns (SQLModel for DB, httpx for HTTP)
- Keep the test suite under 10 seconds
- Update README if adding user-facing features

## Supported Models

| Model | Cost (per 1M tokens) | Tier |
|---|---|---|
| `deepseek/deepseek-v4-flash` | $0.27 prompt / $1.10 completion | 1 (Cheap) |
| `xiaomi/mimo-v2.5` | $0.14 prompt / $0.56 completion | 1-2 |
| `openai/gpt-4o-mini` | $0.15 prompt / $0.60 completion | 1 |
| `anthropic/claude-3.5-sonnet` | $3.00 prompt / $15.00 completion | 3 (Premium) |

## License

MIT License — see [LICENSE](LICENSE) for details.
