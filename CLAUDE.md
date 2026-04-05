# VSWE — Virtual Software Engineer

## Overview
Production-ready Virtual SWE system on AWS supporting:
1. GitHub Issue Workflow — agent listens to issues, asks clarifications, writes code, opens PRs
2. Interactive Chat GUI — React app with WebSocket streaming, multi-model support

## Tech Stack
- **Backend**: Python 3.12, FastAPI, boto3
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Zustand, TanStack Query
- **Infrastructure**: AWS CDK (Python) — ECS Fargate, Lambda, AWS Batch, DynamoDB, EFS, S3, CloudFront
- **LLMs**: Claude Opus (default), Sonnet, Haiku, GPT-4, GPT-4 Turbo
- **Checkpointing**: Custom library for ML training jobs (`training/vswe_checkpoint/`)

## Local Development
```bash
docker-compose up -d          # DynamoDB Local + LocalStack
cd backend && pip install -e ".[dev]"
cd frontend && npm install
```

## Key Commands
```bash
make dev        # Start all local services
make test       # Run backend tests
make deploy     # CDK deploy to AWS
make destroy    # Tear down AWS resources
```

## Architecture Notes
- Lambda is a thin webhook receiver only — all agent logic runs in ECS Fargate
- LLM Router, Job Scheduler, GitHub Client are libraries in the same process, NOT separate services
- ML training jobs run on AWS Batch (Spot EC2), decoupled from the agent
- Checkpoints are model weights/optimizer state, NOT conversation state
- `profile_job()` is deterministic (AST analysis + formulas), not LLM-based

## TODO (Deferred)
- [ ] Dataset size estimation in job profiler (need to determine how to stat files or ask user)
- [ ] Scalability to 10k users (discuss in article, implement later)
- [ ] Agent learning from interactions (discuss in article)
- [ ] Multi-GPU / distributed training support in profiler
- [ ] **Auth**: Current auth uses self-managed JWT with HS256 symmetric secret. Limitations: no token revocation (logout doesn't invalidate server-side), no refresh tokens (hard 24hr expiry), secret rotation logs out all users, no MFA support. For production, migrate to a managed auth provider (AWS Cognito, Auth0, or Firebase Auth) with GitHub as a social identity provider. Challenge: we still need the raw GitHub OAuth access token for repo API calls, so the auth provider must expose the upstream social token.
