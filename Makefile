.PHONY: demo demo-up demo-down test

demo-up:
	PROOFRAIL_ADMIN_KEY="change-me" \
	PROOFRAIL_SIGNING_SECRET="dev-signing-secret" \
	PROOFRAIL_SIGNING_KEYS="k1:dev-signing-secret" \
	PROOFRAIL_SIGNING_KEY_CURRENT="k1" \
	PROOFRAIL_DEMO_MODE="1" \
	docker compose up -d --build

demo:
	./scripts/demo_investor.sh

demo-down:
	docker compose down -v

demo-reset: demo-down demo-up

test:
	.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m unittest discover -s tests -p "test_*.py" -q

