import requests
import json

url = 'http://localhost:3001/api/public/traces'
auth = ('pk-lf-cce7c0f5-2c72-4d01-a7b9-4f021071089b', 'sk-lf-3ceac515-365b-44eb-b423-c9a2ab15f279')

r = requests.get(url, auth=auth)
print('Status:', r.status_code)
data = r.json()
traces = data.get('data', [])
if traces:
    print('Traces found:', len(traces))
    for t in traces[:3]:
        print(f"Trace ID: {t.get('id')}, Name: {t.get('name')}")
else:
    print('No traces found:', data)
