FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY deploy/cpu-validation/requirements.in /validation/requirements.in
COPY deploy/cpu-validation/constraints.lock /validation/constraints.lock
RUN python -m pip install --no-cache-dir -r /validation/requirements.in -c /validation/constraints.lock && python -m pip check
WORKDIR /app
COPY scriptalocal /app/scriptalocal
COPY tests /app/tests
COPY docs/api/examples /app/docs/api/examples
COPY scripts/export_contract.py /app/scripts/export_contract.py
COPY scripts/validation/export_manuscript_result.py /app/scripts/validation/export_manuscript_result.py
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
