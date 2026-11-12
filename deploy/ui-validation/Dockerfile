FROM mcr.microsoft.com/playwright/python@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
COPY deploy/ui-validation/constraints.lock /constraints.lock
RUN pip install --no-cache-dir -c /constraints.lock playwright==1.64.1
WORKDIR /app
COPY scripts/validation/manual_ui_browser_probe.py /app/probe.py
CMD ["python","/app/probe.py"]
