"""Full Experiment Rep1 executor (runner v0.2.2).

Reads P1's FROZEN per-run execution manifest (one row per run, already expanded
to one row per source LLM, each with its own canonical run_id) and runs each row
EXACTLY ONCE, in manifest order. The manifest run_id is preserved verbatim in
every output record. A technical retry keeps the same run_id and only increments
attempt_id; every attempt's evidence (raw record, eval record, snapshots) is kept
separately and never overwritten.

This driver changes nothing about model behaviour: prompts, tools, scenario,
environment init, injection vectors/payloads, model mapping, temperature/seed/
context/timeout/max-steps, request/tool-call parsing, retry semantics and the
evaluator are all as in v0.2.1. It only (a) reads the frozen manifest, (b)
dispatches one run per row to that row's model, (c) preserves run_id, and
(d) guards the runtime: Ollama version, model digest and served context are
re-checked BEFORE and AFTER every run, and the batch STOPS (exit code 3) on any
change. A run whose post-check fails is quarantined, not added to the dataset.

Isolated Ollama instance (approved by P1): the batch talks only to the instance named by
OLLAMA_BASE_URL (e.g. http://127.0.0.1:11435/v1); OLLAMA_HOST must name the same host:port
so the CLI and the runner agree. The owner's separate instance (--other-ollama, default
127.0.0.1:11434) is only READ (never stopped/changed): if it holds a model in memory, or one of our
models is not fully on the GPU, the batch STOPS. GPU/residency is logged to resource_log.jsonl.

Usage (from Z:\\p2, venv active, env vars set as in the setup steps):
  python run_full.py --handoff <dir> --expected-endpoint http://127.0.0.1:11435 --gate-only
  python run_full.py --handoff <dir> --expected-endpoint http://127.0.0.1:11435 --dry-run
  python run_full.py --handoff <dir> --expected-endpoint http://127.0.0.1:11435 --out output_full
Safety nets (driver only, no effect on model behaviour): lock file per output folder, free-disk check,
--chunk-size / checkpoints, STOP after N consecutive technical failures, at most 4 attempts per run, an
append-only attempt_ledger.jsonl (accepted attempt is explicit), per-attempt schema/contract validation
(validate_outputs.py; breaks -> quarantine + exit 6). Exit codes: 0 ok, 2 gate/arguments, 3 runtime changed,
4 disk/lock, 5 failures/exhausted attempts, 6 contract break, 130 interrupted.
For a re-frozen manifest pass --manifest-rel / --expected-manifest-sha (and
--expected-payload-sha if the payload file changed).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from p2runner import RUNNER_VERSION
from p2runner.scenarios import Case, sha256_text

# Frozen v1.0 artifact hashes (defaults). Override via CLI for a re-frozen manifest (v1.1).
DEFAULT_MANIFEST_REL = "core/full_experiment_execution_manifest_v1.0.csv"
DEFAULT_PAYLOAD_REL = "core/full_experiment_fixed_ipi_payloads_v1.0.json"
DEFAULT_MANIFEST_SHA = "3135d4e5a18d2bc72b897a239b87c4f1d156f97cfdae48a9a5e1e30a6bc921cf"
DEFAULT_PAYLOAD_SHA = "f16129d0234bbc9ccf4566ad8a085745a9c5ab51243d5a87efcdb4232a9723c7"
DEFAULT_OLLAMA = "0.35.0"
EXPECTED_CONTEXT = 16384

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OTHER_OLLAMA = "http://127.0.0.1:11434"   # the owner's separate Ollama instance (must stay idle)
RERUN_STATUSES = {"model_api_error", "timeout", "runner_error", "interrupted"}
EXIT_STOP = 2     # gate failure before the batch
EXIT_RUNTIME = 3  # runtime change detected during the batch
EXIT_RESOURCE = 4  # disk space / lock / environment problem (nothing wrong with the science)
EXIT_FAILURES = 5  # too many consecutive technical failures: stop for review
EXIT_SCHEMA = 6    # an attempt's record breaks the manifest/data contract (see validate_outputs.py)
EXIT_INTERRUPT = 130
MAX_RUN_ATTEMPTS = 4   # data contract: retry limit at most 4 attempts per intended run

# Expected per-row frozen generation config (everything that touches model behaviour).
EXPECTED_ROW_CONFIG = {
    "temperature": "0.0", "seed": "42", "parallel_tool_calls": "false",
    "thinking_override": "omitted", "top_p": "omitted_default", "top_k": "omitted_default",
    "max_output_tokens": "omitted_default", "context_length": "16384",
    "max_agent_steps": "15", "timeout_seconds": "180",
}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ollama_host() -> str:
    """Native API root of the Ollama instance THIS run uses, derived from OLLAMA_BASE_URL
    (the same variable the adapter reads), e.g. http://127.0.0.1:11435."""
    base = os.getenv("OLLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _hostport(url_or_hostport: str) -> str:
    """Normalise 'http://localhost:11435/v1' / '127.0.0.1:11435' -> '127.0.0.1:11435'."""
    s = re.sub(r"^https?://", "", url_or_hostport.strip()).split("/")[0]
    host, _, port = s.partition(":")
    host = "127.0.0.1" if host in ("localhost", "") else host
    return f"{host}:{port or '11434'}"


def _get(path: str, host: str | None = None) -> dict:
    with urllib.request.urlopen((host or ollama_host()) + path, timeout=30) as r:
        return json.loads(r.read().decode())


def _norm_digest(d) -> str:
    return (d or "").split("sha256:")[-1]


def runner_source_sha() -> str:
    """Same fingerprint the runner records (sha256 over p2runner/*.py bytes)."""
    import p2runner
    h = hashlib.sha256()
    for p in sorted(Path(p2runner.__file__).parent.glob("*.py")):
        h.update(p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ manifest / payloads
def read_rows(manifest: Path) -> list[dict]:
    with manifest.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def payload_index(payloads: Path) -> dict[str, dict]:
    data = json.loads(payloads.read_text(encoding="utf-8"))
    return {e["pair_id"]: e for e in data}


def parse_vectors(raw: str) -> list[str]:
    return [v.strip() for v in (raw or "").replace(";", ",").replace("|", ",").split(",") if v.strip()]


def verify_frozen_hashes(handoff: Path, manifest_rel=DEFAULT_MANIFEST_REL, payload_rel=DEFAULT_PAYLOAD_REL,
                         exp_manifest=DEFAULT_MANIFEST_SHA, exp_payload=DEFAULT_PAYLOAD_SHA) -> list[str]:
    problems = []
    m, p = handoff / manifest_rel, handoff / payload_rel
    if not m.exists():
        return [f"manifest not found: {m}"]
    if not p.exists():
        return [f"payloads not found: {p}"]
    if sha256_file(m) != exp_manifest:
        problems.append("execution manifest SHA256 mismatch")
    if sha256_file(p) != exp_payload:
        problems.append("payload JSON SHA256 mismatch")
    return problems


def assert_frozen_config(rows: list[dict], expected_ollama=DEFAULT_OLLAMA) -> list[str]:
    """Every row must carry the frozen config; runner_version must match THIS code."""
    problems = []
    for r in rows:
        for k, v in EXPECTED_ROW_CONFIG.items():
            if (r.get(k) or "").strip() != v:
                problems.append(f"{r['run_id']}: {k}={r.get(k)!r} expected {v!r}")
        if (r.get("runner_version") or "").strip() != RUNNER_VERSION:
            problems.append(f"{r['run_id']}: manifest runner_version={r.get('runner_version')!r} "
                            f"!= code {RUNNER_VERSION!r} (needs P1 re-freeze to {RUNNER_VERSION})")
        if (r.get("ollama_version") or "").strip() != expected_ollama:
            problems.append(f"{r['run_id']}: manifest ollama_version={r.get('ollama_version')!r} "
                            f"!= expected {expected_ollama!r}")
    return problems


def build_case(row: dict, payloads: dict[str, dict]) -> Case:
    cond = (row["condition"] or "").strip()
    group = row["scenario_group_id"].strip()
    suite = row["suite"].strip()
    ut = row["user_task_id"].strip()
    it = (row.get("injection_task_id") or "").strip() or None
    vectors = parse_vectors(row.get("injection_vector_ids", ""))
    case = Case(f"{group}__{cond}", group, suite, ut, it, cond, vectors,
                split=(row.get("split") or None), manifest_source="full_experiment_execution_manifest")
    if cond == "ipi":
        key = row["payload_lookup_key"].strip()
        e = payloads.get(key)
        if e is None:
            raise KeyError(f"payload_lookup_key not found in frozen payloads: {key}")
        text = e["payload"]
        actual = sha256_text(text)
        if not (row["payload_sha256"] == e["payload_sha256"] == actual):
            raise ValueError(f"payload SHA mismatch for {key}: row={row['payload_sha256']} "
                             f"entry={e['payload_sha256']} recomputed={actual}")
        if not vectors:
            raise ValueError(f"ipi row has no injection vector: {row['run_id']}")
        case.payload, case.payload_sha256 = text, actual
        case.attack_template_id = (row.get("attack_template") or None)
    return case


# ------------------------------------------------------------------ runtime guard
def fresh_identity(model: str) -> dict:
    """Uncached model identity (resolved name, digest, server version). Clearing the
    cache first guarantees each run record reflects the runtime at that moment."""
    from p2runner.adapters import OllamaAdapter, _OLLAMA_IDENTITY_CACHE
    _OLLAMA_IDENTITY_CACHE.clear()
    return OllamaAdapter(model).runtime_identity()


def identity_problems(row: dict, ident: dict, expected_ollama: str) -> list[str]:
    p = []
    if ident.get("identity_error"):
        p.append(f"cannot read runtime identity: {ident['identity_error']}")
    if ident.get("ollama_version") != expected_ollama:
        p.append(f"Ollama server version {ident.get('ollama_version')!r} != {expected_ollama!r}")
    if _norm_digest(ident.get("model_digest")) != _norm_digest(row["expected_model_digest"]):
        p.append(f"{row['requested_model_name']}: digest {ident.get('model_digest')!r} "
                 f"!= expected {row['expected_model_digest']!r}")
    if ident.get("resolved_model") != row["expected_resolved_model_name"]:
        p.append(f"resolved model {ident.get('resolved_model')!r} != {row['expected_resolved_model_name']!r}")
    return p


def served_context(model: str):
    """(context_length, residency) of the loaded model from /api/ps; (None, None) if not loaded."""
    base = model.split(":")[0]
    try:
        for m in _get("/api/ps").get("models", []):
            n = m.get("name", "")
            if n == model or n.split(":")[0] == base:
                size, vram = m.get("size", 0), m.get("size_vram", 0)
                where = "100% GPU" if vram and vram >= size else ("partial CPU" if vram else "CPU only")
                return m.get("context_length"), where
    except Exception:
        pass
    return None, None


def cli_version_problems(expected: str) -> tuple[list[str], str]:
    """`ollama --version` (client) must match; a client/server mismatch prints extra versions."""
    try:
        out = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return ["`ollama` CLI not found on PATH (cannot verify client version)"], ""
    except Exception as e:  # noqa: BLE001
        return [f"`ollama --version` failed: {e}"], ""
    text = (out.stdout + " " + out.stderr).strip()
    versions = sorted(set(re.findall(r"\d+\.\d+\.\d+", text)))
    problems = [f"Ollama CLI reports version(s) {versions} (expected only {expected!r}); raw: {text!r}"
                ] if versions != [expected] else []
    return problems, text


def endpoint_problems(expected_endpoint: str | None) -> list[str]:
    """The batch must talk to ONE well-defined instance: OLLAMA_BASE_URL (runner) and
    OLLAMA_HOST (CLI / server) must agree, and match --expected-endpoint when given."""
    problems = []
    base = os.getenv("OLLAMA_BASE_URL")
    if not base:
        problems.append("OLLAMA_BASE_URL is not set (runner would silently use the default endpoint)")
        return problems
    ours = _hostport(base)
    oh = os.getenv("OLLAMA_HOST")
    if not oh:
        problems.append(f"OLLAMA_HOST is not set; the CLI would talk to the default instance, not {ours}")
    elif _hostport(oh) != ours:
        problems.append(f"OLLAMA_HOST={_hostport(oh)} != OLLAMA_BASE_URL host {ours}")
    if expected_endpoint and _hostport(expected_endpoint) != ours:
        problems.append(f"endpoint {ours} != expected endpoint {_hostport(expected_endpoint)}")
    return problems


def other_instance_problems(other_url: str | None) -> list[str]:
    """GPU isolation: the owner's separate Ollama instance must not hold models in VRAM.
    Read-only check; this driver never stops or changes another user's instance."""
    if not other_url:
        return []
    if _hostport(other_url) == _hostport(os.getenv("OLLAMA_BASE_URL", DEFAULT_BASE_URL)):
        return []                      # same instance, nothing "other" to check
    try:
        loaded = _get("/api/ps", host=other_url.rstrip("/")).get("models", [])
    except Exception:
        return []                      # not running / unreachable -> cannot interfere
    if loaded:
        names = ", ".join(f"{m.get('name')} (vram {m.get('size_vram')})" for m in loaded)
        return [f"other Ollama instance {other_url} has model(s) loaded in memory: {names} "
                f"(possible GPU/VRAM interference); ask its owner to keep it idle/unloaded"]
    return []


def gpu_snapshot() -> dict | None:
    """GPU utilisation/memory via nvidia-smi (None if unavailable). Logged, not used to stop."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        name, drv, util, used, total = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
        return {"gpu": name, "driver": drv, "util_pct": int(util), "mem_used_mib": int(used),
                "mem_total_mib": int(total)}
    except Exception:
        return None


def runtime_gate(rows: list[dict], expected_ollama: str = DEFAULT_OLLAMA,
                 expected_endpoint: str | None = None, other_url: str | None = None) -> list[str]:
    """Pre-batch gate: endpoint, CLI + server version, per-model digest, served context 16384,
    other-instance GPU isolation."""
    from p2runner.adapters import get_adapter
    problems = endpoint_problems(expected_endpoint)
    ctx_env = os.getenv("OLLAMA_CONTEXT_LENGTH")
    if ctx_env != str(EXPECTED_CONTEXT):
        problems.append(f"OLLAMA_CONTEXT_LENGTH is {ctx_env!r} in this window; it must be {EXPECTED_CONTEXT} "
                        f"(it is written into every run record)")
    problems += other_instance_problems(other_url)
    p2, _ = cli_version_problems(expected_ollama)
    problems += p2
    models: dict[str, dict] = {}
    for r in rows:
        models.setdefault(r["requested_model_name"], r)
    for model, row in models.items():
        problems += identity_problems(row, fresh_identity(model), expected_ollama)
        try:
            get_adapter("ollama", model).complete([{"role": "user", "content": "Say OK."}], [], timeout=120)
            ctx, _where = served_context(model)
            if ctx != EXPECTED_CONTEXT:
                problems.append(f"{model}: served context {ctx} != {EXPECTED_CONTEXT}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{model}: runtime check failed: {e}")
    return problems


# ------------------------------------------------------------------ attempts / logs
def load_attempts(raw_path: Path, quarantine_path: Path) -> tuple[dict[str, dict], dict[str, int]]:
    """raw_latest[run_id] = record with the highest attempt_id in raw_runs.jsonl;
    q_max[run_id] = highest attempt_id that was quarantined."""
    raw_latest: dict[str, dict] = {}
    q_max: dict[str, int] = {}
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                cur = raw_latest.get(r["run_id"])
                if cur is None or r["attempt_id"] >= cur["attempt_id"]:
                    raw_latest[r["run_id"]] = r
    if quarantine_path.exists():
        for line in quarantine_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                q_max[r["run_id"]] = max(q_max.get(r["run_id"], 0), r["attempt_id"])
    return raw_latest, q_max


def decide_attempt(raw_latest: dict | None, q_max: int | None) -> tuple[str, int | None]:
    """("skip", None) when the run already has a final (non-rerunnable) accepted record;
    otherwise ("run", next_attempt) where next_attempt never reuses an earlier attempt number
    (including quarantined ones), so evidence of earlier attempts is never overwritten."""
    top = max(raw_latest["attempt_id"] if raw_latest else 0, q_max or 0)
    if raw_latest and raw_latest["attempt_id"] >= (q_max or 0) \
            and raw_latest["execution_status"] not in RERUN_STATUSES:
        return "skip", None
    return "run", top + 1


def append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def write_batch_metadata(out: Path, args, handoff: Path, rows: list[dict]) -> None:
    """Batch-level metadata (appended once per start/resume)."""
    meta = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "runner_version": RUNNER_VERSION, "runner_source_sha256": runner_source_sha(),
        "manifest_rel": args.manifest_rel, "manifest_sha256": sha256_file(handoff / args.manifest_rel),
        "payload_sha256": sha256_file(handoff / args.payload_rel),
        "rows": len(rows), "expected_ollama": args.expected_ollama,
        "context_length_env": os.getenv("OLLAMA_CONTEXT_LENGTH"),
        "slots": args.slots, "limit": args.limit, "schema_check": not args.skip_schema_check,
        "chunk_size": args.chunk_size, "max_run_attempts": args.max_run_attempts,
        "max_consecutive_failures": args.max_consecutive_failures, "min_free_gb": args.min_free_gb,
        # environment / endpoint identity (isolated Ollama instance)
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL"), "ollama_host_env": os.getenv("OLLAMA_HOST"),
        "ollama_models_env": os.getenv("OLLAMA_MODELS"), "ollama_cli_path": shutil.which("ollama"),
        "other_ollama_checked": args.other_ollama if not args.no_other_check else None,
        "expected_endpoint": args.expected_endpoint,
        "hostname": socket.gethostname(), "windows_user": os.getenv("USERNAME"),
        "gpu_at_start": gpu_snapshot(),
    }
    try:
        from importlib import metadata
        meta["agentdojo_version"] = metadata.version("agentdojo")
    except Exception:
        meta["agentdojo_version"] = None
    try:
        meta["ollama_server_version"] = _get("/api/version").get("version")
    except Exception as e:  # noqa: BLE001
        meta["ollama_server_version"] = f"error: {e}"
    append_jsonl(out / "run_metadata_full.jsonl", meta)


# ------------------------------------------------------------------ driver safety (no effect on model behaviour)
class DriverError(Exception):
    """A clean, user-facing problem (printed without a traceback)."""

    def __init__(self, msg: str, code: int = EXIT_STOP):
        super().__init__(msg)
        self.code = code


def validate_args(args, rows: list[dict]) -> list[str]:
    """Argument problems, all reported at once (clean messages instead of tracebacks)."""
    errs = []
    if args.limit is not None and args.limit < 1:
        errs.append("--limit must be >= 1")
    if args.chunk_size is not None and args.chunk_size < 1:
        errs.append("--chunk-size must be >= 1")
    if args.checkpoint_every < 1:
        errs.append("--checkpoint-every must be >= 1")
    if args.min_free_gb < 0:
        errs.append("--min-free-gb must be >= 0")
    if not 1 <= args.max_run_attempts <= MAX_RUN_ATTEMPTS:
        errs.append(f"--max-run-attempts must be 1..{MAX_RUN_ATTEMPTS} (data contract: at most {MAX_RUN_ATTEMPTS})")
    if args.max_consecutive_failures < 1:
        errs.append("--max-consecutive-failures must be >= 1")
    if not (args.dry_run or args.gate_only) and not args.expected_endpoint:
        errs.append("--expected-endpoint is required for a real batch (e.g. http://127.0.0.1:11435) so the "
                    "batch can never silently run on the wrong Ollama instance")
    known = {r["run_id"] for r in rows}
    if args.only:
        unknown = [x for x in args.only if x not in known]
        if unknown:
            errs.append(f"--only: {len(unknown)} run_id(s) not in the manifest, e.g. {unknown[:3]}")
    try:
        out, hand = args.out.resolve(), args.handoff.resolve()
        if out == hand or hand in out.parents:
            errs.append("--out must not be inside the frozen handoff folder (it must stay untouched)")
    except OSError as e:
        errs.append(f"cannot resolve paths: {e}")
    return errs


def free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 2**30


class OutputLock:
    """One driver per output folder: two drivers on the same folder could duplicate runs or
    overwrite evidence. Created atomically; a stale lock (crashed driver) is never removed
    automatically - the operator removes it after checking that no driver is running."""

    def __init__(self, out: Path):
        self.path = out / ".run_full.lock"
        self.held = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            who = ""
            try:
                who = " (" + self.path.read_text(encoding="utf-8").strip() + ")"
            except OSError:
                pass
            raise DriverError(f"lock file exists: {self.path}{who}. Another run_full.py may be running on this "
                              f"output folder. If you are SURE none is running (e.g. it crashed), delete that "
                              f"file and start again.", EXIT_RESOURCE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} host={socket.gethostname()} started={datetime.now(timezone.utc).isoformat()}")
        self.held = True
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


def snapshot_attempts(out: Path) -> dict[str, int]:
    """Highest attempt number that already has snapshot files per run_id. Covers a driver killed
    after the snapshots were written but before the raw record was appended: that attempt
    number is never reused (its files are never overwritten)."""
    found: dict[str, int] = {}
    snap = out / "snapshots"
    if snap.exists():
        for f in snap.iterdir():
            m = re.match(r"^(?P<rid>.+)__att(?P<n>\d+)\.(?:pre|post)\.json\.gz$", f.name)
            if m:
                found[m["rid"]] = max(found.get(m["rid"], 0), int(m["n"]))
    return found


def ledger_entry(run_id: str, attempt: int, status: str | None, outcome: str, **extra) -> dict:
    """Append-only attempt ledger: which attempt of a run was ACCEPTED is recorded explicitly
    (data contract: accepted attempt identifiable, not inferred from file order)."""
    return {"run_id": run_id, "attempt_id": attempt, "execution_status": status, "outcome": outcome,
            "utc": datetime.now(timezone.utc).isoformat(), **extra}


# ------------------------------------------------------------------ main
def main() -> int:
    try:
        return _main()
    except DriverError as e:
        print(f"STOP: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("\nInterrupted by the operator. The run in progress was NOT recorded (it never reached "
              "raw_runs.jsonl). Start the same command again to resume.", file=sys.stderr)
        return EXIT_INTERRUPT
    except FileNotFoundError as e:
        print(f"STOP: file or folder not found: {e.filename or e}", file=sys.stderr)
        return EXIT_STOP
    except (json.JSONDecodeError, csv.Error, KeyError) as e:
        print(f"STOP: input file problem ({type(e).__name__}: {e}). Do not continue; report to P1.",
              file=sys.stderr)
        return EXIT_STOP


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--handoff", type=Path, default=Path("P1_to_P2_full_execution_v1.0"))
    ap.add_argument("--manifest-rel", default=DEFAULT_MANIFEST_REL)
    ap.add_argument("--payload-rel", default=DEFAULT_PAYLOAD_REL)
    ap.add_argument("--expected-manifest-sha", default=DEFAULT_MANIFEST_SHA)
    ap.add_argument("--expected-payload-sha", default=DEFAULT_PAYLOAD_SHA)
    ap.add_argument("--expected-ollama", default=DEFAULT_OLLAMA)
    ap.add_argument("--expected-endpoint", default=None,
                    help="endpoint this batch must use, e.g. http://127.0.0.1:11435 (isolated instance)")
    ap.add_argument("--other-ollama", default=DEFAULT_OTHER_OLLAMA,
                    help="the owner's separate Ollama instance to check for GPU interference (read-only)")
    ap.add_argument("--no-other-check", action="store_true", help="skip the other-instance interference check")
    ap.add_argument("--out", type=Path, default=Path("output_full"))
    ap.add_argument("--slots", nargs="+", choices=["LLM-A", "LLM-B", "LLM-C"], help="only these source LLMs")
    ap.add_argument("--only", nargs="+", help="only these run_id(s)")
    ap.add_argument("--limit", type=int, help="run only the first N planned rows")
    ap.add_argument("--chunk-size", type=int, help="execute at most N runs in this invocation, then stop "
                    "(exit 0); start the same command again to continue")
    ap.add_argument("--checkpoint-every", type=int, default=25, help="write a progress line every N executed runs")
    ap.add_argument("--min-free-gb", type=float, default=5.0,
                    help="STOP if the output drive has less free space than this (checked at start and at checkpoints)")
    ap.add_argument("--max-run-attempts", type=int, default=MAX_RUN_ATTEMPTS,
                    help=f"max attempts per intended run (data contract: at most {MAX_RUN_ATTEMPTS})")
    ap.add_argument("--max-consecutive-failures", type=int, default=5,
                    help="STOP after N consecutive technical failures (timeout/API/runner error) for review")
    ap.add_argument("--skip-schema-check", action="store_true",
                    help="NOT RECOMMENDED: do not validate each attempt's raw/eval record (recorded in the metadata)")
    ap.add_argument("--dry-run", action="store_true", help="plan + hash/config gates, execute nothing")
    ap.add_argument("--gate-only", action="store_true", help="run all gates incl. runtime, then stop")
    args = ap.parse_args()

    probs = verify_frozen_hashes(args.handoff, args.manifest_rel, args.payload_rel,
                                 args.expected_manifest_sha, args.expected_payload_sha)
    if probs:
        print("STOP (frozen hash gate):", *probs, sep="\n  ", file=sys.stderr)
        return EXIT_STOP
    print(f"[gate] frozen hashes OK  (runner code = {RUNNER_VERSION})")

    rows = read_rows(args.handoff / args.manifest_rel)
    payloads = payload_index(args.handoff / args.payload_rel)
    print(f"[gate] manifest rows = {len(rows)} | payload entries = {len(payloads)}")
    ids = [r["run_id"] for r in rows]
    if len(set(ids)) != len(ids):
        print("STOP: duplicate run_id in the manifest (1 manifest row must be exactly 1 execution)", file=sys.stderr)
        return EXIT_STOP
    errs = validate_args(args, rows)
    if errs:
        print("STOP (arguments):", *errs, sep="\n  ", file=sys.stderr)
        return EXIT_STOP

    probs = assert_frozen_config(rows, args.expected_ollama)
    if probs:
        print(f"STOP (config/runner-version gate): {len(probs)} problem(s). First few:", file=sys.stderr)
        print(*probs[:5], sep="\n  ", file=sys.stderr)
        return EXIT_STOP
    print("[gate] per-row frozen config + runner_version + ollama_version OK")

    plan = rows
    if args.slots:
        plan = [r for r in plan if r["source_llm_id"] in set(args.slots)]
    if args.only:
        plan = [r for r in plan if r["run_id"] in set(args.only)]
    if args.limit:
        plan = plan[:args.limit]
    print(f"[plan] {len(plan)} run(s) in manifest order (1 per row, no LLM multiplication)")

    other = None if args.no_other_check else args.other_ollama
    if not args.dry_run or args.gate_only:
        probs = runtime_gate(rows, args.expected_ollama, args.expected_endpoint, other)
        if probs:
            print("STOP (runtime gate):", *probs, sep="\n  ", file=sys.stderr)
            return EXIT_STOP
        print(f"[gate] runtime OK (endpoint {ollama_host()}, CLI+server {args.expected_ollama}, "
              f"digests match, context {EXPECTED_CONTEXT}, no other-instance interference)")

    if args.gate_only:
        print("gate-only: all gates passed. Not executing.")
        return 0
    if args.dry_run:
        for r in plan[:10]:
            print(f"  {r['source_llm_id']}  {r['run_id']}")
        print(f"  ... ({len(plan)} total)")
        return 0

    from p2runner.runner import RunConfig, execute_run  # lazy (needs agentdojo)
    from p2runner.adapters import get_adapter
    import time as _time

    free = free_gb(args.out)
    if free < args.min_free_gb:
        raise DriverError(f"only {free:.1f} GB free on the output drive (< --min-free-gb {args.min_free_gb}). "
                          f"Free some space and start again.", EXIT_RESOURCE)
    print(f"[gate] output drive free space {free:.1f} GB (minimum {args.min_free_gb} GB)")

    with OutputLock(args.out):
        return _run_batch(args, plan, rows, payloads, other, RunConfig, execute_run, get_adapter, _time)


def _run_batch(args, plan, rows, payloads, other, RunConfig, execute_run, get_adapter, _time) -> int:
    from validate_outputs import validate_attempt
    cfg = RunConfig(max_steps=15, timeout_s=180)
    raw_path, eval_path = args.out / "raw_runs.jsonl", args.out / "eval_evidence.jsonl"
    q_raw, q_eval = args.out / "quarantine_raw.jsonl", args.out / "quarantine_eval.jsonl"
    ledger = args.out / "attempt_ledger.jsonl"
    raw_latest, q_max = load_attempts(raw_path, q_raw)
    snaps = snapshot_attempts(args.out)
    exhausted_logged = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                if e.get("outcome") == "exhausted_no_accepted_attempt":
                    exhausted_logged.add(e["run_id"])
    write_batch_metadata(args.out, args, args.handoff, rows)

    n_ok = n_skip = n_retry = 0
    consecutive_fail = 0
    exhausted: list[str] = []
    needs_retry: list[str] = []
    t0 = _time.monotonic()
    for i, row in enumerate(plan, 1):
        run_id, model = row["run_id"], row["requested_model_name"]
        action, attempt = decide_attempt(raw_latest.get(run_id), q_max.get(run_id))
        if action == "skip":
            n_skip += 1
            continue
        if attempt <= snaps.get(run_id, 0):          # orphan snapshots of an unrecorded attempt: never reuse
            attempt = snaps[run_id] + 1
        if attempt > args.max_run_attempts:
            exhausted.append(run_id)
            if run_id not in exhausted_logged:
                append_jsonl(ledger, ledger_entry(run_id, attempt - 1, None, "exhausted_no_accepted_attempt",
                                                  note=f"{attempt - 1} attempts used, none accepted"))
                exhausted_logged.add(run_id)
            print(f"[{i}/{len(plan)}] {run_id}: {attempt - 1} attempts used, none accepted -> NOT retried "
                  f"(limit {args.max_run_attempts}); P1 decides", file=sys.stderr)
            continue
        if attempt > 1:
            n_retry += 1

        # PRE-RUN GUARD (fresh, uncached): version / digest / resolved name / endpoint / other instance
        probs = identity_problems(row, fresh_identity(model), args.expected_ollama)
        probs += endpoint_problems(args.expected_endpoint)
        probs += other_instance_problems(other)
        gpu_pre = gpu_snapshot()
        if probs:
            print(f"STOP (runtime changed BEFORE {run_id}):", *probs, sep="\n  ", file=sys.stderr)
            print("Nothing was run for this row. Report to P1; do not continue.", file=sys.stderr)
            return EXIT_RUNTIME

        case = build_case(row, payloads)
        adapter = get_adapter("ollama", model)   # identity cache is fresh from the pre-check
        rep = int((row.get("repetition_id") or "rep1").replace("rep", "") or 1)
        res = execute_run(case, adapter, row["source_llm_id"], cfg, args.out,
                          repetition_id=rep, attempt_id=attempt, run_id=run_id)
        snaps[run_id] = max(snaps.get(run_id, 0), attempt)

        # POST-RUN GUARD: runtime must be unchanged after the run as well
        probs = identity_problems(row, fresh_identity(model), args.expected_ollama)
        probs += other_instance_problems(other)
        ctx, where = served_context(model)
        if ctx is not None and ctx != EXPECTED_CONTEXT:
            probs.append(f"{model}: served context {ctx} != {EXPECTED_CONTEXT}")
        if where is not None and where != "100% GPU":
            probs.append(f"{model}: not fully on GPU after the run ({where}); possible VRAM interference")
        gpu_post = gpu_snapshot()
        append_jsonl(args.out / "resource_log.jsonl", {
            "run_id": run_id, "attempt_id": attempt, "model": model, "residency": where,
            "served_context": ctx, "gpu_pre": gpu_pre, "gpu_post": gpu_post,
            "utc": datetime.now(timezone.utc).isoformat()})
        if probs:
            note = {"quarantine_reason": probs, "quarantined_utc": datetime.now(timezone.utc).isoformat()}
            append_jsonl(q_raw, {**res["raw"], **note})
            append_jsonl(q_eval, {**res["eval"], **note})
            append_jsonl(ledger, ledger_entry(run_id, attempt, res["raw"]["execution_status"], "quarantined",
                                              retry_reason=probs))
            print(f"STOP (runtime changed DURING/AFTER {run_id}, attempt {attempt}):", *probs, sep="\n  ",
                  file=sys.stderr)
            print("This run is QUARANTINED (not in raw_runs.jsonl). Report to P1; do not continue.",
                  file=sys.stderr)
            return EXIT_RUNTIME

        # SCHEMA GUARD: the record must match its manifest row and the data contract before it is accepted
        if not args.skip_schema_check:
            errs, warns = validate_attempt(res["raw"], res["eval"], row, args.out,
                                           expected_ollama=args.expected_ollama, runner_version=RUNNER_VERSION)
            if warns:
                append_jsonl(args.out / "schema_warnings.jsonl", {"run_id": run_id, "attempt_id": attempt,
                                                                  "warnings": warns})
            if errs:
                note = {"quarantine_reason": ["schema/contract check: " + m for m in errs],
                        "quarantined_utc": datetime.now(timezone.utc).isoformat()}
                append_jsonl(q_raw, {**res["raw"], **note})
                append_jsonl(q_eval, {**res["eval"], **note})
                append_jsonl(ledger, ledger_entry(run_id, attempt, res["raw"].get("execution_status"),
                                                  "quarantined", retry_reason=note["quarantine_reason"]))
                print(f"STOP (record of {run_id} attempt {attempt} breaks the contract):", *errs[:8], sep="\n  ",
                      file=sys.stderr)
                print("This run is QUARANTINED (not in raw_runs.jsonl). Report to P1; do not continue.",
                      file=sys.stderr)
                return EXIT_SCHEMA

        append_jsonl(raw_path, res["raw"])
        append_jsonl(eval_path, res["eval"])
        raw_latest[run_id] = res["raw"]
        r = res["raw"]
        status = r["execution_status"]
        technical_failure = status in RERUN_STATUSES
        append_jsonl(ledger, ledger_entry(
            run_id, attempt, status, "failed_retryable" if technical_failure else "accepted",
            retry_reason=(status if technical_failure else None)))
        if technical_failure:
            needs_retry.append(run_id)
            consecutive_fail += 1
        else:
            consecutive_fail = 0
        inj = r["injection"]
        print(f"[{i}/{len(plan)}] {run_id} att{attempt} {status} "
              f"calls={r['usage_total']['llm_calls']} inserted={inj['injection_inserted']} "
              f"encountered={inj['injection_encountered']} gpu={where}")
        n_ok += 1

        if consecutive_fail >= args.max_consecutive_failures:
            print(f"STOP: {consecutive_fail} consecutive technical failures (last: {run_id}, {status}). "
                  f"Possible runtime/GPU/server problem. Evidence is kept. Report to P1; do not continue.",
                  file=sys.stderr)
            return EXIT_FAILURES
        if n_ok % args.checkpoint_every == 0:
            elapsed = _time.monotonic() - t0
            left = len(plan) - i
            fg = free_gb(args.out)
            append_jsonl(args.out / "checkpoint_log.jsonl", {
                "utc": datetime.now(timezone.utc).isoformat(), "executed_this_invocation": n_ok,
                "skipped_complete": n_skip, "retries": n_retry, "last_run_id": run_id,
                "rows_left_in_plan": left, "sec_per_run": round(elapsed / n_ok, 1),
                "eta_min_rough": round(left * elapsed / n_ok / 60, 1), "free_gb": round(fg, 1)})
            print(f"  [checkpoint] {n_ok} executed, ~{left * elapsed / n_ok / 60:.0f} min left (rough), "
                  f"{fg:.1f} GB free")
            if fg < args.min_free_gb:
                raise DriverError(f"only {fg:.1f} GB free (< --min-free-gb {args.min_free_gb}); stopped at a "
                                  f"checkpoint, nothing lost. Free space and start again.", EXIT_RESOURCE)
        if args.chunk_size and n_ok >= args.chunk_size:
            print(f"\nChunk of {args.chunk_size} finished. Start the same command again to continue.")
            break

    print(f"\nExecuted {n_ok} (of which retries {n_retry}), skipped {n_skip} already-complete. Output: {args.out}")
    if needs_retry:
        print(f"{len(needs_retry)} run(s) ended with a technical failure and are NOT yet accepted; start the same "
              f"command again to retry them (up to {args.max_run_attempts} attempts each).")
    if exhausted:
        print(f"WARNING: {len(exhausted)} run(s) used all {args.max_run_attempts} attempts without an accepted "
              f"result: {exhausted[:5]}{' ...' if len(exhausted) > 5 else ''}. Report to P1.", file=sys.stderr)
        return EXIT_FAILURES
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
