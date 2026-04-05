# SDN-Based Access Control System

> **Course Project #11** – OpenFlow / Ryu / Mininet  
> Implements whitelist-based host access control inside a software-defined network.

---

## Problem Statement

Design and implement an SDN controller that **allows only authorized hosts to communicate** within the network.  
Unauthorized traffic is silently dropped at the switch level using OpenFlow flow rules.

**Goals:**
- Maintain a whitelist of permitted host pairs
- Install `ALLOW` flow rules for whitelisted pairs
- Install `DROP` flow rules for all other pairs
- Verify access control with live traffic tests (ping + iperf)
- Regression test: confirm policy consistency after rules are installed

---

## Network Topology

```
          h1 (10.0.0.1)  ──┐
          h2 (10.0.0.2)  ──┤
          h3 (10.0.0.3)  ──┤── s1 (OVS)  ←── Ryu Controller
          h4 (10.0.0.4)  ──┤               (127.0.0.1:6633)
          h5 (10.0.0.5)  ──┘
```

| Host | IP          | Status       |
|------|-------------|--------------|
| h1   | 10.0.0.1    | ✅ Authorized |
| h2   | 10.0.0.2    | ✅ Authorized |
| h3   | 10.0.0.3    | ✅ Authorized |
| h4   | 10.0.0.4    | ❌ Unauthorized |
| h5   | 10.0.0.5    | ❌ Unauthorized |

**Whitelist (bidirectional):**
- h1 ↔ h2 ✓
- h1 ↔ h3 ✓
- h2 ↔ h3 ✓

All other pairs (anything involving h4 or h5) → **BLOCKED**.

---

## SDN Design

### Controller Logic

```
packet_in event
  │
  ├── ARP?  ──► flood (so hosts can resolve MACs)
  │
  └── IPv4?
        │
        ├── (src_ip, dst_ip) in WHITELIST?
        │     ├── YES ──► install bidirectional ALLOW rules → forward
        │     └── NO  ──► install DROP rules → discard packet
        └── log decision
```

### Flow Rule Design

| Rule Type | Priority | Match                     | Action   | Idle Timeout |
|-----------|----------|---------------------------|----------|--------------|
| Table-miss | 1       | (any)                     | → ctrl   | permanent    |
| BLOCK      | 10      | eth_type=IP, src+dst pair | DROP     | 30 s         |
| ALLOW      | 20      | eth_type=IP, src+dst pair | output X | 30 s         |

ALLOW rules have higher priority than BLOCK to prevent conflicts.

---

## Prerequisites

```bash
# Ubuntu 20.04/22.04 recommended
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-pip wireshark iperf

pip3 install ryu
```

---

## Setup & Execution

### Step 1 – Start the Ryu Controller

Open **Terminal 1**:

```bash
ryu-manager controller/access_control.py --verbose
```

You should see:
```
AccessControlController started.
Whitelist has 3 allowed pairs.
```

### Step 2 – Start the Mininet Topology

Open **Terminal 2**:

```bash
sudo python3 topology.py
```

After the network starts you will see the Mininet CLI prompt (`mininet>`).

### Step 3 – Quick Manual Tests

Inside the Mininet CLI:

```bash
# ── Scenario 1: Allowed vs Blocked ──────────────────────────

# Should SUCCEED (h1 and h2 are whitelisted)
mininet> h1 ping -c 3 h2

# Should FAIL (h4 is unauthorized)
mininet> h1 ping -c 3 h4

# Should SUCCEED
mininet> h2 ping -c 3 h3

# Should FAIL
mininet> h5 ping -c 3 h1

# ── Inspect flow table ──────────────────────────────────────
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1

# ── Throughput test ─────────────────────────────────────────
# Allowed pair
mininet> h2 iperf -s &
mininet> h1 iperf -c 10.0.0.2 -t 5

# Blocked pair
mininet> h1 iperf -s &
mininet> h4 iperf -c 10.0.0.1 -t 5   # should timeout/fail
```

### Step 4 – Run Automated Test Suite

Open **Terminal 3** (while Mininet is still running):

```bash
python3 tests/run_tests.py
```

---

## Expected Output

### Ping – Allowed Pair (h1 → h2)
```
PING 10.0.0.2 (10.0.0.2) 56(84) bytes of data.
64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.3 ms
64 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=0.2 ms
--- 10.0.0.2 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss
```

### Ping – Blocked Pair (h1 → h4)
```
PING 10.0.0.4 (10.0.0.4) 56(84) bytes of data.
--- 10.0.0.4 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss
```

### Flow Table (after first ping)
```
cookie=0x0, duration=5s, table=0, n_packets=3, priority=20,
  ip,nw_src=10.0.0.1,nw_dst=10.0.0.2 actions=output:2

cookie=0x0, duration=5s, table=0, n_packets=0, priority=10,
  ip,nw_src=10.0.0.1,nw_dst=10.0.0.4 actions=drop
```

### Test Suite Summary
```
──────────────────────────────────────────────────────────────
  TEST SUMMARY
──────────────────────────────────────────────────────────────
  ✓  PASS  ping h1→h2
  ✓  PASS  ping h1→h4 (should be blocked)
  ✓  PASS  iperf h1→h2 bandwidth=9.5 Mbps
  ✓  PASS  iperf h4→h1 (blocked, no bandwidth – correct)
  ✓  PASS  [regression] ping h1→h2
  ✓  PASS  DROP rule present for 10.0.0.4 in flow table
  ...
Result: 18/18 tests passed ✓
```

---

## Wireshark Capture

To capture on the virtual interface between h1 and s1:

```bash
# Find the interface name
sudo ovs-vsctl show | grep Interface

# Capture (e.g. s1-eth1 is h1's port)
sudo wireshark -i s1-eth1 &
```

Filter: `ip.addr == 10.0.0.4` to observe dropped packets never reaching the destination.

---

## File Structure

```
sdn-access-control/
├── controller/
│   └── access_control.py    # Ryu controller – core logic
├── tests/
│   └── run_tests.py         # Automated test suite (3 scenarios)
├── topology.py              # Mininet topology (5 hosts, 1 switch)
└── README.md
```

---

## References

1. OpenFlow 1.3 Specification – https://opennetworking.org/wp-content/uploads/2014/10/openflow-switch-v1.3.5.pdf
2. Ryu SDN Framework Documentation – https://ryu.readthedocs.io/
3. Mininet Documentation – http://mininet.org/overview/
4. Open vSwitch Documentation – https://docs.openvswitch.org/
5. Lantz, B. et al. "A Network in a Laptop: Rapid Prototyping for Software-Defined Networks." HotNets 2010.