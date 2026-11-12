.PHONY: validation-build validation-config validation-gpu0 validation-gpu1

validation-build:
	docker build -f deploy/validation/Dockerfile -t scriptalocal-validation:initial .

validation-config:
	docker compose -f deploy/validation/compose.yaml --profile second-gpu config --quiet

validation-gpu0:
	docker compose -f deploy/validation/compose.yaml run --rm gpu0

validation-gpu1:
	docker compose -f deploy/validation/compose.yaml --profile second-gpu run --rm gpu1
