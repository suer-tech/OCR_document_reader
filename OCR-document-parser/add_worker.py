def add_langfuse_worker(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    worker_service = """
  langfuse-worker:
    image: langfuse/langfuse:3
    container_name: ocr-langfuse-worker
    restart: always
    command: ["node", "/app/packages/worker/dist/index.js"]
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
      - REDIS_HOST=langfuse-redis
      - REDIS_PORT=6379
      - REDIS_AUTH=langfuse_redis_pass
    depends_on:
      langfuse-db:
        condition: service_healthy
      clickhouse:
        condition: service_healthy
      langfuse-redis:
        condition: service_healthy

volumes:"""
    content = content.replace('\nvolumes:', worker_service)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

add_langfuse_worker('docker-compose_dev.yml')
print('langfuse-worker added')
