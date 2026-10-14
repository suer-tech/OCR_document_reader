"""Standalone host collector: fixed read-only queries, sanitized atomic snapshot.

Runs under a systemd timer, never from the remotely accessible SSH account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from admin_ops.hr_protocol import (
    CODES,
    SERVICES,
    STAGES,
    daily_report,
    safe_code,
    safe_stage,
)


def command(args, input_text=None):
    result = subprocess.run(args, input=input_text, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=12, check=True)
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("source response too large")
    return result.stdout


def database_query(summary_hour=19, summary_minute=5):
    if type(summary_hour) is not int or type(summary_minute) is not int or not (0 <= summary_hour < 24 and 0 <= summary_minute < 60):
        raise ValueError("invalid summary cutoff")
    codes = ",".join("'" + code.replace("'", "''") + "'" for code in sorted(CODES))
    stages = ",".join("'" + stage + "'" for stage in sorted(STAGES))
    # No candidate names, IDs, payloads, raw errors, prompts or credentials.
    return f"""BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '5s';
WITH windows AS (
 SELECT day AT TIME ZONE 'Asia/Yekaterinburg' AS start,
  (day + make_interval(hours=>{summary_hour},mins=>{summary_minute})) AT TIME ZONE 'Asia/Yekaterinburg' AS finish
 FROM (SELECT date_trunc('day',now() AT TIME ZONE 'Asia/Yekaterinburg') - i * interval '1 day' AS day
       FROM generate_series(0,1) i) d
), completed AS (
 SELECT w.start,r.state,g.candidate_id,
  CASE WHEN r.state='SUCCEEDED' AND a.started IS NOT NULL AND r.last_progress_at::timestamptz >= a.started
   THEN extract(epoch FROM r.last_progress_at::timestamptz - a.started) END AS seconds
 FROM windows w JOIN agent_runs r ON r.last_progress_at::timestamptz >= w.start
  AND r.last_progress_at::timestamptz < least(w.finish,now()) AND r.state IN ('SUCCEEDED','FAILED','CANCELLED')
 LEFT JOIN agent_goals g ON g.id=r.goal_id
 LEFT JOIN LATERAL (SELECT min(a.started_at::timestamptz) AS started FROM agent_tasks t
   JOIN agent_attempts a ON a.task_id=t.id WHERE t.run_id=r.id) a ON true
), daily_errors AS (
 SELECT w.start,CASE WHEN t.routing_class IN ({stages}) THEN t.routing_class ELSE 'unknown' END AS stage,count(*) AS count
 FROM windows w JOIN agent_attempts a ON a.finished_at::timestamptz >= w.start
  AND a.finished_at::timestamptz < least(w.finish,now()) AND a.state IN ('FAILED','UNKNOWN_OUTCOME')
 JOIN agent_tasks t ON t.id=a.task_id GROUP BY w.start,stage
), safe_attempts AS (
 SELECT a.task_id,a.finished_at,a.attempt_number,t.routing_class,
  CASE WHEN a.error_code IN ({codes}) THEN a.error_code ELSE 'TECHNICAL_ERROR' END AS code
 FROM agent_attempts a JOIN agent_tasks t ON t.id=a.task_id
 WHERE a.state IN ('FAILED','UNKNOWN_OUTCOME') AND a.finished_at IS NOT NULL
  AND a.finished_at::timestamptz >= now()-interval '24 hours'
)
SELECT json_build_object(
 'daily_reports',(SELECT json_agg(json_build_object(
   'start',w.start,'end',w.finish,'as_of',least(w.finish,now()),
   'candidates',(SELECT count(DISTINCT candidate_id) FROM completed WHERE start=w.start),
   'completed_runs',(SELECT count(*) FROM completed WHERE start=w.start),
   'succeeded',(SELECT count(*) FROM completed WHERE start=w.start AND state='SUCCEEDED'),
   'failed',(SELECT count(*) FROM completed WHERE start=w.start AND state='FAILED'),
   'cancelled',(SELECT count(*) FROM completed WHERE start=w.start AND state='CANCELLED'),
   'timed_runs',(SELECT count(seconds) FROM completed WHERE start=w.start),
   'avg_seconds',(SELECT avg(seconds) FROM completed WHERE start=w.start),
   'p95_seconds',(SELECT percentile_disc(0.95) WITHIN GROUP (ORDER BY seconds) FROM completed WHERE start=w.start),
   'failed_attempts',COALESCE((SELECT sum(count) FROM daily_errors WHERE start=w.start),0),
   'retry_attempts',(SELECT count(*) FROM agent_attempts WHERE attempt_number>1
     AND started_at::timestamptz >= w.start AND started_at::timestamptz < least(w.finish,now())),
   'error_stages',COALESCE((SELECT json_agg(json_build_object('stage',stage,'count',count)) FROM daily_errors WHERE start=w.start),'[]'::json),
   'active_runs',(SELECT count(*) FROM agent_runs WHERE state='ACTIVE'),
   'waiting_runs',(SELECT count(*) FROM agent_runs WHERE state='WAITING_FOR_HUMAN'),
   'queued_tasks',(SELECT count(*) FROM agent_tasks WHERE state='RUNNABLE'),
   'running_tasks',(SELECT count(*) FROM agent_tasks WHERE state='RUNNING')
  )) FROM windows w),
 'incidents', COALESCE((SELECT json_agg(x) FROM (
  SELECT * FROM (
   SELECT DISTINCT ON (a.task_id, a.code)
    md5('task:' || a.task_id || ':' || a.code) AS identity,
    'task_failed' AS kind, a.finished_at AS occurred_at,
    a.routing_class AS stage, a.code,
    a.attempt_number AS attempts
   FROM safe_attempts a
   ORDER BY a.task_id,a.code,a.finished_at DESC,a.attempt_number DESC
  ) attempts
  UNION ALL
  SELECT md5('run:' || r.id || ':' || r.last_progress_at), 'run_failed', r.last_progress_at,
   COALESCE(t.routing_class,'unknown'),
   CASE WHEN a.error_code IN ({codes}) THEN a.error_code
    WHEN a.error_code IS NULL THEN 'RUN_FAILED' ELSE 'TECHNICAL_ERROR' END,
   COALESCE(a.attempt_number,0)
  FROM agent_runs r
  LEFT JOIN LATERAL (SELECT task.id,task.routing_class FROM agent_tasks task
    WHERE task.run_id=r.id AND task.state IN ('FAILED','UNKNOWN_OUTCOME')
    ORDER BY (SELECT max(finished_at) FROM agent_attempts WHERE task_id=task.id) DESC NULLS LAST,task.id LIMIT 1) t ON true
  LEFT JOIN LATERAL (SELECT attempt.error_code,attempt.attempt_number FROM agent_attempts attempt
    WHERE attempt.task_id=t.id AND attempt.state IN ('FAILED','UNKNOWN_OUTCOME')
    ORDER BY attempt.finished_at DESC NULLS LAST,attempt.attempt_number DESC LIMIT 1) a ON true
  WHERE r.state='FAILED' AND r.last_progress_at::timestamptz >= now()-interval '24 hours'
  ORDER BY occurred_at DESC LIMIT 1001
 ) x), '[]'::json),
 'active_runs',(SELECT count(*) FROM agent_runs WHERE state='ACTIVE'),
 'failed_runs_24h',(SELECT count(*) FROM agent_runs WHERE state='FAILED'
   AND last_progress_at::timestamptz >= now()-interval '24 hours'),
 'oldest_progress_seconds',COALESCE((SELECT greatest(0,max(extract(epoch FROM now()-last_progress_at::timestamptz)))
   FROM agent_runs WHERE state='ACTIVE'),0)
);
COMMIT;
"""


def collect(project="hh-agent", postgres_container="hh-agent-postgres-1", summary_hour=19, summary_minute=5):
    now = datetime.now(timezone.utc).isoformat()
    snapshot = {"schema_version": 1, "generated_at": now,
                "sources": {"docker": "unavailable", "database": "unavailable", "events": "unavailable"},
                "services": [], "incidents": [], "stats": {}}
    try:
        ids = command(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).split()
        if not ids or len(ids) > 500:
            raise ValueError("missing or excessive containers")
        template = ('{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}|'
                    '{{.State.OOMKilled}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}')
        lines = command(["docker", "inspect", "--format", template, *ids]).splitlines()
        services = {name: {"name": name, "running": 0, "unhealthy": 0, "restarts": 0, "oom": False}
                    for name in sorted(SERVICES)}
        for line in lines:
            name, running, oom, health, restarts = line.split("|")
            if name not in services:
                continue
            item = services[name]
            item["running"] += running == "true"
            item["unhealthy"] += running == "true" and health == "unhealthy"
            item["restarts"] += max(0, int(restarts))
            item["oom"] = item["oom"] or oom == "true"
        snapshot["services"] = list(services.values())
        snapshot["sources"]["docker"] = "ok"
    except (subprocess.SubprocessError, ValueError, OSError):
        pass  # Explicit unavailable source, never a healthy empty result.
    try:
        data = json.loads(command(["docker", "exec", "-i", "-u", "postgres", postgres_container,
                                   "psql", "-X", "-U", "hh_agent", "-d", "hh_agent", "-Atq",
                                   "-v", "ON_ERROR_STOP=1"], database_query(summary_hour, summary_minute)))
        incidents = data.get("incidents", [])
        snapshot["sources"].update(database="ok", events="truncated" if len(incidents) > 1000 else "ok")
        for item in incidents[:1000]:
            if item.get("kind") not in {"task_failed", "run_failed"}:
                continue
            occurred = datetime.fromisoformat(item["occurred_at"].replace("Z", "+00:00"))
            if occurred.tzinfo is None:
                raise ValueError("missing event timezone")
            snapshot["incidents"].append({
                "id": hashlib.sha256(item["identity"].encode()).hexdigest(),
                "kind": item["kind"], "occurred_at": occurred.astimezone(timezone.utc).isoformat(),
                "stage": safe_stage(item.get("stage")), "code": safe_code(item.get("code")),
                "attempts": min(1000000, max(0, int(item.get("attempts") or 0))),
            })
        snapshot["stats"] = {key: max(0, float(data.get(key) or 0)) for key in
                             ("active_runs", "failed_runs_24h", "oldest_progress_seconds")}
        reports = data.get("daily_reports")
        if not isinstance(reports, list) or len(reports) != 2:
            raise ValueError("missing daily aggregates")
        snapshot["daily_reports"] = [daily_report(item) for item in reports]
    except (subprocess.SubprocessError, ValueError, TypeError, KeyError, OSError):
        snapshot["sources"].update(database="unavailable", events="unavailable")
        snapshot["incidents"], snapshot["stats"] = [], {}
        snapshot.pop("daily_reports", None)
    try:
        disk = shutil.disk_usage("/")
        snapshot["host"] = {"disk_free_percent": round(100 * disk.free / disk.total, 1)}
        values = dict(re.findall(r"^(MemTotal|MemAvailable):\s+(\d+)", Path("/proc/meminfo").read_text(), re.MULTILINE))
        if int(values.get("MemTotal", 0)):
            snapshot["host"]["memory_used_percent"] = round(
                100 * (1 - int(values["MemAvailable"]) / int(values["MemTotal"])), 1)
    except (OSError, ValueError, KeyError):
        pass
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="hh-agent")
    parser.add_argument("--postgres-container", default="hh-agent-postgres-1")
    parser.add_argument("--output")
    parser.add_argument("--group")
    parser.add_argument("--summary-hour", type=int, default=19)
    parser.add_argument("--summary-minute", type=int, default=5)
    args = parser.parse_args()
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", value)
               for value in (args.project, args.postgres_container)):
        parser.error("invalid source name")
    body = json.dumps(collect(args.project, args.postgres_container, args.summary_hour, args.summary_minute),
                      ensure_ascii=False, allow_nan=False)
    if len(body.encode()) > 262144:
        raise ValueError("snapshot exceeds boundary limit")
    if not args.output:
        print(body)
        return
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
        if args.group:
            import grp
            os.chown(temp, 0, grp.getgrnam(args.group).gr_gid)
        os.chmod(temp, 0o640)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


if __name__ == "__main__":
    main()
