"""Start the whole prototype: prepare the data stores, then run the RDR site, the API and the UI.

Usage (from the repo root):
    uv run python start.py                 # ingest, then start everything
    uv run python start.py --skip-ingest   # start everything, reuse existing data
    uv run python start.py --ingest-only   # prepare data and stop

Ctrl+C stops all services.
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
PY = sys.executable
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.environ.setdefault("INCIDENT_DB_PATH", str(ROOT / "data" / "rdr.db")))

SERVICES = [
    # name, command, URL that must answer before the service counts as up
    ("RDR site", [PY, "-m", "http.server", "8765"], "http://127.0.0.1:8765/web/"),
    ("API", [PY, "-m", "uvicorn", "app.main:app", "--port", "8000"], "http://127.0.0.1:8000/incidents"),
    ("UI", [PY, "-m", "streamlit", "run", "app/streamlit_app.py",
            "--server.headless", "true", "--server.port", "8501"], "http://127.0.0.1:8501/_stcore/health"),
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def ingest() -> None:
    print("\n== RDR database")
    if DB_PATH.exists():
        print(f"{DB_PATH} exists, kept as is")
    else:
        run([PY, "-m", "app.tools.init_rdr_db"])

    print("\n== Layer 2 runbook index (docs-agent)")
    run([PY, "-m", "app.tools.docs_tools"])

    print("\n== Layer 1 knowledge base")
    run([PY, "-m", "app.layer1_rag.retriever"])


def wait_until_up(name: str, proc: subprocess.Popen, url: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"{name} exited early (port in use?)")
            return False
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    print(f"{name} did not answer at {url} within {timeout}s")
    return False


def start_services() -> None:
    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        print("\n== Starting services")
        for name, cmd, url in SERVICES:
            proc = subprocess.Popen(cmd, cwd=ROOT)
            procs.append((name, proc))
            if not wait_until_up(name, proc, url):
                raise SystemExit(f"Failed to start {name}. Stopping.")
            print(f"{name} up: {url}")

        print("\nAll services running. Press Ctrl+C to stop.")
        while all(p.poll() is None for _, p in procs):
            time.sleep(1)
        for name, proc in procs:
            if proc.poll() is not None:
                print(f"{name} stopped (exit {proc.returncode})")
    except KeyboardInterrupt:
        print("\nStopping services...")
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for _, proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # keep our headers in order with child output
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-ingest", action="store_true", help="do not rebuild the data stores")
    parser.add_argument("--ingest-only", action="store_true", help="prepare data and stop")
    parser.add_argument(
        "--demo-browser",
        action="store_true",
        help="slow visible browser-agent actions for demonstrations",
    )
    args = parser.parse_args()

    if args.demo_browser:
        # The browser service is launched as a child process, so pass the
        # opt-in demonstration setting through its environment.
        os.environ["BROWSER_DEMO_DELAY_SECONDS"] = os.environ.get("BROWSER_DEMO_DELAY_SECONDS", "1.5")

    if not args.skip_ingest:
        ingest()
    if args.ingest_only:
        print("\nData ready.")
        return
    start_services()


if __name__ == "__main__":
    main()
