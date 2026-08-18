FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# openssh-client + xray: the head SSHs to nodes during provisioning, and runs
# xray itself to generate Reality keys and to tunnel control calls when its
# direct path to a node is blocked.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip openssh-client ca-certificates \
    && curl -fsSL https://github.com/Gozargah/Marzban-scripts/raw/master/install_latest_xray.sh | bash \
    && apt-get purge -y unzip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Code before install: an editable install run against a bare pyproject.toml
# finds no packages, and `app` then stays unimportable even after the source
# lands — it only appears to work because the working directory happens to be
# on sys.path.
COPY head/ /app/
RUN pip install --no-cache-dir .
# provision_node.py runs bootstrap_node.sh on the node; the admin panel reads
# the same script from this path.
COPY provisioning/ /provisioning/

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
