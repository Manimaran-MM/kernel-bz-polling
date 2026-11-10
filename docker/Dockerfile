# Red Hat UBI base — reachable when Docker Hub is blocked
FROM registry.access.redhat.com/ubi9/python-311:latest

USER root

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Base image already includes git, openssh-clients, curl-minimal,
# ca-certificates, rsync, gcc, and make.
# OpenShift CLI (oc) + gettext (envsubst) required by create-rhel-guest-image.sh
# when provisioning *-custom DataVolumes for unmet baselines.
ARG OC_VERSION=4.16.30
RUN dnf install -y gettext \
    && curl -fsSL -o /tmp/openshift-client-linux.tar.gz \
      "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/${OC_VERSION}/openshift-client-linux.tar.gz" \
    && tar -C /usr/local/bin -xzf /tmp/openshift-client-linux.tar.gz oc kubectl \
    && rm -f /tmp/openshift-client-linux.tar.gz \
    && oc version --client \
    && dnf clean all

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
