#!/usr/bin/env python3
"""
SDN Access Control – Automated Test Suite
tests/run_tests.py

Runs inside Mininet (after topology.py is up) OR can be called
by topology.py directly.  Covers:

  Scenario 1 – Allowed vs Blocked (ping)
  Scenario 2 – Throughput allowed vs blocked (iperf)
  Scenario 3 – Regression: policy consistency re-check after flows installed

Usage (from inside Mininet CLI):
  sh python3 tests/run_tests.py

Or from topology.py directly – pass the `net` object.
"""

import subprocess
import sys
import time


# ─────────────────────────────────────────────────────────────
#  COLOUR HELPERS
# ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}✓  PASS{RESET}  {msg}")
def fail(msg): print(f"  {RED}✗  FAIL{RESET}  {msg}")
def info(msg): print(f"  {CYAN}ℹ{RESET}  {msg}")
def header(msg):
    print(f"\n{BOLD}{YELLOW}{'─'*60}{RESET}")
    print(f"{BOLD}{YELLOW}  {msg}{RESET}")
    print(f"{BOLD}{YELLOW}{'─'*60}{RESET}")


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
def mn_cmd(host, cmd):
    """Run a shell command on a Mininet host (via 'mn exec')."""
    full = f"mn exec {host} -- {cmd}"
    result = subprocess.run(full, shell=True,
                            capture_output=True, text=True, timeout=30)
    return result.stdout + result.stderr


def ping_test(src, dst, count=3):
    """Return (success:bool, avg_rtt:float|None)."""
    out = mn_cmd(src, f"ping -c {count} -W 2 {dst}")
    if "0 received" in out or "100% packet loss" in out:
        return False, None
    # Extract avg RTT from last line:  rtt min/avg/max/mdev = 0.1/0.2/0.3/0.05 ms
    for line in out.splitlines():
        if "rtt min/avg/max" in line or "round-trip" in line:
            try:
                rtt = float(line.split("/")[4])
                return True, rtt
            except Exception:
                pass
    # Packets were received but we couldn't parse RTT
    if " 0% packet loss" in out:
        return True, None
    return False, None


def iperf_test(server_host, client_host, duration=5):
    """Return (bandwidth_Mbps:float|None)."""
    # Start iperf server in background
    mn_cmd(server_host, "pkill iperf 2>/dev/null; sleep 0.2")
    mn_cmd(server_host, f"iperf -s -D")
    time.sleep(1)

    out = mn_cmd(client_host,
                 f"iperf -c {_ip(server_host)} -t {duration} 2>&1")
    mn_cmd(server_host, "pkill iperf 2>/dev/null")

    for line in out.splitlines():
        if "Mbits/sec" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if "Mbits" in p:
                    try:
                        return float(parts[i - 1])
                    except Exception:
                        pass
    return None


def _ip(host_name):
    base = {"h1": "10.0.0.1", "h2": "10.0.0.2", "h3": "10.0.0.3",
            "h4": "10.0.0.4", "h5": "10.0.0.5"}
    return base[host_name]


def show_flows():
    """Dump the flow table from switch s1."""
    out = subprocess.run("ovs-ofctl -O OpenFlow13 dump-flows s1",
                         shell=True, capture_output=True, text=True).stdout
    return out


# ─────────────────────────────────────────────────────────────
#  TEST SCENARIOS
# ─────────────────────────────────────────────────────────────

results = []   # (test_name, passed)

def record(name, passed):
    results.append((name, passed))
    (ok if passed else fail)(name)


# ── Scenario 1: Allowed vs Blocked Ping ──────────────────────
def scenario_ping():
    header("SCENARIO 1 – Allowed vs Blocked (Ping)")

    ALLOWED_PAIRS = [
        ("h1", "h2"), ("h1", "h3"), ("h2", "h3"),
        ("h2", "h1"), ("h3", "h1"), ("h3", "h2"),   # reverse
    ]
    BLOCKED_PAIRS = [
        ("h4", "h1"), ("h4", "h2"), ("h4", "h3"),
        ("h5", "h1"), ("h5", "h2"), ("h5", "h3"),
        ("h1", "h4"), ("h1", "h5"),
        ("h4", "h5"),
    ]

    info("Testing ALLOWED pairs (expect ping success)…")
    for src, dst in ALLOWED_PAIRS:
        success, rtt = ping_test(src, _ip(dst))
        label = f"ping {src}→{dst}"
        if success:
            record(label, True)
            if rtt:
                info(f"  RTT = {rtt:.3f} ms")
        else:
            record(label, False)

    info("\nTesting BLOCKED pairs (expect ping failure)…")
    for src, dst in BLOCKED_PAIRS:
        success, _ = ping_test(src, _ip(dst))
        label = f"ping {src}→{dst} (should be blocked)"
        record(label, not success)   # pass = NOT reachable


# ── Scenario 2: Throughput Allowed vs Blocked (iperf) ────────
def scenario_iperf():
    header("SCENARIO 2 – Throughput: Allowed vs Blocked (iperf)")

    info("iperf h1 → h2  (allowed pair, expect bandwidth)…")
    bw = iperf_test("h2", "h1")
    if bw is not None and bw > 0:
        record(f"iperf h1→h2 bandwidth={bw:.1f} Mbps", True)
    else:
        record("iperf h1→h2 (should succeed)", False)

    info("iperf h4 → h1  (blocked pair, expect 0 / connection refused)…")
    bw_blocked = iperf_test("h1", "h4")
    # If blocked, iperf client should fail → None bandwidth
    if bw_blocked is None:
        record("iperf h4→h1 (blocked, no bandwidth – correct)", True)
    else:
        record(f"iperf h4→h1 returned {bw_blocked} Mbps (should be 0)", False)


# ── Scenario 3: Regression – Policy Consistency ──────────────
def scenario_regression():
    header("SCENARIO 3 – Regression: Policy Consistency")

    info("Re-running ping tests after flows are installed…")
    # Allowed once more
    for src, dst in [("h1", "h2"), ("h2", "h3")]:
        success, rtt = ping_test(src, _ip(dst))
        record(f"[regression] ping {src}→{dst}", success)

    # Blocked once more
    for src, dst in [("h4", "h1"), ("h5", "h2")]:
        success, _ = ping_test(src, _ip(dst))
        record(f"[regression] ping {src}→{dst} still blocked", not success)

    info("Checking flow table entries…")
    flows = show_flows()
    info(f"Flow table:\n{flows}")

    # Verify DROP rules exist for h4/h5
    has_drop_h4 = "10.0.0.4" in flows and "actions=drop" in flows.lower()
    record("DROP rule present for 10.0.0.4 in flow table", has_drop_h4)

    has_drop_h5 = "10.0.0.5" in flows and "actions=drop" in flows.lower()
    record("DROP rule present for 10.0.0.5 in flow table", has_drop_h5)


# ─────────────────────────────────────────────────────────────
#  SUMMARY
# ─────────────────────────────────────────────────────────────
def print_summary():
    header("TEST SUMMARY")
    passed = sum(1 for _, p in results if p)
    total  = len(results)
    for name, p in results:
        (ok if p else fail)(name)
    print(f"\n{BOLD}Result: {passed}/{total} tests passed{RESET}")
    if passed == total:
        print(f"{GREEN}{BOLD}All tests passed! ✓{RESET}\n")
    else:
        print(f"{RED}{BOLD}{total - passed} test(s) failed.{RESET}\n")
    return passed == total


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{BOLD}SDN Access Control – Test Suite{RESET}")
    print("Make sure topology.py and the Ryu controller are running.\n")

    scenario_ping()
    scenario_iperf()
    scenario_regression()
    success = print_summary()
    sys.exit(0 if success else 1)