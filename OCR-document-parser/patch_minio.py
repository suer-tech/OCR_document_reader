import sys

def patch_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip() == 'volumes:':
            # Append minio and minio-init services
            new_lines.append('''  minio:
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

''')
            new_lines.append(line)
        elif 'CLICKHOUSE_CLUSTER_ENABLED=false' in line:
            new_lines.append(line)
            new_lines.append('      - LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse-events\n')
            new_lines.append('      - LANGFUSE_S3_EVENT_UPLOAD_REGION=us-east-1\n')
            new_lines.append('      - LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT=http://minio:9000\n')
            new_lines.append('      - LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=minioadmin\n')
            new_lines.append('      - LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=minioadmin\n')
            new_lines.append('      - LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=true\n')
        elif 'clickhouse-data:' in line:
            new_lines.append(line)
            new_lines.append('  minio-data:\n')
        else:
            new_lines.append(line)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

patch_file('docker-compose_dev.yml')
patch_file('docker-compose.yml')
print('Patched successfully!')
