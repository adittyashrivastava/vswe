# VSWE — Virtual Software Engineer

A production-ready AI coding agent that supports two workflows:

1. **GitHub Issue Workflow** — A GitHub App listens to issues via webhooks, asks clarifying questions, writes code, and opens PRs automatically.
2. **Interactive Chat GUI** — A React-based chat interface with real-time WebSocket streaming, multi-model support, and parallel sessions.

The system also manages **compute jobs** on ECS Fargate with deterministic resource allocation, adaptive checkpointing, and cost tracking.

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

### 6. AWS CDK CLI (infrastructure deployment)
```bash
npm install -g aws-cdk
```

### 7. direnv (auto-loads environment variables per project)
```bash
brew install direnv
echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc
source ~/.zshrc
```

### 8. ngrok (for local GitHub webhook testing)
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
# Required — LLM provider
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...              # Optional — only needed for GPT models

# Required — GitHub OAuth (needed for login, even for Chat GUI only)
GITHUB_CLIENT_ID=<from GitHub App settings → Client ID>
GITHUB_CLIENT_SECRET=<from GitHub App settings → Client secrets>

# Required only for GitHub Issue workflow
GITHUB_APP_ID=<from step 7>
GITHUB_APP_PRIVATE_KEY=<from step 9 — single line with \n separators>
GITHUB_WEBHOOK_SECRET=<from step 2>
NGROK_URL=<your ngrok URL>
```

### 3. Activate direnv

The project includes an `.envrc` file that auto-loads `backend/.env` and ECS job config into your shell. Activate it once:

```bash
direnv allow
```

From now on, every time you `cd` into the project (or open a terminal here), all environment variables are loaded automatically. You'll see direnv print the exported variables on entry. If you later edit `backend/.env` or `.envrc`, run `direnv allow` again.

### 4. Start DynamoDB Local
```bash
docker compose up -d dynamodb-local
```

### 5. Install and start the backend
```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

### 6. Install and start the frontend (new terminal)
```bash
cd frontend
npm install
npm run dev
```

### 7. Open the app
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8080
- **Health check**: http://localhost:8080/health
- **API docs**: http://localhost:8080/docs

### 8. Start ngrok (for GitHub webhooks — new terminal)
```bash
ngrok http 8080
```
Copy the `https://` URL and update:
- Your `backend/.env` `NGROK_URL=` value
- Your GitHub App's Webhook URL to `<ngrok-url>/webhooks/github`

Then run `direnv allow` to pick up the change.

---

## Testing the Chat GUI

1. Open http://localhost:3000
2. The chat interface should load with a dark theme
3. Click **New Session** in the sidebar
4. Type a message and press Enter
5. The agent will respond via WebSocket with streaming updates

Note: The Chat GUI requires GitHub OAuth login. You need at minimum `ANTHROPIC_API_KEY`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` configured. The full GitHub App setup (App ID, private key, webhook secret) and ngrok are only needed for the GitHub Issue workflow.

## Testing ECS Jobs

Run compute jobs via the agent using ECS Fargate. Requires `VsweVpc`, `VsweStorage`, and `VsweEcs` stacks deployed.

### 1. Fill in `.envrc` with ECS networking config

After deploying, get the subnet and security group IDs:
```bash
# Private subnets
aws ec2 describe-subnets --filters "Name=tag:aws:cloudformation:stack-name,Values=VsweVpc" \
  --query 'Subnets[?MapPublicIpOnLaunch==`false`].SubnetId' --output text

# Default security group
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=tag:Project,Values=vswe" --query 'Vpcs[0].VpcId' --output text)
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=default" \
  --query 'SecurityGroups[0].GroupId' --output text
```

Edit `.envrc`:
```bash
export VSWE_ECS_CLUSTER=vswe-cluster
export VSWE_JOB_TASK_DEF=vswe-job
export VSWE_PRIVATE_SUBNETS=subnet-xxx,subnet-yyy
export VSWE_SECURITY_GROUPS=sg-xxx
```

Then: `direnv allow` and restart the backend.

### 2. Test via Chat GUI

1. Open http://localhost:3000
2. Start a new session
3. Ask the agent: "Create a Python script that prints hello world 5 times with a 10 second delay between each, and run it as a job."
4. The agent will write the script, profile it (detects as simple CPU script), and submit it as an ECS Fargate task
5. Use "check the job status" to monitor progress

### 3. Verify the job ran

```bash
# List recent tasks
aws ecs list-tasks --cluster vswe-cluster --desired-status STOPPED --query 'taskArns[-1]' --output text

# Check exit code (0 = success)
aws ecs describe-tasks --cluster vswe-cluster --tasks <task-arn> \
  --query 'tasks[0].{status:lastStatus,exit:containers[0].exitCode}' --output json
```

---

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
    cdk/stacks/      # VPC, ECS, Storage, Lambda, CDN
  training/          # ML checkpoint library
    vswe_checkpoint/ # Importable by training scripts
```

---

## AWS Infrastructure

### What's deployed where

| Stack | What it creates | Needed for local dev? | Needed for production? |
|-------|----------------|----------------------|----------------------|
| **VsweVpc** | VPC, subnets, fck-nat, EFS security group | Yes (if testing ECS jobs) | Yes |
| **VsweStorage** | DynamoDB tables, EFS, S3 bucket | Yes (if testing ECS jobs) | Yes |
| **VsweLambda** | Lambda webhook handler, API Gateway, SQS | No (local uses FastAPI directly) | Yes |
| **VsweEcs** | Fargate cluster, API + agent + job task definitions | Yes (if testing ECS jobs) | Yes |
| **VsweCdn** | CloudFront + S3 frontend hosting | No (local uses Vite dev server) | Yes |

### Prerequisites

1. **AWS CLI configured** — verify with:
   ```bash
   aws sts get-caller-identity
   ```
   If this errors, run `aws configure` and enter your Access Key ID, Secret Access Key, region (`us-east-1`), and output format (`json`).

2. **AWS CDK CLI installed**:
   ```bash
   npm install -g aws-cdk
   ```

### First-time setup

Create a Python virtual environment for CDK (separate from the backend venv):
```bash
cd infrastructure/cdk
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Bootstrap CDK in your AWS account (one-time):
```bash
cdk bootstrap
```

---

### Local dev deploy (ECS jobs)

For local development, you need VPC + Storage + ECS. The backend API runs on your machine via uvicorn, but compute jobs run on ECS Fargate. You also need the training Docker image in ECR (see "Building and pushing Docker images" below).

```bash
cd infrastructure/cdk && source .venv/bin/activate
cdk deploy VsweVpc VsweStorage VsweEcs
```

Note: The VsweEcs stack will fail to create the API service if the `vswe/api` Docker image isn't in ECR yet. That's fine for local dev — the job task definition will still be created. If you see a circuit breaker error, you can ignore it.

After deploying, fill in `.envrc` with the VPC subnet and security group IDs:
```bash
# Get private subnet IDs
aws ec2 describe-subnets --filters "Name=tag:aws:cloudformation:stack-name,Values=VsweVpc" "Name=tag:aws:cloudformation:logical-id,Values=*Private*" --query 'Subnets[].SubnetId' --output text

# Get the default VPC security group (or the ECS task security group)
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=<vpc-id>" "Name=group-name,Values=default" --query 'SecurityGroups[0].GroupId' --output text
```

Edit `.envrc`:
```bash
export VSWE_ECS_CLUSTER=vswe-cluster
export VSWE_JOB_TASK_DEF=vswe-job
export VSWE_PRIVATE_SUBNETS=subnet-abc123,subnet-def456
export VSWE_SECURITY_GROUPS=sg-abc123
```

Then reload:
```bash
direnv allow
```

---

### Production deploy (full stack)

Production runs the backend on ECS Fargate and the frontend on CloudFront. CDK automatically builds Docker images, pushes them to a managed ECR repository, and wires all infrastructure together.

#### 1. Store secrets in SSM Parameter Store (one-time)

Secrets (API keys, credentials) are stored as a single JSON blob in SSM. Non-secret config (URLs, subnet IDs) is derived automatically by CDK from stack outputs.

Create the secrets before the first deploy:
```bash
python3 scripts/push-secrets-to-ssm.sh
```

This reads `backend/.env.production` and pushes the secrets to SSM at `/vswe/secrets`. To update later: `python3 scripts/push-secrets-to-ssm.sh --overwrite`.

The following are stored in SSM (actual secrets only):
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`
- `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`
- `JWT_SECRET`

The following are set automatically by CDK (no manual config):
- `BACKEND_URL` — derived from ALB DNS name
- `FRONTEND_URL` — derived from CloudFront domain
- `CORS_ORIGINS` — derived from both URLs above
- `VSWE_SQS_QUEUE_URL` — wired from Lambda stack
- `VSWE_ECS_CLUSTER`, `VSWE_JOB_TASK_DEF` — hardcoded in CDK
- `VSWE_PRIVATE_SUBNETS`, `VSWE_SECURITY_GROUPS` — derived from VPC

#### 2. Deploy all stacks
```bash
make deploy
```

CDK builds Docker images from `backend/` and `training/` source directories, pushes them to ECR, and deploys all stacks. First deploy takes ~10-15 minutes.

#### 3. Update GitHub App webhook URL (one-time)

Get the API Gateway URL and update your GitHub App settings:
```bash
aws apigatewayv2 get-apis --query 'Items[?Name==`vswe-webhook-api`].ApiEndpoint' --output text
```

Set the Webhook URL in your GitHub App to: `<api-gateway-url>/webhook`

#### 4. Deploy frontend
```bash
cd frontend && npm run deploy
```

This builds the React app and syncs it to S3 + invalidates the CloudFront cache.

### Subsequent deploys

**Infrastructure or backend code changes:**
```bash
make deploy
```
CDK detects changes, rebuilds Docker images only if source changed, and updates the stacks.

**Frontend-only changes:**
```bash
cd frontend && npm run deploy
```

**Secrets changed (rotated API key, etc.):**
```bash
python3 scripts/push-secrets-to-ssm.sh --overwrite
```
Then restart the ECS service to pick up new secrets:
```bash
aws ecs update-service --cluster vswe-cluster --service <service-name> --force-new-deployment
```

### Tear down
```bash
make destroy
```

### Cost notes
- Most resources are pay-per-use and cost nothing when idle (ECS Fargate, DynamoDB, S3, Lambda, CloudFront)
- The VPC uses **fck-nat** (a t4g.micro EC2 instance) instead of a managed NAT Gateway — ~$3/month idle instead of ~$32/month
- EFS charges ~$0.30/GB/month (empty = $0)
- The only always-on cost when idle is the fck-nat instance (~$3/month)

---

## Architecture

See `CLAUDE.md` for architecture notes and the plan file at `.claude/plans/` for the full design document.

Key points:
- **Lambda** is a thin webhook receiver — all agent logic runs in **ECS Fargate**
- LLM Router, Job Scheduler, and GitHub Client are libraries in the same process, not separate services
- Compute jobs run on **ECS Fargate** (on-demand tasks), decoupled from the agent
- **DynamoDB** for state, **EFS** for workspaces, **S3** for archives
- Infrastructure managed with **AWS CDK** (Python)

---

## Cost

The system is designed to run a full demo for under **$5**, including:
- LLM API calls (Anthropic / OpenAI)
- AWS compute (ECS Fargate tasks)
- Storage (DynamoDB, EFS, S3)

The cost dashboard at `/costs` in the frontend tracks spending in real-time.
