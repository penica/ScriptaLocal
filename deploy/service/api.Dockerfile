FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY deploy/api-validation/requirements.in /requirements.in
COPY deploy/api-validation/constraints.lock /constraints.lock
RUN python -m pip install --no-cache-dir -c /constraints.lock -r /requirements.in && python -m pip check
WORKDIR /app
COPY scriptalocal /app/scriptalocal
USER 1000:1000
CMD ["python","-m","uvicorn","scriptalocal.ocr_server:application","--factory","--host","0.0.0.0","--port","8090","--no-access-log","--no-proxy-headers"]
