"""Opt-in real PostgreSQL acceptance for MON-001..003 (synthetic data only).

OPS_HR_MONITOR_TEST_CONSENT=synthetic-only python -m \
  admin_ops.tests.run_hr_incident_postgres_acceptance \
  --ssh-target ocr-deploy@87.228.124.81 --ssh-key <existing deploy key>

Linux CI with local Docker runs the same acceptance without SSH:
OPS_HR_MONITOR_TEST_CONSENT=synthetic-only python -m \
  admin_ops.tests.run_hr_incident_postgres_acceptance --local-docker

Only an isolated disposable container and /tmp package are created. No ports,
production volumes, secrets, Telegram delivery, or production database access.
SSH mode requires an already verified identity and strict host checking.
Local Docker mode requires Linux, python3 and permission to run Docker.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import importlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class Remote:
    def __init__(self, target=None, key=None, *, local_docker=False):
        self.prefix = None if local_docker else [
            "ssh", "-i", key, "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
            "-oConnectTimeout=15", target,
        ]

    def run(self, args, content=None, *, timeout=60):
        command = args if self.prefix is None else self.prefix + [shlex.join(args)]
        result = subprocess.run(command, input=content,
                                text=True, encoding="utf-8", capture_output=True, timeout=timeout, check=False)
        if result.returncode:
            # Do not repeat remote output: diagnostic strings might contain data.
            raise RuntimeError(f"Synthetic acceptance command failed: {args[0]}; exit={result.returncode}")
        return result.stdout


def assert_snapshot(body, *, count):
    assert body["schema_version"] == 1
    assert body["sources"]["database"] == "ok", "Real PostgreSQL query must succeed"
    assert body["sources"]["events"] == "ok"
    assert len(body["incidents"]) == count
    text = json.dumps(body)
    assert "PRIVATE_SYNTHETIC" not in text, "Raw IDs/error/stage leaked across collector boundary"
    for event in body["incidents"]:
        assert set(event) == {"id", "occurred_at", "kind", "stage", "code", "attempts"}
        assert re.fullmatch("[a-f0-9]{64}", event["id"])
        assert datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")).utcoffset() == timedelta(0)


def verify_daily_reports(sql, collect):
    """Exact SQL boundary fixtures for yesterday's completed local window."""
    yesterday = datetime.now(ZoneInfo("Asia/Yekaterinburg")) - timedelta(days=1)
    start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=19, minute=5)

    def ts(seconds):
        return (start + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat()

    # Explicit columns keep incident fixtures compatible with the extended schema.
    sql(f"""
INSERT INTO agent_goals(id,candidate_id) VALUES ('PRIVATE_SYNTHETIC_G1',101),
 ('PRIVATE_SYNTHETIC_G2',102),('PRIVATE_SYNTHETIC_G3',103),('PRIVATE_SYNTHETIC_EXCLUDED',104);
INSERT INTO agent_runs(id,state,last_progress_at,goal_id) VALUES
 ('PRIVATE_SYNTHETIC_D1','SUCCEEDED','{ts(0)}','PRIVATE_SYNTHETIC_G1'),
 ('PRIVATE_SYNTHETIC_D2','SUCCEEDED','{ts(64980)}','PRIVATE_SYNTHETIC_G1'),
 ('PRIVATE_SYNTHETIC_D3','FAILED','{ts(65040)}','PRIVATE_SYNTHETIC_G2'),
 ('PRIVATE_SYNTHETIC_D4','CANCELLED','{ts(65100)}','PRIVATE_SYNTHETIC_G2'),
 ('PRIVATE_SYNTHETIC_D5','SUCCEEDED','{ts(65160)}','PRIVATE_SYNTHETIC_G3'),
 ('PRIVATE_SYNTHETIC_AT_END','SUCCEEDED','{ts(68700)}','PRIVATE_SYNTHETIC_EXCLUDED'),
 ('PRIVATE_SYNTHETIC_BEFORE','SUCCEEDED','{ts(-1)}','PRIVATE_SYNTHETIC_EXCLUDED'),
 ('PRIVATE_SYNTHETIC_ACTIVE','ACTIVE','{ts(1)}','PRIVATE_SYNTHETIC_G1'),
 ('PRIVATE_SYNTHETIC_WAIT','WAITING_FOR_HUMAN','{ts(1)}','PRIVATE_SYNTHETIC_G2');
INSERT INTO agent_tasks(id,run_id,state,routing_class) VALUES
 ('PRIVATE_SYNTHETIC_DT1','PRIVATE_SYNTHETIC_D1','SUCCEEDED','documents'),
 ('PRIVATE_SYNTHETIC_DT2','PRIVATE_SYNTHETIC_D2','SUCCEEDED','documents'),
 ('PRIVATE_SYNTHETIC_DT3','PRIVATE_SYNTHETIC_D3','FAILED','media'),
 ('PRIVATE_SYNTHETIC_DT5','PRIVATE_SYNTHETIC_D5','SUCCEEDED','llm'),
 ('PRIVATE_SYNTHETIC_BOUNDARY','PRIVATE_SYNTHETIC_BEFORE','FAILED','PRIVATE_SYNTHETIC_STAGE'),
 ('PRIVATE_SYNTHETIC_EXACT_END','PRIVATE_SYNTHETIC_AT_END','FAILED','documents'),
 ('PRIVATE_SYNTHETIC_Q1','PRIVATE_SYNTHETIC_ACTIVE','RUNNABLE','llm'),
 ('PRIVATE_SYNTHETIC_Q2','PRIVATE_SYNTHETIC_ACTIVE','RUNNABLE','documents'),
 ('PRIVATE_SYNTHETIC_RUNNING','PRIVATE_SYNTHETIC_ACTIVE','RUNNING','llm'),
 ('PRIVATE_SYNTHETIC_TERMINAL_QUEUE','PRIVATE_SYNTHETIC_D3','RUNNABLE','llm'),
 ('PRIVATE_SYNTHETIC_TERMINAL_RUNNING','PRIVATE_SYNTHETIC_D4','RUNNING','documents');
INSERT INTO agent_attempts(id,task_id,state,finished_at,error_code,attempt_number,started_at) VALUES
 ('PRIVATE_SYNTHETIC_DA1','PRIVATE_SYNTHETIC_DT1','SUCCEEDED','{ts(0)}',NULL,1,'{ts(-60)}'),
 ('PRIVATE_SYNTHETIC_DA21','PRIVATE_SYNTHETIC_DT2','FAILED','{ts(64860)}','HTTP_503',1,'{ts(64800)}'),
 ('PRIVATE_SYNTHETIC_DA22','PRIVATE_SYNTHETIC_DT2','SUCCEEDED','{ts(64980)}',NULL,2,'{ts(64920)}'),
 ('PRIVATE_SYNTHETIC_DA3','PRIVATE_SYNTHETIC_DT3','UNKNOWN_OUTCOME','{ts(65040)}','TIMEOUT',1,'{ts(64980)}'),
 ('PRIVATE_SYNTHETIC_DA5','PRIVATE_SYNTHETIC_DT5','SUCCEEDED','{ts(65160)}',NULL,1,NULL),
 ('PRIVATE_SYNTHETIC_ABOUNDARY','PRIVATE_SYNTHETIC_BOUNDARY','FAILED','{ts(0)}','PRIVATE_SYNTHETIC_ERROR',2,'{ts(0)}'),
 ('PRIVATE_SYNTHETIC_AEND','PRIVATE_SYNTHETIC_EXACT_END','FAILED','{ts(68700)}','HTTP_503',2,'{ts(68700)}');
""")
    body = collect()
    assert body["sources"]["database"] == "ok", "Daily aggregate SQL must execute against real PostgreSQL"
    assert "PRIVATE_SYNTHETIC" not in json.dumps(body)
    assert len(body["daily_reports"]) == 2
    reports = [item for item in body["daily_reports"]
               if datetime.fromisoformat(item["start"].replace("Z", "+00:00")) == start]
    assert len(reports) == 1
    report = reports[0]
    for key, expected in {"candidates": 3, "completed_runs": 5, "succeeded": 3, "failed": 1,
                          "cancelled": 1, "timed_runs": 2, "failed_attempts": 3,
                          "retry_attempts": 2, "active_runs": 1, "waiting_runs": 1,
                          "queued_tasks": 2, "running_tasks": 1}.items():
        assert report[key] == expected, f"Real SQL {key}: expected {expected}, got {report[key]}"
    assert abs(report["avg_seconds"] - 120) < 0.001
    assert abs(report["p95_seconds"] - 180) < 0.001
    assert {item["stage"]: item["count"] for item in report["error_stages"]} == {
        "documents": 1, "media": 1, "unknown": 1}
    assert datetime.fromisoformat(report["end"].replace("Z", "+00:00")) == end
    assert datetime.fromisoformat(report["as_of"].replace("Z", "+00:00")) == end
    today = next(item for item in body["daily_reports"] if item is not report)
    generated = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    today_end = datetime.fromisoformat(today["end"].replace("Z", "+00:00"))
    assert abs((datetime.fromisoformat(today["as_of"].replace("Z", "+00:00")) - min(generated, today_end)).total_seconds()) < 2
    daily = importlib.import_module("admin_ops.hr_daily_summary")
    from admin_ops.config import OpsSettings

    async def read(_):
        return copy.deepcopy(body)

    original_read = daily.read_snapshot
    daily.read_snapshot = read
    try:
        text = asyncio.run(daily.build_summary(OpsSettings(_env_file=None), end, datetime.now(timezone.utc)))
        assert "19:05" in text and "PRIVATE_SYNTHETIC" not in text
        assert re.search(r"кандидат\w*\s*:?\s*3", text.lower())
        assert re.search(r"запуск\w*\s*:?\s*5", text.lower())
    finally:
        daily.read_snapshot = original_read


def main():
    # Match pytest's conftest source layout when invoked without an editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-docker", action="store_true", help="Use local Linux Docker without SSH")
    parser.add_argument("--ssh-target")
    parser.add_argument("--ssh-key")
    args = parser.parse_args()
    if not args.local_docker and not (args.ssh_target and args.ssh_key):
        parser.error("SSH mode requires --ssh-target and --ssh-key; use --local-docker for Linux CI")
    if os.environ.get("OPS_HR_MONITOR_TEST_CONSENT") != "synthetic-only":
        parser.error("Requires OPS_HR_MONITOR_TEST_CONSENT=synthetic-only")
    assert importlib.util.find_spec("admin_ops.hr_snapshot"), "PRODUCT RED: collector absent"
    assert importlib.util.find_spec("admin_ops.hr_incidents"), "PRODUCT RED: monitor absent"

    remote = Remote(args.ssh_target, args.ssh_key, local_docker=args.local_docker)
    identity = "hh-monitor-acceptance-" + uuid.uuid4().hex
    root = "/tmp/" + identity
    package = Path(__file__).resolve().parents[1]
    created = False
    try:
        files = {name: base64.b64encode((package / name).read_bytes()).decode("ascii")
                 for name in ("__init__.py", "hr_snapshot.py", "hr_protocol.py")}
        upload = ("import pathlib,json,base64; p=pathlib.Path(" + repr(root) + ")/'admin_ops'; "
                  "p.mkdir(parents=True,exist_ok=False); "
                  "[(p/name).write_bytes(base64.b64decode(value)) for name,value in json.loads(input()).items()]")
        remote.run(["python3", "-c", upload], json.dumps(files) + "\n")
        remote.run(["docker", "run", "-d", "--name", identity, "--network", "none",
                    "--memory", "192m", "--cpus", "0.5", "--pids-limit", "100",
                    "--tmpfs", "/var/lib/postgresql/data:rw,size=128m",
                    "--label", f"com.docker.compose.project={identity}",
                    "--label", "com.docker.compose.service=postgres",
                    "-e", "POSTGRES_USER=hh_agent", "-e", "POSTGRES_DB=hh_agent",
                    "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:16-alpine"], timeout=120)
        created = True
        for attempt in range(45):
            try:
                remote.run(["docker", "exec", identity, "pg_isready", "-h", "127.0.0.1", "-U", "hh_agent", "-d", "hh_agent"])
                break
            except RuntimeError:
                if attempt == 44:
                    raise
                time.sleep(1)

        def sql(statement):
            return remote.run(["docker", "exec", "-i", identity, "psql", "-X", "-U", "hh_agent",
                               "-d", "hh_agent", "-Atq", "-v", "ON_ERROR_STOP=1"], statement)

        def collect():
            return json.loads(remote.run(["env", f"PYTHONPATH={root}", "python3", "-m", "admin_ops.hr_snapshot",
                                          "--postgres-container", identity, "--project", identity]))

        sql("""
CREATE TABLE agent_goals(id text PRIMARY KEY,candidate_id bigint);
CREATE TABLE agent_runs(id text PRIMARY KEY,state text,last_progress_at text,goal_id text);
CREATE TABLE agent_tasks(id text PRIMARY KEY,run_id text,state text,routing_class text);
CREATE TABLE agent_attempts(id text PRIMARY KEY,task_id text,state text,finished_at text,error_code text,attempt_number int,started_at text);
INSERT INTO agent_runs(id,state,last_progress_at) VALUES ('PRIVATE_SYNTHETIC_RUN','ACTIVE',now()::text),
 ('PRIVATE_SYNTHETIC_UNKNOWN_RUN','ACTIVE',now()::text);
INSERT INTO agent_tasks VALUES
 ('PRIVATE_SYNTHETIC_TASK','PRIVATE_SYNTHETIC_RUN','FAILED','documents'),
 ('PRIVATE_SYNTHETIC_UNKNOWN','PRIVATE_SYNTHETIC_UNKNOWN_RUN','FAILED','PRIVATE_SYNTHETIC_STAGE');
INSERT INTO agent_attempts(id,task_id,state,finished_at,error_code,attempt_number) VALUES
 ('PRIVATE_SYNTHETIC_OLD','PRIVATE_SYNTHETIC_TASK','FAILED',(now()-interval '2 days')::text,'HTTP_503',1);
""")
        baseline = collect()
        assert_snapshot(baseline, count=0)
        module = importlib.import_module("admin_ops.hr_incidents")
        from admin_ops.config import OpsSettings
        current, sends = copy.deepcopy(baseline), []

        async def read(_):
            return copy.deepcopy(current)

        async def send(recipient, text):
            sends.append((recipient, text))

        original_read = module.read_snapshot
        module.read_snapshot = read
        try:
            with tempfile.TemporaryDirectory(prefix="hh-monitor-acceptance-") as local_state:
                config = OpsSettings(_env_file=None, admin_ids="123", hr_monitor_host="synthetic.invalid",
                                     state_path=str(Path(local_state) / "outbox.sqlite3"))
                asyncio.run(module.HrIncidentMonitor(config, send).tick(datetime.now(timezone.utc)))
                sends.clear()  # Missing production services in this isolated project are expected.
                time.sleep(0.1)
                sql("""
INSERT INTO agent_attempts(id,task_id,state,finished_at,error_code,attempt_number) VALUES
 ('PRIVATE_SYNTHETIC_A1','PRIVATE_SYNTHETIC_TASK','FAILED',now()::text,'HTTP_503',1),
 ('PRIVATE_SYNTHETIC_A2','PRIVATE_SYNTHETIC_TASK','FAILED',now()::text,'HTTP_503',2),
 ('PRIVATE_SYNTHETIC_U1','PRIVATE_SYNTHETIC_UNKNOWN','FAILED',now()::text,'PRIVATE_SYNTHETIC_ERROR_1',1),
 ('PRIVATE_SYNTHETIC_U2','PRIVATE_SYNTHETIC_UNKNOWN','FAILED',now()::text,'PRIVATE_SYNTHETIC_ERROR_2',2);
UPDATE agent_runs SET state='FAILED',last_progress_at=now()::text;
""")
                current = collect()
                assert_snapshot(current, count=4)  # two deduplicated tasks and two terminal runs
                known = [event for event in current["incidents"] if event["kind"] == "task_failed" and event["code"] == "HTTP_503"]
                assert len(known) == 1 and known[0]["attempts"] == 2
                unknown = [event for event in current["incidents"] if event["kind"] == "task_failed" and event["code"] == "TECHNICAL_ERROR"]
                assert len(unknown) == 1 and unknown[0]["stage"] == "unknown"
                terminal_unknown = [event for event in current["incidents"] if event["kind"] == "run_failed" and event["code"] == "TECHNICAL_ERROR"]
                assert len(terminal_unknown) == 1 and terminal_unknown[0]["stage"] == "unknown"
                asyncio.run(module.HrIncidentMonitor(config, send).tick(datetime.now(timezone.utc)))
                asyncio.run(module.HrIncidentMonitor(config, send).tick(datetime.now(timezone.utc)))
                assert len(sends) == 4, "Real DB incidents must reach delivery boundary once after restart"
                assert "PRIVATE_SYNTHETIC" not in json.dumps(sends)
                stable = collect()
                assert {event["id"] for event in stable["incidents"]} == {event["id"] for event in current["incidents"]}
                assert sql("SELECT count(*) FROM agent_attempts;").strip() == "5", "Collector must not mutate database"
                verify_daily_reports(sql, collect)
                sql("""
INSERT INTO agent_tasks SELECT 'synthetic-bulk-'||i,'PRIVATE_SYNTHETIC_RUN','FAILED','llm' FROM generate_series(1,1005) i;
INSERT INTO agent_attempts(id,task_id,state,finished_at,error_code,attempt_number) SELECT 'synthetic-bulk-attempt-'||i,'synthetic-bulk-'||i,'FAILED',now()::text,'TIMEOUT',1 FROM generate_series(1,1005) i;
""")
                truncated = collect()
                assert truncated["sources"]["events"] == "truncated"
                assert len(truncated["incidents"]) <= 1000
                remote.run(["docker", "rm", "-f", identity])
                created = False
                unavailable = collect()
                assert unavailable["sources"]["database"] == "unavailable"
                assert unavailable["sources"]["events"] == "unavailable"
                assert unavailable["incidents"] == []
        finally:
            module.read_snapshot = original_read
        print(json.dumps({"status": "PASSED", "scope": "synthetic PostgreSQL16 collector + durable monitor delivery boundary",
                          "checks": ["real_sql", "no_raw_data", "task_safe_code_dedupe", "terminal_failure", "restart_dedupe",
                                     "readonly", "truncation", "database_unavailable", "daily_real_sql_aggregates",
                                     "daily_window_boundaries", "daily_timing_coverage", "daily_safe_rendering"], "telegram_sent": False,
                          "production_data_accessed": False}))
    finally:
        if created:
            remote.run(["docker", "rm", "-f", identity])
        # Exact generated /tmp directory, no dynamic discovery or shared paths.
        remote.run(["python3", "-c", "import pathlib,shutil; p=pathlib.Path(" + repr(root) + "); "
                    "assert p.parent==pathlib.Path('/tmp') and p.name.startswith('hh-monitor-acceptance-'); "
                    "shutil.rmtree(p,ignore_errors=True)"])


if __name__ == "__main__":
    main()
