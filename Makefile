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
	cd infrastructure/cdk && cdk deploy --all

destroy:
	cd infrastructure/cdk && cdk destroy --all

tables:
	cd backend && python -m app.db.create_tables
