#!/usr/bin/env python3
"""
SDN Access Control – Test Suite
run_tests.py

Run this INSIDE Mininet CLI like this:
    mininet> py exec(open('run_tests.py').read())

OR from a separate terminal while Mininet is running:
    sudo python3 run_tests.py
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
import subprocess
import time
import sys

# ─────────────────────────────────────────────
#  COLOUR HELPERS
# ─────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):     print(f"  {GREEN}✓  PASS{RESET}  {msg}")
def fail(msg):   print(f"  {RED}✗  FAIL{RESET}  {msg}")
def info(msg):   print(f"  {CYAN}ℹ{RESET}  {msg}")
def header(msg):
    print(f"\n{BOLD}{YELLOW}{'─'*60}{RESET}")
    print(f"{BOLD}{YELLOW}  {msg}{RESET}")
    print(f"{BOLD}{YELLOW}{'─'*60}{RESET}")

results = []

def record(name, passed):
    results.append((name, passed))
    (ok if passed else fail)(name)


# ─────────────────────────────────────────────
#  BUILD NETWORK (same as topology.py)
# ─────────────────────────────────────────────
def build_network():
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
    )
    c0 = net.addController("c0", controller=RemoteController,
                           ip="127.0.0.1", port=6633)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    h1 = net.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
    h2 = net.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
    h3 = net.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")
    h4 = net.addHost("h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04")
    h5 = net.addHost("h5", ip="10.0.0.5/24", mac="00:00:00:00:00:05")

    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s1)
    net.addLink(h4, s1)
    net.addLink(h5, s1)

    return net, s1


# ─────────────────────────────────────────────
#  PING HELPER
# ─────────────────────────────────────────────
def ping_test(src_host, dst_ip, count=3):
    """Run ping from src_host to dst_ip, return (success, avg_rtt)."""
    out = src_host.cmd(f"ping -c {count} -W 2 {dst_ip}")

    # parse the received count from line like:
    # "3 packets transmitted, 3 received, 0% packet loss"
    received = 0
    for line in out.splitlines():
        if "packets transmitted" in line:
            try:
                received = int(line.split(",")[1].strip().split()[0])
            except Exception:
                pass

    success = received > 0

    rtt = None
    for line in out.splitlines():
        if "rtt min/avg/max" in line or "round-trip" in line:
            try:
                rtt = float(line.split("/")[4])
            except Exception:
                pass
    return success, rtt


# ─────────────────────────────────────────────
#  IPERF HELPER
# ─────────────────────────────────────────────
def iperf_test(server_host, client_host, duration=5):
    """Run iperf between two hosts, return bandwidth in Mbps or None."""
    server_host.cmd("pkill iperf 2>/dev/null; sleep 0.5")
    server_host.cmd("iperf -s -D")
    time.sleep(2)
    out = client_host.cmd(f"iperf -c {server_host.IP()} -t {duration} 2>&1")
    server_host.cmd("pkill iperf 2>/dev/null")
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


# ─────────────────────────────────────────────
#  FLOW TABLE HELPER
# ─────────────────────────────────────────────
def get_flows():
    result = subprocess.run(
        "ovs-ofctl -O OpenFlow13 dump-flows s1",
        shell=True, capture_output=True, text=True
    )
    return result.stdout


# ─────────────────────────────────────────────
#  SCENARIO 1 – Allowed vs Blocked Ping
# ─────────────────────────────────────────────
def scenario_ping(hosts):
    header("SCENARIO 1 – Allowed vs Blocked (Ping)")
    h1, h2, h3, h4, h5 = hosts

    info("Testing ALLOWED pairs (expect ping success)…")
    for src, dst_ip, label in [
        (h1, "10.0.0.2", "h1→h2"),
        (h1, "10.0.0.3", "h1→h3"),
        (h2, "10.0.0.3", "h2→h3"),
        (h2, "10.0.0.1", "h2→h1"),
        (h3, "10.0.0.1", "h3→h1"),
        (h3, "10.0.0.2", "h3→h2"),
    ]:
        success, rtt = ping_test(src, dst_ip)
        name = f"ping {label}"
        record(name, success)
        if success and rtt:
            info(f"    RTT = {rtt:.3f} ms")

    info("\nTesting BLOCKED pairs (expect ping failure)…")
    for src, dst_ip, label in [
        (h4, "10.0.0.1", "h4→h1"),
        (h4, "10.0.0.2", "h4→h2"),
        (h4, "10.0.0.3", "h4→h3"),
        (h5, "10.0.0.1", "h5→h1"),
        (h5, "10.0.0.2", "h5→h2"),
        (h5, "10.0.0.3", "h5→h3"),
        (h1, "10.0.0.4", "h1→h4"),
        (h1, "10.0.0.5", "h1→h5"),
        (h4, "10.0.0.5", "h4→h5"),
    ]:
        success, _ = ping_test(src, dst_ip)
        name = f"ping {label} (should be blocked)"
        record(name, not success)   # pass = NOT reachable


# ─────────────────────────────────────────────
#  SCENARIO 2 – iperf Throughput
# ─────────────────────────────────────────────
def scenario_iperf(hosts):
    header("SCENARIO 2 – Throughput: Allowed vs Blocked (iperf)")
    h1, h2, h3, h4, h5 = hosts

    info("iperf h1->h2 (allowed, expect bandwidth)...")
    h2.cmd("pkill iperf; sleep 1")
    h2.sendCmd("iperf -s")
    time.sleep(3)
    out = h1.cmd("iperf -c 10.0.0.2 -t 5 -y C 2>&1")
    h2.sendInt()
    h2.waitOutput()
    info(f"  output: {out.strip()[:200]}")
    # CSV format: timestamp,src,sport,dst,dport,id,interval,bytes,bits/sec
    bw = None
    for line in out.splitlines():
        line = line.strip()
        if line and "," in line:
            parts = line.split(",")
            if len(parts) >= 9:
                try:
                    bw = float(parts[8]) / 1e6  # convert bps to Mbps
                except Exception:
                    pass
        elif "Mbits/sec" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if "Mbits" in p:
                    try:
                        bw = float(parts[i-1])
                    except Exception:
                        pass
    if bw and bw > 0:
        record(f"iperf h1->h2  bandwidth={bw:.1f} Mbits/sec", True)
    else:
        record("iperf h1->h2 (should succeed)", False)

    info("iperf h4->h1 (blocked, expect connection fail)...")
    h1.cmd("pkill iperf; sleep 1")
    h1.sendCmd("iperf -s")
    time.sleep(3)
    out_blocked = h4.cmd("iperf -c 10.0.0.1 -t 5 2>&1")
    h1.sendInt()
    h1.waitOutput()
    info(f"  output: {out_blocked.strip()[:120]}")
    if "Mbits/sec" not in out_blocked:
        record("iperf h4->h1 (blocked - no bandwidth, correct)", True)
    else:
        record("iperf h4->h1 returned bandwidth (should be blocked)", False)


# ─────────────────────────────────────────────
#  SCENARIO 3 – Regression
# ─────────────────────────────────────────────
def scenario_regression(hosts):
    header("SCENARIO 3 – Regression: Policy Consistency")
    h1, h2, h3, h4, h5 = hosts

    info("Re-running allowed pings after flow rules installed…")
    for src, dst_ip, label in [
        (h1, "10.0.0.2", "h1→h2"),
        (h2, "10.0.0.3", "h2→h3"),
    ]:
        success, _ = ping_test(src, dst_ip)
        record(f"[regression] ping {label} still allowed", success)

    info("Re-running blocked pings after flow rules installed…")
    for src, dst_ip, label in [
        (h4, "10.0.0.1", "h4→h1"),
        (h5, "10.0.0.2", "h5→h2"),
    ]:
        success, _ = ping_test(src, dst_ip)
        record(f"[regression] ping {label} still blocked", not success)

    info("Checking flow table for DROP rules…")
    flows = get_flows()
    print(f"\n{CYAN}Flow Table:{RESET}\n{flows}")

    drop_h4 = "10.0.0.4" in flows and "drop" in flows.lower()
    drop_h5 = "10.0.0.5" in flows and "drop" in flows.lower()
    record("DROP rule exists for 10.0.0.4", drop_h4)
    record("DROP rule exists for 10.0.0.5", drop_h5)


# ─────────────────────────────────────────────
#  SUMMARY
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    setLogLevel("warning")   # suppress Mininet noise during tests

    print(f"\n{BOLD}SDN Access Control – Automated Test Suite{RESET}")
    print("Building network and connecting to Ryu controller…\n")

    net, s1 = build_network()
    net.start()
    s1.cmd("ovs-vsctl set Bridge s1 protocols=OpenFlow13")

    info("Waiting for controller to connect (5s)…")
    time.sleep(5)

    hosts = [net.get(h) for h in ["h1", "h2", "h3", "h4", "h5"]]

    try:
        scenario_ping(hosts)
        scenario_iperf(hosts)
        scenario_regression(hosts)
        success = print_summary()
    finally:
        net.stop()

    sys.exit(0 if success else 1)