.PHONY: dev test deploy destroy lint format

dev:
	docker-compose up -d dynamodb-local
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8080 &
	cd frontend && npm run dev &

dev-docker:
	docker-compose up --build

test:
	cd backend && python -m pytest tests/ -v

lint:
	cd backend && ruff check app/ tests/
	cd frontend && npm run lint

format:
	cd backend && ruff format app/ tests/
	cd frontend && npm run format

deploy:
	cd infrastructure/cdk && source .venv/bin/activate && pip install -q -r requirements.txt && cdk deploy --all
	@echo "Waiting for ECS deployment to stabilize..."
	@SERVICE=$$(aws ecs list-services --cluster vswe-cluster --query 'serviceArns[0]' --output text 2>/dev/null) && \
	if [ -n "$$SERVICE" ] && [ "$$SERVICE" != "None" ]; then \
		while [ "$$(aws ecs describe-services --cluster vswe-cluster --services $$SERVICE --query 'services[0].deployments | length(@)' --output text 2>/dev/null)" != "1" ]; do \
			echo "  ECS rollout in progress..."; \
			sleep 10; \
		done; \
		echo "ECS deployment complete — safe to test."; \
	fi

destroy:
	cd infrastructure/cdk && cdk destroy --all

tables:
	cd backend && python -m app.db.create_tables
