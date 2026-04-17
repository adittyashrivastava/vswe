# VSWE — Virtual Software Engineer

## Overview
Production-ready Virtual SWE system on AWS supporting:
1. GitHub Issue Workflow — agent listens to issues, asks clarifications, writes code, opens PRs
2. Interactive Chat GUI — React app with WebSocket streaming, multi-model support

## Tech Stack
- **Backend**: Python 3.12, FastAPI, boto3
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Zustand, TanStack Query
- **Infrastructure**: AWS CDK (Python) — ECS Fargate, Lambda, DynamoDB, EFS, S3, CloudFront
- **LLMs**: Claude Opus (default), Sonnet, Haiku, GPT-4, GPT-4 Turbo
- **Checkpointing**: Custom library for ML training jobs (`training/vswe_checkpoint/`)

## Local Development
```bash
docker-compose up -d          # DynamoDB Local
cd backend && pip install -e ".[dev]"
cd frontend && npm install
direnv allow                  # Loads ECS job config from .envrc
```

## Key Commands
```bash
make dev        # Start all local services
make test       # Run backend tests
make deploy     # CDK deploy to AWS (builds + pushes Docker images automatically)
make destroy    # Tear down AWS resources
cd frontend && npm run deploy  # Deploy frontend to CloudFront
```

## Architecture Notes
- **CloudFront** is the single entry point — serves frontend static files AND proxies `/api/*` and `/ws/*` to the ALB. No mixed content issues.
- **ALB** sits behind CloudFront, routes to the ECS API service
- **ECS API service** runs FastAPI (serves API + consumes SQS in same process)
- **Lambda** is a thin webhook receiver — validates signature, enqueues to SQS
- **SQS** decouples webhook delivery from agent processing in production
- LLM Router, Job Scheduler, GitHub Client are libraries in the same process, NOT separate services
- Compute jobs run on **ECS Fargate** (on-demand tasks), decoupled from the agent
- The training container auto-detects and installs script dependencies at runtime via `vswe_checkpoint.runner`
- Checkpoints are model weights/optimizer state, NOT conversation state
- `profile_job()` is deterministic (AST analysis + formulas), not LLM-based
- **WebSocket heartbeat** every 25s keeps connections alive through CloudFront (60s idle timeout)

## Agent Workflow
- Both Chat GUI and GitHub Issues follow: **CLARIFY → PLAN_REVIEW → EXECUTE**
- Agent must ask clarifying questions before submitting a plan (enforced by tool gating)
- `submit_plan` tool transitions to PLAN_REVIEW; user approval transitions to EXECUTE
- GitHub issue plan approval classified by Haiku via tool calls (`approve_plan` / `request_changes`)
- Chat GUI uses deterministic `[PLAN_APPROVED]` signal from frontend button
- Session state: `ACTIVE` (agent running) or `INACTIVE` (idle, accepts new messages)

## Prompt Caching Strategy
- Cache breakpoint on system prompt (always) + 2nd-to-last user message (sliding)
- Compaction only modifies messages AFTER the cache breakpoint to preserve cached prefix
- Tool results: age 1-2 → truncated if >2000 chars; age 3+ → replaced with one-liner
- Messages before the cache breakpoint are never mutated

## Event Consumer Architecture
- `GitHubEventConsumer` base class owns all business logic (session creation, agent execution, plan classification, comment posting)
- `LocalEventConsumer` — FastAPI route adapter for local dev (ngrok)
- `CloudEventConsumer` — SQS polling loop for production (ECS Fargate)
- `github_handler.py` — Lambda thin forwarder (validate + enqueue, separate deployment)

## Secrets Management
- Production secrets stored in SSM Parameter Store as single JSON blob at `/vswe/secrets`
- `config.py` unpacks `VSWE_SECRETS` env var into individual settings at startup
- Non-secret config (URLs, subnet IDs, queue URLs) set as plain CDK environment variables
- `FRONTEND_URL`, `BACKEND_URL`, `CORS_ORIGINS` stored in SSM (set after first deploy when CloudFront domain is known)
- `scripts/push-secrets-to-ssm.sh` reads `backend/.env.production` and pushes to SSM

## TODO (Deferred)
- [ ] Dataset size estimation in job profiler (need to determine how to stat files or ask user)
- [ ] Scalability to 10k users (discuss in article, implement later)
- [ ] Agent learning from interactions (discuss in article)
- [ ] GPU support for training jobs (currently CPU-only on Fargate)
- [ ] **Cache-aware compaction on TTL expiry**: Currently, old tool results in the cached prefix are never compacted (to preserve cache hits). When the cache TTL expires (~5 min idle), we should detect this and run aggressive compaction on the full message history — the cache is already gone so there's nothing to protect. This reduces prompt size and cache creation cost on the next call. Implementation: track `_last_llm_call_time` in the orchestrator, pass `cache_is_cold=True` to `compact_tool_results()` when TTL has elapsed, skip the cache boundary check in that case.
- [ ] **Message queuing while agent is active**: Comments/messages sent while the agent is running are silently dropped in both GitHub issue and chat GUI flows. The session state is `ACTIVE` so incoming events are ignored to prevent race conditions. Fix: queue incoming messages and process them after the current `run()` completes, so no user input is lost.
- [ ] **Cost efficiency**: The ECS API server runs 24/7 (~$15-20/month idle). Consider migrating to AWS App Runner which supports scale-to-zero (~$7/month idle, 10-20s cold start). App Runner handles load balancing, HTTPS, and auto-scaling automatically. Trade-off: cold start latency on first request after idle period. Requires a VPC Connector for EFS access.
- [ ] **Auth**: Current auth uses self-managed JWT with HS256 symmetric secret. Limitations: no token revocation (logout doesn't invalidate server-side), no refresh tokens (hard 24hr expiry), secret rotation logs out all users, no MFA support. For production, migrate to a managed auth provider (AWS Cognito, Auth0, or Firebase Auth) with GitHub as a social identity provider. Challenge: we still need the raw GitHub OAuth access token for repo API calls, so the auth provider must expose the upstream social token.
- [ ] **Dead code cleanup**: `backend/app/webhooks/github_handler.py` appears unused — CDK deploys `backend/lambda/webhook/handler.py` as the Lambda instead. Verify and remove if confirmed dead.
- [ ] **Job completion notifications**: No event-driven notification when ECS jobs finish. User must manually ask agent to check status. Fix: EventBridge rule on ECS task state changes → Lambda → update DynamoDB + push WebSocket notification to frontend.
- [ ] **Local ECS job testing**: Jobs submitted locally can't run because the workspace is on the local filesystem, not EFS. The Fargate container can't access local files. Jobs only work end-to-end when the API server runs on ECS (shared EFS).
