FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY deploy/contract-validation/requirements.in /requirements.in
COPY deploy/contract-validation/constraints.lock /constraints.lock
RUN python -m pip install --no-cache-dir -c /constraints.lock -r /requirements.in && python -m pip check
WORKDIR /bundle
CMD ["python", "-m", "pytest", "-c", "/dev/null", "-p", "no:cacheprovider", "tests/test_scriptalocal_contract.py", "-q"]
