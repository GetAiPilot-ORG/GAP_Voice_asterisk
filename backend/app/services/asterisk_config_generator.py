import logging
from typing import Dict, Any, List, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

class AsteriskConfigGenerator:
    @staticmethod
    def generate_config(trunk: Dict[str, Any], mask_password: bool = True) -> Dict[str, str]:
        """
        Generate pjsip.conf, extensions.conf, firewall commands, and reload commands for a SIP Trunk.
        """
        trunk_id = trunk.get("id", "template-id")
        name = trunk.get("name", "SIP-Trunk")
        auth_type = trunk.get("auth_type", "ip_auth")
        sip_proxy = trunk.get("sip_proxy", "127.0.0.1")
        sip_port = trunk.get("sip_port", 5060)
        transport = trunk.get("transport", "udp")
        username = trunk.get("username", "")
        
        try:
            port_val = int(sip_port) if sip_port else 5060
        except ValueError:
            port_val = 5060
        port_suffix = f":{sip_port}" if port_val != 5060 else ""
        
        # Get password, check if it's already decrypted/plain-text
        password = trunk.get("password", "") or trunk.get("password_decrypted", "")
        if mask_password and password:
            password = "********"

        provider_ips = trunk.get("provider_ips") or []
        if isinstance(provider_ips, str):
            import json
            try:
                provider_ips = json.loads(provider_ips)
            except Exception:
                provider_ips = [provider_ips]

        outbound_caller_id = trunk.get("outbound_caller_id") or "911168140253"
        if outbound_caller_id.startswith("+"):
            outbound_caller_id = outbound_caller_id[1:]

        # Force codec safety: allow=ulaw,alaw only
        codecs_str = "ulaw,alaw"
        webhook_secret = settings.asterisk_webhook_secret or "your_shared_webhook_secret"
        transport_name = f"transport-{transport}"
 
        # 1. PJSIP config generation
        pjsip_lines = [
            f"; === SIP Trunk: {name} (ID: {trunk_id}) ==="
        ]
 
        if auth_type == "ip_auth":
            # IP Auth Endpoint
            pjsip_lines.extend([
                f"[provider-{trunk_id}-aor]",
                "type=aor",
                f"contact=sip:{sip_proxy}{port_suffix}",
                "",
                f"[provider-{trunk_id}]",
                "type=endpoint",
                f"transport={transport_name}",
                f"context=from-provider-{trunk_id}",
                "disallow=all",
                f"allow={codecs_str}",
                f"aors=provider-{trunk_id}-aor",
                "direct_media=no",
                "force_rport=yes",
                "rewrite_contact=yes",
                "rtp_symmetric=yes",
                "send_pai=yes",
                "send_rpid=yes",
                f"from_user={outbound_caller_id}",
                f"from_domain={sip_proxy}",
                f"callerid=\"{outbound_caller_id}\" <{outbound_caller_id}>",
                "timers=yes",
                "timers_min_se=90",
                "timers_sess_expires=1800",
                "",
                f"[provider-{trunk_id}-identify]",
                "type=identify",
                f"endpoint=provider-{trunk_id}",
            ])
            # Include configured provider IPs, ensuring carrier signaling and RTP gateway are covered
            all_ips = list(provider_ips)
            if sip_proxy not in all_ips and sip_proxy not in ("127.0.0.1", "localhost"):
                all_ips.append(sip_proxy)
            # 24IT Hub observed RTP gateway
            if "202.89.78.2" not in all_ips:
                all_ips.append("202.89.78.2")

            for ip in all_ips:
                if ip:
                    pjsip_lines.append(f"match={ip}")
            pjsip_lines.append("")
        else:
            # Username/Password Auth
            pjsip_lines.extend([
                f"[provider-{trunk_id}-auth]",
                "type=auth",
                "auth_type=userpass",
                f"username={username}",
                f"password={password}",
                "",
                f"[provider-{trunk_id}-aor]",
                "type=aor",
                f"contact=sip:{sip_proxy}{port_suffix}",
                "",
                f"[provider-{trunk_id}]",
                "type=endpoint",
                f"transport={transport_name}",
                f"context=from-provider-{trunk_id}",
                "disallow=all",
                f"allow={codecs_str}",
                f"outbound_auth=provider-{trunk_id}-auth",
                f"aors=provider-{trunk_id}-aor",
                f"from_user={username or outbound_caller_id}",
                f"from_domain={sip_proxy}",
                f"callerid=\"{outbound_caller_id}\" <{outbound_caller_id}>",
                "direct_media=no",
                "force_rport=yes",
                "rewrite_contact=yes",
                "rtp_symmetric=yes",
                "send_pai=yes",
                "send_rpid=yes",
                "",
                f"[provider-{trunk_id}-reg]",
                "type=registration",
                f"transport={transport_name}",
                f"outbound_auth=provider-{trunk_id}-auth",
                f"client_uri=sip:{username}@{sip_proxy}{port_suffix}",
                f"server_uri=sip:{sip_proxy}{port_suffix}",
                f"contact_user={username}",
                ""
            ])
 
        pjsip_conf = "\n".join(pjsip_lines)
 
        base_api_url = settings.public_base_url or f"http://127.0.0.1:{settings.api_port}"
        as_port = settings.asterisk_audiosocket_port or 9095
        # 2. Extensions config generation
        ext_lines = [
            f"; === Inbound Routing for Provider: {name} (ID: {trunk_id}) ===",
            f"[from-provider-{trunk_id}]",
            "",
            "; Unified Inbound Handler with dynamic DID detection and robust authorization",
            "exten => _.,1,NoOp(--- INBOUND CALL START ---)",
            " same => n,Set(RAW_CALLER=${CALLERID(num)})",
            " same => n,Set(RAW_EXTEN=${EXTEN})",
            " same => n,GotoIf($[\"${RAW_EXTEN}\" = \"s\" | \"${RAW_EXTEN}\" = \"\"]?extract_to:use_exten)",
            "",
            " same => n(extract_to),Set(SIP_TO=${PJSIP_HEADER(read,To)})",
            " same => n,Set(EXTRACTED_DID=${CUT(CUT(SIP_TO,@,1),:,2)})",
            " same => n,GotoIf($[\"${EXTRACTED_DID}\" != \"\"]?set_did:use_exten)",
            f" same => n(set_did),Set(DIALED_DID=${{EXTRACTED_DID}})",
            " same => n,Goto(did_ready)",
            "",
            " same => n(use_exten),Set(DIALED_DID=${RAW_EXTEN})",
            "",
            " same => n(did_ready),Set(CLEAN_DID=${FILTER(0123456789,${DIALED_DID})})",
            " same => n,Set(CLEAN_CALLER=${FILTER(0123456789,${RAW_CALLER})})",
            " same => n,NoOp(Inbound call detected: Caller=${CLEAN_CALLER}, DID=${CLEAN_DID})",
            " same => n,Set(CALL_UUID=${UNIQUEID})",
            f" same => n,Set(CALL_PERMISSION=${{CURL({base_api_url}/api/webhooks/asterisk/inbound?caller_id=${{CLEAN_CALLER}}&dialed_number=${{CLEAN_DID}}&call_uuid=${{CALL_UUID}}&provider=asterisk&secret={webhook_secret})}})",
            " same => n,NoOp(Inbound auth response: ${CALL_PERMISSION})",
            " same => n,GotoIf($[\"${CALL_PERMISSION:0:5}\"=\"ALLOW\"]?auth_ok:auth_fail)",
            "",
            " same => n(auth_fail),NoOp(Call rejected by backend: ${CALL_PERMISSION})",
            " same => n,Hangup(21)",
            "",
            " same => n(auth_ok),NoOp(Call authorized - Answering and bridging to AudioSocket)",
            " same => n,Set(BACKEND_UUID=${CALL_PERMISSION:6})",
            " same => n,GotoIf($[\"${BACKEND_UUID}\"!=\"\"]?set_b_uuid:keep_u)",
            " same => n(set_b_uuid),Set(CALL_UUID=${BACKEND_UUID})",
            " same => n(keep_u),Set(CHANNEL(hangup_handler_push)=call-hangup-handler,s,1(${CALL_UUID}))",
            " same => n,Answer()",
            f" same => n,AudioSocket(${{CALL_UUID}},127.0.0.1:{as_port})",
            " same => n,Hangup()",
            "",
            f"exten => h,1,NoOp(Channel hung up on provider-{trunk_id}: ${{HANGUPCAUSE}})",
            "",
            "; === Outbound Answer-Gated Media Context ===",
            "[outbound-ai-answered]",
            "exten => _[0-9a-zA-Z-].,1,NoOp(--- OUTBOUND CALL ANSWERED: ${EXTEN} ---)",
            " same => n,Set(CALL_UUID=${EXTEN})",
            " same => n,Set(CHANNEL(hangup_handler_push)=call-hangup-handler,s,1(${CALL_UUID}))",
            " same => n,Answer()",
            f" same => n,Set(ACK_RESP=${{CURL({base_api_url}/api/webhooks/asterisk/answered?call_uuid=${{CALL_UUID}})}})",
            " same => n,NoOp(Answered webhook ack: ${ACK_RESP})",
            f" same => n,AudioSocket(${{CALL_UUID}},127.0.0.1:{as_port})",
            " same => n,Hangup()",
            "",
            "exten => s,1,NoOp(--- OUTBOUND CALL ANSWERED (s fallback) ---)",
            " same => n,GotoIf($[\"${CALL_UUID}\" != \"\"]?outbound-ai-answered,${CALL_UUID},1:no_uuid)",
            " same => n(no_uuid),NoOp(ERROR: Missing CALL_UUID on outbound answer)",
            " same => n,Hangup()",
            "",
            "; === Global Call Hangup Handler ===",
            "[call-hangup-handler]",
            "exten => s,1,NoOp(--- HANGUP HANDLER: UUID=${ARG1}, Cause=${HANGUPCAUSE}, Billsec=${CDR(billsec)} ---)",
            " same => n,Set(H_UUID=${ARG1})",
            " same => n,Set(H_CAUSE=${HANGUPCAUSE})",
            " same => n,Set(H_DUR=${CDR(billsec)})",
            " same => n,GotoIf($[\"${H_UUID}\" = \"\"]?handler_done)",
            f" same => n,Set(H_RES=${{CURL({base_api_url}/api/webhooks/asterisk/hangup?call_uuid=${{H_UUID}}&cause=${{H_CAUSE}}&duration=${{H_DUR}})}})",
            " same => n,NoOp(Hangup webhook result: ${H_RES})",
            " same => n(handler_done),Return()",
        ]
        extensions_conf = "\n".join(ext_lines)


        # 3. Suggested firewall rules
        fw_rules = [
            f"# 1. Allow SIP traffic from SIP proxy ({sip_proxy})",
            f"sudo ufw allow from {sip_proxy} to any port {sip_port} proto {transport}",
        ]
        if auth_type == "ip_auth":
            for ip in provider_ips:
                if ip:
                    fw_rules.append(f"sudo ufw allow from {ip} to any port {sip_port} proto {transport}")
        fw_rules.extend([
            "# 2. Allow RTP media ports (typical default Asterisk RTP range)",
            "sudo ufw allow 10000:20000/udp"
        ])
        firewall_commands = "\n".join(fw_rules)

        # 4. Asterisk reload commands
        reload_commands = (
            "asterisk -rx \"pjsip reload\"\n"
            "asterisk -rx \"dialplan reload\""
        )

        return {
            "pjsip_conf": pjsip_conf,
            "extensions_conf": extensions_conf,
            "firewall_commands": firewall_commands,
            "reload_commands": reload_commands
        }


def validate_asterisk_config_syntax(pjsip_text: str, ext_text: str) -> Optional[str]:
    """Basic Python-side syntax sanity checker for Asterisk config files."""
    from typing import Optional
    for name, text in [("PJSIP", pjsip_text), ("Extensions", ext_text)]:
        open_brackets = text.count("[")
        close_brackets = text.count("]")
        if open_brackets != close_brackets:
            return f"{name} configuration has bracket mismatch: {open_brackets} open vs {close_brackets} close brackets."
            
        if name == "Extensions":
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(";") and not line.startswith("["):
                    if not (line.startswith("exten") or line.startswith("same") or line.startswith("include") or line.startswith("switch")):
                        return f"Invalid line format in Extensions: '{line}'"
    return None
