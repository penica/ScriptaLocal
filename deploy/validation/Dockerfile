FROM pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime@sha256:2b59b1b91885677814f78be1f8df48a25d5dc952eb6580eaecfefca510f9afd3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
COPY deploy/validation/requirements.in /validation/requirements.in
COPY deploy/validation/constraints.lock /validation/constraints.lock
RUN python -m pip install --no-cache-dir -r /validation/requirements.in -c /validation/constraints.lock \
    && python -m pip check \
    && python -m pip freeze --all > /validation/resolved-requirements.txt
COPY scripts/validation /validation/scripts
CMD ["python", "/validation/scripts/dependencies_probe.py"]
