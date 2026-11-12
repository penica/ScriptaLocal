FROM pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime@sha256:2b59b1b91885677814f78be1f8df48a25d5dc952eb6580eaecfefca510f9afd3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
COPY deploy/validation/requirements.in /validation/requirements.in
COPY deploy/validation/constraints.lock /validation/constraints.lock
RUN python -m pip install --no-cache-dir -r /validation/requirements.in -c /validation/constraints.lock \
    && python -m pip check \
    && python -m pip freeze --all > /validation/resolved-requirements.txt
COPY deploy/worker-validation/requirements.extra /validation/requirements.extra
COPY deploy/worker-validation/constraints.lock /validation/worker-constraints.lock
RUN python -m pip install --no-cache-dir -c /validation/worker-constraints.lock -r /validation/requirements.extra && python -m pip check
WORKDIR /app
COPY scriptalocal /app/scriptalocal
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
USER 1000:1000
CMD ["python","-m","scriptalocal.resident_worker","--config","/settings/worker.json"]
