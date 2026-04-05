# VSWE — Virtual Software Engineer

A production-ready AI coding agent that supports two workflows:

1. **GitHub Issue Workflow** — A GitHub App listens to issues via webhooks, asks clarifying questions, writes code, and opens PRs automatically.
2. **Interactive Chat GUI** — A React-based chat interface with real-time WebSocket streaming, multi-model support, and parallel sessions.

The system also manages **ML/DL training jobs** on AWS Batch with deterministic compute allocation, adaptive checkpointing, and cost tracking.

---

## Prerequisites

Install these before starting:

### 1. Homebrew (macOS package manager)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Python 3.12
```bash
brew install python@3.12
```

### 3. Node.js
```bash
brew install node
```

### 4. Docker Desktop
Download and install from https://www.docker.com/products/docker-desktop/

### 5. AWS CLI
```bash
brew install awscli
aws configure
# Enter your AWS Access Key ID, Secret Access Key, region (us-east-1), output (json)
```

### 6. ngrok (for local GitHub webhook testing)
```bash
brew install ngrok
```
Sign up at https://ngrok.com, then:
```bash
ngrok config add-authtoken <your-token>
```

---

## GitHub App Setup

This is required for the GitHub Issue workflow. Skip this section if you only want to test the Chat GUI.

1. Go to **https://github.com/settings/apps/new**

2. Fill in:
   - **GitHub App name**: `VSWE Agent` (or any unique name)
   - **Homepage URL**: `https://github.com` (placeholder)
   - **Callback URL**: leave blank for now
   - **Webhook URL**: your ngrok URL + `/webhooks/github` (e.g. `https://abc123.ngrok-free.app/webhooks/github`)
   - **Webhook secret**: generate one with `openssl rand -hex 32`

3. Set **Permissions** (Repository permissions):
   - Issues: **Read & Write**
   - Pull requests: **Read & Write**
   - Contents: **Read & Write**

4. **Subscribe to events**:
   - [x] Issues
   - [x] Issue comment

5. Set **Where can this app be installed?**: Only on this account

6. Click **Create GitHub App**

7. On the app page, note the **App ID**

8. Click **Generate a private key** — this downloads a `.pem` file

9. To get the private key as a string for `.env`, run:
   ```bash
   awk 'NF {sub(/\r/, ""); printf "%s\\n",$0;}' ~/Downloads/<your-app-name>*.pem
   ```
   Copy the output (ignore any trailing `%`).

10. **Install the app** on your account/repos:
    - Go to https://github.com/settings/apps → your app → Install App → Install

---

## Local Setup

### 1. Clone and enter the project
```bash
cd /path/to/vswe
```

### 2. Configure environment variables
```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and fill in:
```env
# Required for Chat GUI
ANTHROPIC_API_KEY=sk-ant-...

# Optional — only needed for GPT models in chat
OPENAI_API_KEY=sk-...

# Required for GitHub Issue workflow
GITHUB_APP_ID=<from step 7>
GITHUB_APP_PRIVATE_KEY=<from step 9 — single line with \n separators>
GITHUB_WEBHOOK_SECRET=<from step 2>

# Required for GitHub Issue workflow via ngrok
NGROK_URL=<your ngrok URL>
```

### 3. Start DynamoDB Local
```bash
docker compose up -d dynamodb-local
```

### 4. Install and start the backend
```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

### 5. Install and start the frontend (new terminal)
```bash
cd frontend
npm install
npm run dev
```

### 6. Open the app
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8080
- **Health check**: http://localhost:8080/health
- **API docs**: http://localhost:8080/docs

### 7. Start ngrok (for GitHub webhooks — new terminal)
```bash
ngrok http 8080
```
Copy the `https://` URL and update:
- Your `.env` `NGROK_URL=` value
- Your GitHub App's Webhook URL to `<ngrok-url>/webhooks/github`

---

## Testing the Chat GUI

1. Open http://localhost:3000
2. The chat interface should load with a dark theme
3. Click **New Session** in the sidebar
4. Type a message and press Enter
5. The agent will respond via WebSocket with streaming updates

Note: The Chat GUI works with just `ANTHROPIC_API_KEY` configured. No GitHub App or ngrok needed.

## Testing the GitHub Issue Workflow

1. Make sure ngrok is running and the webhook URL is configured
2. Install the GitHub App on a test repository
3. Configure the agent for the repo:
   ```bash
   curl -X PUT http://localhost:8080/api/config/repo:owner/repo-name \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer test-user" \
     -d '{"enabled": true}'
   ```
4. Create an issue on the repo
5. The agent should comment on the issue within seconds

---

## Project Structure

```
vswe/
  backend/           # Python FastAPI backend
    app/
      api/           # REST + WebSocket endpoints
      agent/         # Orchestrator, tools, prompts
      llm/           # Anthropic + OpenAI clients
      jobs/          # Job profiler, scheduler
      checkpoints/   # ML checkpoint management
      cost/          # Cost tracking + budget
      db/            # DynamoDB models + helpers
      github_app/    # GitHub App client
      webhooks/      # Lambda handler + local dev handler
  frontend/          # React + TypeScript + Vite
    src/
      components/    # Chat, Dashboard, Config, Jobs views
      hooks/         # WebSocket, Sessions, Costs hooks
      stores/        # Zustand state management
      lib/           # API client, WebSocket manager
  infrastructure/    # AWS CDK stacks
    cdk/stacks/      # VPC, ECS, Batch, Storage, Lambda, CDN
  training/          # ML checkpoint library
    vswe_checkpoint/ # Importable by training scripts
```

---

## Deploying to AWS

```bash
cd infrastructure/cdk
pip install -r requirements.txt
cdk bootstrap        # First time only
cdk deploy --all     # Deploy all stacks
```

To tear down:
```bash
cdk destroy --all
```

---

## Architecture

See `CLAUDE.md` for architecture notes and the plan file at `.claude/plans/` for the full design document.

Key points:
- **Lambda** is a thin webhook receiver — all agent logic runs in **ECS Fargate**
- LLM Router, Job Scheduler, and GitHub Client are libraries in the same process, not separate services
- ML training jobs run on **AWS Batch** (Spot EC2), decoupled from the agent
- **DynamoDB** for state, **EFS** for workspaces, **S3** for archives
- Infrastructure managed with **AWS CDK** (Python)

---

## Cost

The system is designed to run a full demo for under **$5**, including:
- LLM API calls (Anthropic / OpenAI)
- AWS compute (Fargate, Batch spot instances)
- Storage (DynamoDB, EFS, S3)

The cost dashboard at `/costs` in the frontend tracks spending in real-time.
