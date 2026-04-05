.PHONY: dev api worker research web

dev:
	@echo "Start services individually until full dev tooling is wired."

api:
	cd apps/api && uvicorn app.main:app --reload

worker:
	cd services/worker && python -m worker.main

research:
	cd services/research && python -m research.cli

web:
	cd apps/web && pnpm dev
