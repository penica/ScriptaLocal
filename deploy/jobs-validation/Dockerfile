FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY deploy/jobs-validation/requirements.in /requirements.in
COPY deploy/jobs-validation/constraints.lock /constraints.lock
RUN python -m pip install --no-cache-dir -c /constraints.lock -r /requirements.in && python -m pip check
WORKDIR /app
COPY scriptalocal /app/scriptalocal
COPY integration_tests /app/integration_tests
COPY docs/api/examples /app/docs/api/examples
CMD ["python", "-m", "unittest", "discover", "-s", "integration_tests", "-p", "test_jobs.py", "-v"]
