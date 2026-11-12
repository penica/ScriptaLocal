.PHONY: validation-build validation-config validation-gpu0 validation-gpu1 validation-cpu-build validation-cpu contract-export

validation-build:
	docker build -f deploy/validation/Dockerfile -t scriptalocal-validation:initial .

validation-config:
	docker compose -f deploy/validation/compose.yaml --profile second-gpu config --quiet

validation-gpu0:
	docker compose -f deploy/validation/compose.yaml run --rm gpu0

validation-gpu1:
	docker compose -f deploy/validation/compose.yaml --profile second-gpu run --rm gpu1

validation-cpu-build:
	docker build -f deploy/cpu-validation/Dockerfile -t scriptalocal-cpu-validation:locked .

# Run source tests from this checkout, without GPU access or network.
validation-cpu:
	docker run --rm --network none --read-only --memory 2g --cpus 2 --tmpfs /tmp:size=64m -v "$(CURDIR):/app:ro" scriptalocal-cpu-validation:locked

contract-export:
	docker run --rm --network none --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/app" scriptalocal-cpu-validation:locked python -m scripts.export_contract docs/api/openapi.json
