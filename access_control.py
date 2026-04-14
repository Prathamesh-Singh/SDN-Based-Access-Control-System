"""
SDN-Based Access Control System
Ryu OpenFlow Controller - access_control.py

Implements whitelist-based host communication control.
Only authorized host pairs can communicate; all others are blocked.
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, arp
from ryu.lib import mac
import logging

# ─────────────────────────────────────────────
#  WHITELIST CONFIGURATION
#  Define which host PAIRS are allowed to talk.
#  Format: frozenset({src_ip, dst_ip})
# ─────────────────────────────────────────────
WHITELIST = [
    frozenset({"10.0.0.1", "10.0.0.2"}),   # h1 <-> h2  ALLOWED
    frozenset({"10.0.0.1", "10.0.0.3"}),   # h1 <-> h3  ALLOWED
    frozenset({"10.0.0.2", "10.0.0.3"}),   # h2 <-> h3  ALLOWED
    # h4 and h5 are NOT in any whitelist pair → they are blocked
]

# Flow rule priorities
PRIORITY_BLOCK   = 10   # deny rules
PRIORITY_ALLOW   = 20   # allow rules
PRIORITY_DEFAULT =  1   # table-miss

# Idle/hard timeouts (seconds); 0 = permanent
IDLE_TIMEOUT = 30
HARD_TIMEOUT = 0


class AccessControlController(app_manager.RyuApp):
    """
    Ryu application that enforces whitelist-based access control.

    On each packet_in:
      1. Learn the source MAC → port mapping.
      2. Check if the (src_ip, dst_ip) pair is on the whitelist.
      3. If allowed  → install bidirectional ALLOW flow rules and forward.
      4. If denied   → install DROP rule and discard the packet.
      5. ARP is always forwarded (flooded) so hosts can resolve MACs.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # mac_to_port[dpid][mac] = port
        self.mac_to_port: dict[int, dict[str, int]] = {}
        self.logger.setLevel(logging.DEBUG)
        self.logger.info("AccessControlController started.")
        self.logger.info("Whitelist has %d allowed pairs.", len(WHITELIST))

    # ──────────────────────────────────────────
    #  SWITCH HANDSHAKE  – install table-miss
    # ──────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        # Table-miss: send all unmatched packets to controller
        match  = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, PRIORITY_DEFAULT, match, actions)
        self.logger.info("Switch %016x connected – table-miss installed.",
                         datapath.id)

    # ──────────────────────────────────────────
    #  PACKET_IN  – main decision logic
    # ──────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        in_port  = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        dst_mac = eth.dst
        src_mac = eth.src
        dpid    = datapath.id

        # ── MAC learning ──────────────────────
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port

        # ── ARP: always flood ─────────────────
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            self._flood(datapath, msg, in_port)
            return

        # ── IPv4 only beyond here ─────────────
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt is None:
            # Non-IP, non-ARP: flood
            self._flood(datapath, msg, in_port)
            return

        src_ip = ip_pkt.src
        dst_ip = ip_pkt.dst
        pair   = frozenset({src_ip, dst_ip})

        if pair in WHITELIST:
            self.logger.info("[ALLOW] %s → %s on switch %016x",
                             src_ip, dst_ip, dpid)
            self._handle_allowed(datapath, msg, src_mac, dst_mac,
                                 src_ip, dst_ip, in_port)
        else:
            self.logger.warning("[BLOCK] %s → %s on switch %016x – not in whitelist",
                                src_ip, dst_ip, dpid)
            self._handle_blocked(datapath, src_ip, dst_ip)

    # ──────────────────────────────────────────
    #  ALLOW: install bidirectional rules + fwd
    # ──────────────────────────────────────────
    def _handle_allowed(self, datapath, msg, src_mac, dst_mac,
                        src_ip, dst_ip, in_port):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        dpid    = datapath.id

        # Determine output port (use learned table; flood if unknown)
        out_port = self.mac_to_port[dpid].get(dst_mac, ofproto.OFPP_FLOOD)

        # Install forward rule (src→dst)
        match_fwd = parser.OFPMatch(eth_type=0x0800,
                                    ipv4_src=src_ip, ipv4_dst=dst_ip)
        actions_fwd = [parser.OFPActionOutput(out_port)]
        self._add_flow(datapath, PRIORITY_ALLOW, match_fwd, actions_fwd,
                       idle_timeout=IDLE_TIMEOUT)

        # Install reverse rule (dst→src) if we know the return port
        rev_port = self.mac_to_port[dpid].get(src_mac, ofproto.OFPP_FLOOD)
        match_rev = parser.OFPMatch(eth_type=0x0800,
                                    ipv4_src=dst_ip, ipv4_dst=src_ip)
        actions_rev = [parser.OFPActionOutput(rev_port)]
        self._add_flow(datapath, PRIORITY_ALLOW, match_rev, actions_rev,
                       idle_timeout=IDLE_TIMEOUT)

        # Send the buffered packet out
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions_fwd,
            data=data,
        )
        datapath.send_msg(out)

    # ──────────────────────────────────────────
    #  BLOCK: install DROP rule, discard packet
    # ──────────────────────────────────────────
    def _handle_blocked(self, datapath, src_ip, dst_ip):
        parser = datapath.ofproto_parser

        # Drop src→dst
        match = parser.OFPMatch(eth_type=0x0800,
                                ipv4_src=src_ip, ipv4_dst=dst_ip)
        self._add_flow(datapath, PRIORITY_BLOCK, match, [],
                       idle_timeout=IDLE_TIMEOUT)

        # Drop dst→src too
        match_rev = parser.OFPMatch(eth_type=0x0800,
                                    ipv4_src=dst_ip, ipv4_dst=src_ip)
        self._add_flow(datapath, PRIORITY_BLOCK, match_rev, [],
                       idle_timeout=IDLE_TIMEOUT)

    # ──────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────
    def _add_flow(self, datapath, priority, match, actions,
                  idle_timeout=0, hard_timeout=HARD_TIMEOUT):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        )
        datapath.send_msg(mod)

    def _flood(self, datapath, msg, in_port):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)