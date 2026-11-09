# One image for every Fault Lab Python service; the compose command selects
# which service runs. Dependencies are installed from the hashed lockfile.
FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY fault_lab ./fault_lab
RUN useradd --create-home --uid 10001 lab && mkdir -p /data && chown lab /data
USER lab
