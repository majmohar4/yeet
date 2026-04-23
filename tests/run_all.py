#!/usr/bin/env python3
"""
Yeet automated test orchestrator.
Generates assets, starts the service, runs all test suites, reports results.
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_URL = "http://localhost:4534"
ADMIN_PASSWORD = "testadmin"


class Colors:
    GREEN  = '\033[92m'
    RED    = '\033[91m'
    YELLOW = '\033[93m'
    BLUE   = '\033[94m'
    BOLD   = '\033[1m'
    RESET  = '\033[0m'


def header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'─'*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text:^60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'─'*60}{Colors.RESET}\n")


def run(cmd, desc, *, capture=True, timeout=300):
    print(f"  {Colors.YELLOW}▶{Colors.RESET} {desc}")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=capture,
            text=True, timeout=timeout,
        )
        ok = result.returncode == 0
        mark = f"{Colors.GREEN}✓{Colors.RESET}" if ok else f"{Colors.RED}✗{Colors.RESET}"
        print(f"  {mark} {desc}")
        if not ok and capture and result.stderr:
            print(f"    {result.stderr.strip()[:300]}")
        return ok, (result.stdout if capture else "")
    except subprocess.TimeoutExpired:
        print(f"  {Colors.RED}✗ Timeout{Colors.RESET}")
        return False, "timeout"
    except Exception as e:
        print(f"  {Colors.RED}✗ {e}{Colors.RESET}")
        return False, str(e)


def wait_for_health(max_wait=90):
    print(f"  {Colors.YELLOW}▶{Colors.RESET} Waiting for /health …")
    try:
        import requests
    except ImportError:
        import urllib.request as _ur
        for i in range(max_wait):
            try:
                _ur.urlopen(f"{BASE_URL}/health", timeout=2)
                print(f"  {Colors.GREEN}✓ Service healthy{Colors.RESET}")
                return True
            except Exception:
                time.sleep(1)
        print(f"  {Colors.RED}✗ Timeout{Colors.RESET}")
        return False

    for i in range(max_wait):
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=2)
            if r.status_code == 200:
                data = r.json()
                print(f"  {Colors.GREEN}✓ Service healthy — v{data.get('version', '?')}{Colors.RESET}")
                return True
        except Exception:
            pass
        time.sleep(1)
        if i > 0 and i % 15 == 0:
            print(f"    still waiting … ({i}s)")
    print(f"  {Colors.RED}✗ Timeout{Colors.RESET}")
    return False


def step_check_docker():
    header("Step 1 — Check Docker")
    ok1, _ = run("docker --version", "Docker installed")
    ok2, _ = run("docker-compose --version || docker compose version", "docker-compose installed")
    return ok1 and ok2


def step_start_service():
    header("Step 2 — Start Yeet")
    run("docker-compose down --remove-orphans", "Stop old containers", capture=False)
    ok, _ = run(
        f"SECRET_KEY=test-secret-key-for-automated-tests-1234567890 "
        f"ADMIN_PASSWORD={ADMIN_PASSWORD} "
        "docker-compose up -d --build",
        "Build & start Yeet",
        capture=False,
        timeout=600,
    )
    if not ok:
        return False
    return wait_for_health()


def step_generate_assets():
    header("Step 3 — Generate Test Assets")
    ok, _ = run("python3 tests/setup_tests.py", "Generate test files", capture=False)
    return ok


def step_run_tests():
    header("Step 4 — Run Test Suites")

    suites = [
        ("Security & Functional",
         f"YEET_TEST_URL={BASE_URL} "
         f"YEET_TEST_ADMIN_PASSWORD={ADMIN_PASSWORD} "
         "YEET_DB_PATH=/data/yeet.db "
         "python3 -m pytest tests/test_security.py -v --tb=short -q"),
    ]

    results = {}
    for name, cmd in suites:
        print(f"\n  {Colors.BOLD}▶ {name}{Colors.RESET}")
        ok, _ = run(cmd, name, capture=False, timeout=600)
        results[name] = ok

    return results


def step_collect_logs():
    header("Step 5 — Collect Logs")
    Path('test_results').mkdir(exist_ok=True)
    ok, logs = run("docker-compose logs --tail=1000", "Collect container logs")
    if ok:
        Path('test_results/container_logs.txt').write_text(logs)
        print(f"    → test_results/container_logs.txt")
    return ok


def step_report(results, duration):
    header("Step 6 — Report")
    Path('test_results').mkdir(exist_ok=True)

    total  = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": round(duration, 1),
        "summary": {"total": total, "passed": passed, "failed": failed},
        "suites": {k: ("PASS" if v else "FAIL") for k, v in results.items()},
    }

    Path('test_results/test_report.json').write_text(json.dumps(report, indent=2))

    md = [
        "# Yeet Test Report",
        f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Duration:** {duration:.1f}s\n",
        "## Summary\n",
        f"- Total: **{total}**",
        f"- Passed: **{passed}** ✓",
        f"- Failed: **{failed}** ✗\n",
        "## Suites\n",
    ]
    for name, ok in results.items():
        md.append(f"- {'✓' if ok else '✗'} **{name}**")
    Path('test_results/test_report.md').write_text('\n'.join(md))

    print(f"  → test_results/test_report.json")
    print(f"  → test_results/test_report.md")
    return True


def print_summary(results, duration):
    header("Summary")
    total  = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed

    for name, ok in results.items():
        mark = f"{Colors.GREEN}PASS{Colors.RESET}" if ok else f"{Colors.RED}FAIL{Colors.RESET}"
        print(f"  {mark}  {name}")

    print(f"\n  Duration: {duration:.1f}s")
    print(f"  Report:   test_results/test_report.md")

    if failed == 0:
        print(f"\n  {Colors.GREEN}{Colors.BOLD}✓ ALL TESTS PASSED{Colors.RESET}\n")
    else:
        print(f"\n  {Colors.RED}{Colors.BOLD}✗ {failed} SUITE(S) FAILED{Colors.RESET}\n")

    return failed == 0


def main():
    t0 = datetime.now()
    header("Yeet Automated Test Suite")

    if not step_check_docker():
        print(f"\n{Colors.RED}Docker not available — aborting.{Colors.RESET}")
        return 1

    if not step_start_service():
        print(f"\n{Colors.RED}Service failed to start — aborting.{Colors.RESET}")
        return 1

    step_generate_assets()

    results = step_run_tests()
    step_collect_logs()

    duration = (datetime.now() - t0).total_seconds()
    step_report(results, duration)
    all_ok = print_summary(results, duration)
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
