#!/usr/bin/env python3
"""
SDN Access Control – Mininet Topology
topology.py

Network layout:
                    ┌──────┐
          h1 ───── │      │ ───── h4
          h2 ───── │  s1  │ ───── h5
          h3 ───── │      │
                    └──────┘

Hosts:
  h1 = 10.0.0.1  (authorized)
  h2 = 10.0.0.2  (authorized)
  h3 = 10.0.0.3  (authorized)
  h4 = 10.0.0.4  (UNAUTHORIZED – blocked)
  h5 = 10.0.0.5  (UNAUTHORIZED – blocked)

Whitelist (defined in controller):
  h1 <-> h2  ✓
  h1 <-> h3  ✓
  h2 <-> h3  ✓
  h4, h5 → blocked from all communication

Usage:
  sudo python3 topology.py
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI
import time


def build_topology():
    """Build and return the Mininet network."""
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
    )

    info("*** Adding Ryu remote controller\n")
    c0 = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6633,
    )

    info("*** Adding switch\n")
    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    info("*** Adding hosts\n")
    h1 = net.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
    h2 = net.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
    h3 = net.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")
    h4 = net.addHost("h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04")  # UNAUTHORIZED
    h5 = net.addHost("h5", ip="10.0.0.5/24", mac="00:00:00:00:00:05")  # UNAUTHORIZED

    info("*** Creating links\n")
    net.addLink(h1, s1, bw=10)
    net.addLink(h2, s1, bw=10)
    net.addLink(h3, s1, bw=10)
    net.addLink(h4, s1, bw=10)
    net.addLink(h5, s1, bw=10)

    return net, c0, s1, [h1, h2, h3, h4, h5]


def run():
    setLogLevel("info")
    info("=" * 60 + "\n")
    info("  SDN Access Control Topology\n")
    info("  Authorized  : h1(10.0.0.1), h2(10.0.0.2), h3(10.0.0.3)\n")
    info("  Unauthorized: h4(10.0.0.4), h5(10.0.0.5)\n")
    info("=" * 60 + "\n")

    net, c0, s1, hosts = build_topology()

    info("*** Starting network\n")
    net.start()

    info("*** Waiting for controller connection (3s)…\n")
    time.sleep(3)

    # Force OVS to use OpenFlow 1.3
    s1.cmd("ovs-vsctl set Bridge s1 protocols=OpenFlow13")

    info("\n*** Network is up.  Opening CLI…\n")
    info("    Quick test commands:\n")
    info("    h1 ping h2     → should SUCCEED\n")
    info("    h1 ping h4     → should FAIL (blocked)\n")
    info("    Run 'python3 tests/run_tests.py' in another terminal for full tests.\n\n")

    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    run()