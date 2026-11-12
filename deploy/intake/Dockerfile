FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY deploy/intake/requirements.in /requirements.in
COPY deploy/intake/constraints.lock /constraints.lock
RUN python -m pip install --no-cache-dir -r /requirements.in -c /constraints.lock && python -m pip check
WORKDIR /app
COPY scriptalocal /app/scriptalocal
CMD ["python", "-m", "uvicorn", "scriptalocal.intake:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8091", "--workers", "1", "--no-access-log"]
