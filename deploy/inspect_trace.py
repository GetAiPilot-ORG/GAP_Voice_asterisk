#!/usr/bin/env python3
"""
Deep RTP and SIP Diagnostics for Asterisk Outbound Audio Path.
Parses PCAP capture, calculates exact RTP audio volume/RMS, and checks sound files.
"""
import sys
import struct
import socket
import math
import os

def ulaw_to_linear(u_byte):
    """Convert a single 8-bit ITU-T G.711 mu-law byte to 16-bit signed linear PCM sample."""
    u = ~u_byte & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample

def alaw_to_linear(a_byte):
    """Convert a single 8-bit ITU-T G.711 A-law byte to 16-bit signed linear PCM sample."""
    a = a_byte ^ 0x55
    sign = a & 0x80
    exponent = (a >> 4) & 0x07
    mantissa = a & 0x0F
    if exponent == 0:
        sample = (mantissa << 4) + 8
    else:
        sample = ((mantissa << 4) + 0x108) << (exponent - 1)
    return -sample if sign else sample

def calculate_packet_rms(payload_bytes, pt):
    """Calculate RMS volume of an RTP payload."""
    if not payload_bytes:
        return 0.0
    samples = []
    if pt == 0:  # PCMU
        samples = [ulaw_to_linear(b) for b in payload_bytes]
    elif pt == 8:  # PCMA
        samples = [alaw_to_linear(b) for b in payload_bytes]
    else:
        return 0.0

    if not samples:
        return 0.0
    sum_squares = sum(s * s for s in samples)
    return math.sqrt(sum_squares / len(samples))

def parse_pcap(pcap_path):
    print(f"==================================================================")
    print(f"  DEEP TELEPHONY & AUDIO ENERGY ANALYSIS: {pcap_path}")
    print(f"==================================================================")
    if not os.path.exists(pcap_path):
        print(f"ERROR: PCAP file not found: {pcap_path}")
        return

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
        endian = '<'

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

        # SIP message
        if src_port == 5060 or dst_port == 5060 or any(payload.startswith(sig) for sig in [b'INVITE', b'SIP/2.0', b'ACK', b'BYE', b'CANCEL']):
            sip_messages.append((ts, src_ip, src_port, dst_ip, dst_port, payload.decode(errors='ignore')))
        elif (src_port in range(10000, 25000) or dst_port in range(10000, 25000)):
            # RTP packet
            if len(payload) >= 12 and (payload[0] & 0xC0) == 0x80:
                pt = payload[1] & 0x7F
                cc = payload[0] & 0x0F
                seq = struct.unpack('!H', payload[2:4])[0]
                rtp_ts = struct.unpack('!I', payload[4:8])[0]
                audio_payload = payload[12+cc*4:]
                rms = calculate_packet_rms(audio_payload, pt) if pt in (0, 8) else 0.0
                rtp_packets.append({
                    "ts": ts,
                    "src_ip": src_ip,
                    "src_port": src_port,
                    "dst_ip": dst_ip,
                    "dst_port": dst_port,
                    "pt": pt,
                    "seq": seq,
                    "rtp_ts": rtp_ts,
                    "payload_len": len(audio_payload),
                    "rms": rms,
                    "first_bytes": audio_payload[:16].hex() if audio_payload else ""
                })

    print(f"\n--- 1. SIP SIGNALING & SDP NEGOTIATION ---")
    for ts, s_ip, s_pt, d_ip, d_pt, body in sip_messages:
        lines = body.split('\r\n')
        first_line = lines[0]
        if any(k in first_line for k in ['INVITE', '183', '200 OK', 'BYE']):
            print(f"\n>>> [{ts:.3f}] {s_ip}:{s_pt} -> {d_ip}:{d_pt} : {first_line}")
            for h in lines:
                if any(h.startswith(p) for p in ['From:', 'To:', 'Contact:', 'c=IN', 'm=audio', 'a=rtpmap', 'a=sendrecv']):
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

        # AUDIO ENERGY ANALYSIS
        audio_frames = [p for p in outbound_rtp if p["pt"] in (0, 8)]
        if audio_frames:
            max_rms = max(p["rms"] for p in audio_frames)
            avg_rms = sum(p["rms"] for p in audio_frames) / len(audio_frames)
            audible_frames = sum(1 for p in audio_frames if p["rms"] > 500.0)
            silent_frames = sum(1 for p in audio_frames if p["rms"] <= 50.0)

            print(f"\n--- 3. AUDIO ENERGY & VOLUME ANALYSIS ---")
            print(f"  Total Audio (G.711) Packets Sent: {len(audio_frames)}")
            print(f"  Maximum Audio RMS:                {max_rms:.1f}")
            print(f"  Average Audio RMS:                {avg_rms:.1f}")
            print(f"  Audible Frames (RMS > 500):       {audible_frames} ({audible_frames*100.0/len(audio_frames):.1f}%)")
            print(f"  Silent Frames  (RMS <= 50):       {silent_frames} ({silent_frames*100.0/len(audio_frames):.1f}%)")

            if max_rms < 100.0:
                print(f"  [CRITICAL ROOT CAUSE FOUND]")
                print(f"  -> Asterisk transmitted 100% DIGITAL SILENCE! (Max RMS: {max_rms:.1f})")
                print(f"  -> The audio file was missing or muted inside Asterisk, so Asterisk generated silent frames!")
            else:
                print(f"  [CONFIRMED LOUD AUDIO TRANSMITTED BY ASTERISK]")
                print(f"  -> Max RMS: {max_rms:.1f}, Audible Frames: {audible_frames}")
                print(f"  -> Asterisk transmitted clear audio to carrier socket {list(dst_map)[0]}.")
                print(f"  -> The silence on the handset is caused by Carrier One-Way Bearer or Codec mismatch at 202.89.78.2!")

    if inbound_rtp:
        print(f"  Carrier sent {len(inbound_rtp)} packets to Asterisk.")
    else:
        print(f"\n  [CARRIER RTP NOTE] Carrier sent 0 RTP packets back to Asterisk.")

def check_sound_files():
    print(f"\n--- 4. ASTERISK SOUND FILES VERIFICATION ---")
    sound_dirs = [
        "/var/lib/asterisk/sounds/en",
        "/var/lib/asterisk/sounds",
        "/usr/share/asterisk/sounds/en",
        "/usr/share/asterisk/sounds"
    ]
    found_any = False
    for d in sound_dirs:
        if os.path.exists(d):
            print(f"  Scanning directory: {d}")
            files = os.listdir(d)
            matches = [f for f in files if any(k in f for k in ["hello-world", "demo-congrats", "test-tone", "tt-monkeys"])]
            if matches:
                found_any = True
                for m in matches:
                    size = os.path.getsize(os.path.join(d, m))
                    print(f"    [FOUND] {m} ({size} bytes)")
    if not found_any:
        print("  [CRITICAL WARNING] Standard Asterisk sound files (hello-world, demo-congrats) NOT FOUND on system!")

if __name__ == "__main__":
    pcap = sys.argv[1] if len(sys.argv) > 1 else "/tmp/phase1_traces/e1_direct_playback.pcap"
    parse_pcap(pcap)
    check_sound_files()
