FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg unixodbc-dev gcc \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/12/prod.list \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Poetry, export requirements, install into a venv
RUN pip install --no-cache-dir poetry==1.8.3

COPY pyproject.toml poetry.lock ./

# Export prod deps to requirements.txt — avoids shipping Poetry into the image
RUN poetry export -f requirements.txt --without-hashes --only main -o requirements.txt \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim AS runtime

ARG BUILD_DATE
ARG GIT_SHA
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.title="AMR Surveillance API"

# ODBC runtime only (no compiler, no headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg unixodbc \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/12/prod.list \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get purge -y --auto-remove curl gnupg

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Non-root user
RUN useradd --no-create-home --shell /bin/false amr
WORKDIR /app
COPY --chown=amr:amr . .

USER amr

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_ENV=production

EXPOSE 8000


CMD ["python", "-m", "gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]