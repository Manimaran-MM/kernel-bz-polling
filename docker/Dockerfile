# Red Hat UBI base — reachable when Docker Hub is blocked
FROM registry.access.redhat.com/ubi9/python-311:latest

USER root

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Base image already includes git, openssh-clients, curl-minimal,
# ca-certificates, rsync, gcc, and make.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

RUN mkdir -p /app/data/logs/kernel-logs \
             /app/data/jira_cache \
             /app/data/generated_configs \
             /root/.ssh \
    && chmod 700 /root/.ssh

COPY docker/patches/ ./docker/patches/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-m", "src.main", "--yes"]
