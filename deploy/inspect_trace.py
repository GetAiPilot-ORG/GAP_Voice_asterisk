#!/usr/bin/env python3
"""
Deep RTP and SIP Diagnostics for Asterisk Outbound Audio Path.
Parses PCAP capture and Asterisk console log to determine why audio was silent on handset.
"""
import sys
import struct
import socket

def parse_pcap(pcap_path):
    print(f"==================================================================")
    print(f"  DEEP TELEPHONY ANALYSIS: {pcap_path}")
    print(f"==================================================================")
    with open(pcap_path, "rb") as f:
        pcap_data = f.read()

    if len(pcap_data) < 24:
        print("ERROR: Invalid PCAP file (too small)")
        return

    magic = pcap_data[:4]
    if magic == b'\xd4\xc3\xb2\xa1':
        endian = '<'
    elif magic == b'\xa1\xb2\xc3\xd4':
        endian = '>'
    else:
        print(f"Unknown PCAP magic: {magic.hex()}")
        endian = '<'

    # Link-layer header type is at bytes 20:24
    link_type = struct.unpack(endian + 'I', pcap_data[20:24])[0]
    print(f"PCAP Link-layer header type (DLT): {link_type}")

    offset = 24
    packets = []
    while offset + 16 <= len(pcap_data):
        hdr = pcap_data[offset:offset+16]
        ts_sec, ts_usec, incl_len, orig_len = struct.unpack(endian + 'IIII', hdr)
        offset += 16
        if offset + incl_len > len(pcap_data):
            break
        pkt_bytes = pcap_data[offset:offset+incl_len]
        offset += incl_len
        packets.append((ts_sec + ts_usec / 1e6, pkt_bytes))

    print(f"Total Raw Packets Captured: {len(packets)}")

    sip_messages = []
    rtp_packets = []

    for ts, raw in packets:
        ip_offset = None
        if link_type == 113:
            ip_offset = 16
        elif link_type == 276:
            ip_offset = 20
        elif link_type == 1:
            ip_offset = 14
        else:
            for trial in [16, 20, 14, 0]:
                if len(raw) > trial + 20 and (raw[trial] & 0xF0) == 0x40 and (raw[trial] & 0x0F) >= 5:
                    ip_offset = trial
                    break

        if ip_offset is None or len(raw) < ip_offset + 20:
            continue

        ip_hdr = raw[ip_offset:ip_offset+20]
        ver_ihl = ip_hdr[0]
        if (ver_ihl >> 4) != 4:
            continue
        ihl = (ver_ihl & 0x0F) * 4
        proto = ip_hdr[9]
        src_ip = socket.inet_ntoa(ip_hdr[12:16])
        dst_ip = socket.inet_ntoa(ip_hdr[16:20])

        if proto != 17:  # UDP only
            continue

        udp_offset = ip_offset + ihl
        if len(raw) < udp_offset + 8:
            continue

        udp_hdr = raw[udp_offset:udp_offset+8]
        src_port, dst_port, udp_len, udp_cksum = struct.unpack('!HHHH', udp_hdr)
        payload = raw[udp_offset+8:udp_offset+udp_len]

        if not payload:
            continue

        # Check for SIP text (port 5060 or SIP keywords)
        if src_port == 5060 or dst_port == 5060 or any(payload.startswith(sig) for sig in [b'INVITE', b'SIP/2.0', b'ACK', b'BYE', b'CANCEL']):
            sip_messages.append((ts, src_ip, src_port, dst_ip, dst_port, payload.decode(errors='ignore')))
        elif (src_port in range(10000, 25000) or dst_port in range(10000, 25000)):
            # RTP packet
            if len(payload) >= 12 and (payload[0] & 0xC0) == 0x80:
                v = payload[0] >> 6
                p = (payload[0] >> 5) & 1
                x = (payload[0] >> 4) & 1
                cc = payload[0] & 0x0F
                m = (payload[1] >> 7) & 1
                pt = payload[1] & 0x7F
                seq = struct.unpack('!H', payload[2:4])[0]
                rtp_ts = struct.unpack('!I', payload[4:8])[0]
                ssrc = struct.unpack('!I', payload[8:12])[0]
                audio_payload = payload[12+cc*4:]
                rtp_packets.append({
                    "ts": ts,
                    "src_ip": src_ip,
                    "src_port": src_port,
                    "dst_ip": dst_ip,
                    "dst_port": dst_port,
                    "pt": pt,
                    "m": m,
                    "seq": seq,
                    "rtp_ts": rtp_ts,
                    "ssrc": ssrc,
                    "payload_len": len(audio_payload),
                    "first_bytes": audio_payload[:16].hex() if audio_payload else ""
                })

    print(f"\n--- 1. SIP SIGNALING & SDP NEGOTIATION ---")
    for ts, s_ip, s_pt, d_ip, d_pt, body in sip_messages:
        lines = body.split('\r\n')
        first_line = lines[0]
        if any(k in first_line for k in ['INVITE', '183', '200 OK', 'BYE']):
            print(f"\n>>> [{ts:.3f}] {s_ip}:{s_pt} -> {d_ip}:{d_pt} : {first_line}")
            for h in lines:
                if any(h.startswith(p) for p in ['From:', 'To:', 'Contact:', 'c=IN', 'm=audio', 'a=rtpmap', 'a=sendrecv', 'a=direction']):
                    print(f"    {h}")

    print(f"\n--- 2. RTP STREAM DIRECTION & SOCKET MAPPING ---")
    outbound_rtp = [p for p in rtp_packets if p["src_ip"] == "72.60.202.148"]
    inbound_rtp = [p for p in rtp_packets if p["dst_ip"] == "72.60.202.148"]

    print(f"RTP Packets Sent (Asterisk -> Carrier):     {len(outbound_rtp)}")
    print(f"RTP Packets Received (Carrier -> Asterisk): {len(inbound_rtp)}")

    if outbound_rtp:
        src_map = set((p["src_ip"], p["src_port"]) for p in outbound_rtp)
        dst_map = set((p["dst_ip"], p["dst_port"]) for p in outbound_rtp)
        pts = set(p["pt"] for p in outbound_rtp)
        print(f"  Asterisk Source Sockets:       {src_map}")
        print(f"  Target Carrier Sockets:        {dst_map}")
        print(f"  Outbound Payload Types:        {pts}")
        print(f"  Sample Outbound Frames (First 5):")
        for p in outbound_rtp[:5]:
            print(f"    PT={p['pt']} Seq={p['seq']} Len={p['payload_len']} Bytes={p['first_bytes']}")

    if inbound_rtp:
        src_map = set((p["src_ip"], p["src_port"]) for p in inbound_rtp)
        dst_map = set((p["dst_ip"], p["dst_port"]) for p in inbound_rtp)
        pts = set(p["pt"] for p in inbound_rtp)
        print(f"  Carrier Inbound Sockets:       {src_map}")
        print(f"  Asterisk Destination Sockets:  {dst_map}")
        print(f"  Inbound Payload Types:         {pts}")
        print(f"  Sample Inbound Frames (First 5):")
        for p in inbound_rtp[:5]:
            print(f"    PT={p['pt']} Seq={p['seq']} Len={p['payload_len']} Bytes={p['first_bytes']}")

    # Critical Cross-Check: Symmetric Port Match
    if outbound_rtp and inbound_rtp:
        ast_out_dst = list(dst_map)[0]
        carrier_in_src = list(src_map)[0]
        print(f"\n--- 3. SYMMETRIC RTP / NAT PINHOLE VERIFICATION ---")
        print(f"  Asterisk is transmitting RTP to:       {ast_out_dst[0]}:{ast_out_dst[1]}")
        print(f"  Carrier is transmitting RTP from:      {carrier_in_src[0]}:{carrier_in_src[1]}")
        if ast_out_dst == carrier_in_src:
            print("  [OK] Symmetric Port MATCHES: Asterisk is sending to the exact IP:port the carrier sends from.")
        else:
            print(f"  [CRITICAL MISMATCH] Port Mismatch detected!")
            print(f"  Asterisk is sending to {ast_out_dst}, but carrier sends from {carrier_in_src}!")
            print(f"  If carrier SBC requires symmetric RTP, all outgoing Asterisk audio is DROPPED by carrier firewall!")

def parse_log(log_path):
    import os
    if not os.path.exists(log_path):
        return
    print(f"\n--- 4. ASTERISK CONSOLE LOG HIGHLIGHTS ---")
    with open(log_path, "r", errors="ignore") as f:
        lines = f.readlines()
    for l in lines:
        if any(k in l for k in ["Playback", "Playing", "AudioSocket", "format", "translate", "codec", "res_rtp", "dropping", "error", "warning"]):
            print(f"  {l.strip()[:140]}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 inspect_trace.py <pcap_path> [log_path]")
        sys.exit(1)
    parse_pcap(sys.argv[1])
    if len(sys.argv) > 2:
        parse_log(sys.argv[2])
