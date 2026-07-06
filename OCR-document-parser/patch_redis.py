import sys

def patch_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip() == 'volumes:':
            if filepath == 'docker-compose_dev.yml' and 'langfuse-db-data:' not in line:
                # only insert if this is the root volumes block which starts at the end of the file.
                # actually, to be safe, we'll find the last volumes block by just doing it string replace!
                pass
            new_lines.append(line)
        elif 'CLICKHOUSE_CLUSTER_ENABLED=false' in line:
            new_lines.append(line)
            new_lines.append('      - REDIS_HOST=langfuse-redis\n')
            new_lines.append('      - REDIS_PORT=6379\n')
            new_lines.append('      - REDIS_AUTH=\n')
        else:
            new_lines.append(line)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

def insert_redis_service(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    redis_service = """
  langfuse-redis:
    image: redis:7-alpine
    container_name: ocr-langfuse-redis
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:"""
    content = content.replace('\\nvolumes:', redis_service)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

patch_file('docker-compose_dev.yml')
insert_redis_service('docker-compose_dev.yml')
print('Patched successfully!')
