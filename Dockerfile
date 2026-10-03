FROM python:3.12-slim

# System libraries the mssql-python driver needs on Linux
RUN apt-get update && \
    apt-get install -y --no-install-recommends libltdl7 libkrb5-3 libgssapi-krb5-2 && \
    rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user, not root
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app && \
    install -d --owner app --group app /app

WORKDIR /app

# Dependencies first, so this slow layer is cached until requirements.txt changes
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Application code (.env is never copied in; settings arrive as environment variables)
COPY --chown=app:app agent.py tools.py api_server.py host.py ./
COPY --chown=app:app mock_api ./mock_api

USER app

# Foundry can override PORT at runtime; 8088 is the default for hosted agents
ENV PORT=8088 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8088

CMD ["python", "host.py"]
