import os
import shutil
import tempfile
import asyncio
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel

from app.core.config import settings
from app.utils.audio_telephony import mask_phone_number
from app.services.asterisk_cli import execute_asterisk_cli_cmd

logger = logging.getLogger("asterisk.originate")


class OriginateResult(BaseModel):
    success: bool
    mode_used: str  # "ami" | "callfile" | "cli_local"
    channel: str
    call_uuid: str
    masked_number: str
    error: Optional[str] = None
    details: Dict[str, Any] = {}


class AsteriskOriginateService:
    """
    Production-grade Asterisk Outbound Origination Engine.
    Supports:
      1. Pure async Asterisk Manager Interface (AMI) direct origination on PJSIP
      2. High-reliability atomic Spool Callfile origination on PJSIP
      3. Backward-compatible Local Channel CLI origination (Rollback mode)
    """

    @classmethod
    async def _read_ami_block(cls, reader: asyncio.StreamReader, timeout: float = 5.0) -> Dict[str, str]:
        """Reads a CRLF-CRLF terminated block of key-value pairs from AMI socket."""
        block_text = ""
        while True:
            line_bytes = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore")
            if line in ("\r\n", "\n", ""):
                if block_text.strip():
                    break
                else:
                    continue
            block_text += line

        headers: Dict[str, str] = {}
        for l in block_text.splitlines():
            if ":" in l:
                k, v = l.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return headers

    @classmethod
    async def originate_via_ami(
        cls,
        dial_number: str,
        call_uuid: str,
        caller_id: str,
        trunk_id: str,
        carrier_domain: str,
        context: str = "outbound-ai-answered",
        priority: int = 1,
        timeout: float = 10.0,
    ) -> OriginateResult:
        """Originate call directly on PJSIP trunk channel via Asterisk Manager Interface (AMI)."""
        masked = mask_phone_number(dial_number)
        channel = f"PJSIP/{dial_number}@provider-{trunk_id}"
        formatted_cli = f'"{caller_id}" <{caller_id}>'
        
        logger.info(
            f"[AMI Originate] Attempting direct PJSIP originate for call {call_uuid} "
            f"to {masked} via trunk provider-{trunk_id}"
        )

        reader = None
        writer = None
        try:
            # 1. Connect to Asterisk AMI TCP Socket
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(settings.asterisk_ami_host, settings.asterisk_ami_port),
                timeout=timeout,
            )

            # 2. Read banner (e.g., Asterisk Call Manager/5.0.4)
            banner = await asyncio.wait_for(reader.readline(), timeout=timeout)
            logger.debug(f"[AMI Originate] Banner received: {banner.decode(errors='ignore').strip()}")

            # 3. Authenticate with AMI
            login_cmd = (
                f"Action: Login\r\n"
                f"Username: {settings.asterisk_ami_user}\r\n"
                f"Secret: {settings.asterisk_ami_secret}\r\n"
                f"\r\n"
            )
            writer.write(login_cmd.encode("utf-8"))
            await writer.drain()

            login_resp = await cls._read_ami_block(reader, timeout=timeout)
            if login_resp.get("response", "").lower() != "success":
                err_msg = login_resp.get("message", "AMI Authentication Failed")
                raise RuntimeError(f"AMI Login rejected: {err_msg}")

            # 4. Transmit Originate Action
            # Injects verified PAI and RPID headers directly on the newly created PJSIP channel
            orig_cmd = (
                f"Action: Originate\r\n"
                f"Channel: {channel}\r\n"
                f"Context: {context}\r\n"
                f"Exten: {call_uuid}\r\n"
                f"Priority: {priority}\r\n"
                f"CallerID: {formatted_cli}\r\n"
                f"Variable: CALLERID(pres)=allowed\r\n"
                f"Variable: PJSIP_HEADER(add,P-Asserted-Identity)=<sip:{caller_id}@{carrier_domain}>\r\n"
                f"Variable: PJSIP_HEADER(add,Remote-Party-ID)=<sip:{caller_id}@{carrier_domain}>;party=calling;privacy=off;screen=no\r\n"
                f"Async: true\r\n"
                f"\r\n"
            )
            writer.write(orig_cmd.encode("utf-8"))
            await writer.drain()

            orig_resp = await cls._read_ami_block(reader, timeout=timeout)
            resp_status = orig_resp.get("response", "").lower()
            if resp_status != "success":
                err_msg = orig_resp.get("message", f"AMI Originate rejected: {orig_resp}")
                raise RuntimeError(f"AMI Originate failure: {err_msg}")

            # 5. Logoff cleanly
            try:
                writer.write(b"Action: Logoff\r\n\r\n")
                await writer.drain()
            except Exception:
                pass

            logger.info(
                f"[AMI Originate] Successfully queued direct PJSIP originate for call {call_uuid} "
                f"to {masked} (Response: {orig_resp.get('message', 'Queued')})"
            )
            return OriginateResult(
                success=True,
                mode_used="ami",
                channel=channel,
                call_uuid=call_uuid,
                masked_number=masked,
                details=orig_resp,
            )

        except Exception as e:
            logger.warning(f"[AMI Originate] Failed for {call_uuid}: {e}")
            return OriginateResult(
                success=False,
                mode_used="ami",
                channel=channel,
                call_uuid=call_uuid,
                masked_number=masked,
                error=str(e),
            )
        finally:
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

    @classmethod
    async def originate_via_callfile(
        cls,
        dial_number: str,
        call_uuid: str,
        caller_id: str,
        trunk_id: str,
        carrier_domain: str,
        context: str = "outbound-ai-answered",
        priority: int = 1,
        timeout_seconds: int = 30,
    ) -> OriginateResult:
        """Originate call directly on PJSIP trunk via atomic Asterisk Spool Callfile."""
        masked = mask_phone_number(dial_number)
        channel = f"PJSIP/{dial_number}@provider-{trunk_id}"
        formatted_cli = f'"{caller_id}" <{caller_id}>'
        spool_dir = settings.asterisk_spool_outgoing_dir

        logger.info(
            f"[Callfile Originate] Preparing atomic call file for call {call_uuid} "
            f"to {masked} via trunk provider-{trunk_id}"
        )

        callfile_content = (
            f"Channel: {channel}\n"
            f"CallerID: {formatted_cli}\n"
            f"Context: {context}\n"
            f"Extension: {call_uuid}\n"
            f"Priority: {priority}\n"
            f"MaxRetries: 0\n"
            f"RetryTime: 60\n"
            f"WaitTime: {timeout_seconds}\n"
            f"Set: CALLERID(pres)=allowed\n"
            f"Set: PJSIP_HEADER(add,P-Asserted-Identity)=<sip:{caller_id}@{carrier_domain}>\n"
            f"Set: PJSIP_HEADER(add,Remote-Party-ID)=<sip:{caller_id}@{carrier_domain}>;party=calling;privacy=off;screen=no\n"
        )

        def _write_and_move():
            os.makedirs(spool_dir, exist_ok=True)
            tmp_dir = "/tmp" if os.path.exists("/tmp") else tempfile.gettempdir()
            # Write to temporary file first to guarantee atomic delivery
            with tempfile.NamedTemporaryFile("w", dir=tmp_dir, delete=False, prefix=f"vp_{call_uuid}_", suffix=".call") as tf:
                tf.write(callfile_content)
                temp_path = tf.name

            os.chmod(temp_path, 0o666)
            dest_path = os.path.join(spool_dir, f"vp_{call_uuid}.call")
            shutil.move(temp_path, dest_path)
            return dest_path

        try:
            dest_file = await asyncio.to_thread(_write_and_move)
            logger.info(f"[Callfile Originate] Successfully delivered callfile {dest_file} for call {call_uuid}")
            return OriginateResult(
                success=True,
                mode_used="callfile",
                channel=channel,
                call_uuid=call_uuid,
                masked_number=masked,
                details={"callfile": dest_file},
            )
        except Exception as e:
            logger.error(f"[Callfile Originate] Failed to write/move callfile for {call_uuid}: {e}", exc_info=True)
            return OriginateResult(
                success=False,
                mode_used="callfile",
                channel=channel,
                call_uuid=call_uuid,
                masked_number=masked,
                error=str(e),
            )

    @classmethod
    def originate_via_cli_local(
        cls,
        dial_number: str,
        call_uuid: str,
    ) -> OriginateResult:
        """Rollback mode: originates via Asterisk CLI using Local channel."""
        masked = mask_phone_number(dial_number)
        channel = f"Local/{dial_number}@outbound-dial-carrier"
        orig_cmd = f"channel originate {channel} extension {call_uuid}@outbound-ai-answered"
        
        logger.warning(
            f"[CLI Local Originate] Executing fallback/rollback Local channel originate for {call_uuid} to {masked}"
        )

        res = execute_asterisk_cli_cmd(orig_cmd)
        if res.get("returncode") == 0:
            return OriginateResult(
                success=True,
                mode_used="cli_local",
                channel=channel,
                call_uuid=call_uuid,
                masked_number=masked,
                details=res,
            )
        else:
            err = res.get("stderr") or res.get("stdout") or "Unknown CLI failure"
            return OriginateResult(
                success=False,
                mode_used="cli_local",
                channel=channel,
                call_uuid=call_uuid,
                masked_number=masked,
                error=err,
                details=res,
            )

    @classmethod
    async def originate_call(
        cls,
        dial_number: str,
        call_uuid: str,
        caller_id: str,
        trunk_id: str,
        carrier_domain: Optional[str] = None,
        context: str = "outbound-ai-answered",
        priority: int = 1,
        timeout_seconds: int = 30,
        mode: Optional[str] = None,
    ) -> OriginateResult:
        """
        Unified dispatch point for outbound call origination.
        Tries primary configured mode (default: ami), with automatic safe fallback.
        """
        chosen_mode = (mode or getattr(settings, "outbound_originate_mode", "ami")).lower()
        domain = carrier_domain or getattr(settings, "asterisk_default_carrier_domain", "cpbx.24ithub.com")

        # 1. Direct AMI Mode
        if chosen_mode == "ami":
            res = await cls.originate_via_ami(
                dial_number=dial_number,
                call_uuid=call_uuid,
                caller_id=caller_id,
                trunk_id=trunk_id,
                carrier_domain=domain,
                context=context,
                priority=priority,
                timeout=10.0,
            )
            if res.success:
                return res
            logger.warning(f"[Originate Dispatch] AMI failed ({res.error}). Falling back to Callfile mode.")
            # Fallback to Callfile
            chosen_mode = "callfile"

        # 2. Spool Callfile Mode
        if chosen_mode == "callfile":
            res = await cls.originate_via_callfile(
                dial_number=dial_number,
                call_uuid=call_uuid,
                caller_id=caller_id,
                trunk_id=trunk_id,
                carrier_domain=domain,
                context=context,
                priority=priority,
                timeout_seconds=timeout_seconds,
            )
            if res.success:
                return res
            logger.warning(f"[Originate Dispatch] Callfile failed ({res.error}). Falling back to CLI Local mode.")
            # Fallback to CLI Local
            chosen_mode = "cli_local"

        # 3. CLI Local Rollback Mode
        return cls.originate_via_cli_local(
            dial_number=dial_number,
            call_uuid=call_uuid,
        )


originate_service = AsteriskOriginateService()
