import re

def patch(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Append new services before the main volumes block
    if filepath == 'docker-compose_dev.yml':
        volumes_str = "\nvolumes:\n  app-data:"
    else:
        volumes_str = "\nvolumes:\n  app-data:"
        
    services_to_insert = """
  minio:
    image: minio/minio
    container_name: ocr-minio
    command: server /data
    environment:
      - MINIO_ROOT_USER=minioadmin
      - MINIO_ROOT_PASSWORD=minioadmin
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 5s
      retries: 5

  minio-init:
    image: minio/mc
    container_name: ocr-minio-init
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      /usr/bin/mc alias set myminio http://minio:9000 minioadmin minioadmin;
      /usr/bin/mc mb myminio/langfuse-events || true;
      exit 0;
      "

  langfuse:
    image: langfuse/langfuse:3
    container_name: ocr-langfuse
    restart: always
    ports:
      - "3001:3000"
    environment:
      - DATABASE_URL=postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      - NEXTAUTH_SECRET=mysecret_random_string_123
      - SALT=mysalt_random_string_123
      - NEXTAUTH_URL=http://localhost:3001
      - TELEMETRY_ENABLED=false
      - CLICKHOUSE_URL=http://clickhouse:8123
      - CLICKHOUSE_MIGRATION_URL=clickhouse://clickhouse:9000
      - CLICKHOUSE_USER=langfuse
      - CLICKHOUSE_PASSWORD=langfuse
      - CLICKHOUSE_CLUSTER_ENABLED=false
      - LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse-events
      - LANGFUSE_S3_EVENT_UPLOAD_REGION=us-east-1
      - LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT=http://minio:9000
      - LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=minioadmin
      - LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=minioadmin
      - LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=true
    depends_on:
      langfuse-db:
        condition: service_healthy
      clickhouse:
        condition: service_healthy
      minio-init:
        condition: service_completed_successfully

  langfuse-db:
    image: postgres:15-alpine
    container_name: ocr-langfuse-db
    restart: always
    environment:
      - POSTGRES_USER=langfuse
      - POSTGRES_PASSWORD=langfuse
      - POSTGRES_DB=langfuse
    volumes:
      - langfuse-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse -d langfuse"]
      interval: 10s
      timeout: 5s
      retries: 5

  clickhouse:
    image: clickhouse/clickhouse-server:23.8-alpine
    container_name: ocr-clickhouse
    restart: always
    environment:
      - CLICKHOUSE_USER=langfuse
      - CLICKHOUSE_PASSWORD=langfuse
      - CLICKHOUSE_DB=langfuse
    volumes:
      - clickhouse-data:/var/lib/clickhouse
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:8123/ping"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  app-data:"""

    content = content.replace(volumes_str, services_to_insert)

    # Append volumes to the bottom
    content += "\n  langfuse-db-data:\n  clickhouse-data:\n  minio-data:\n"

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

patch('docker-compose_dev.yml')
patch('docker-compose.yml')
