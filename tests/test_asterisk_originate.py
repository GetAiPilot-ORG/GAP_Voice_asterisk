import pytest
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure backend directory is in sys.path
backend_dir = str(Path(__file__).resolve().parent.parent / "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.services.asterisk_originate_service import (
    AsteriskOriginateService,
    OriginateResult,
)


def test_originate_result_model():
    res = OriginateResult(
        success=True,
        mode_used="ami",
        channel="PJSIP/09343418163@provider-trunk",
        call_uuid="test-uuid-1234",
        masked_number="+91******63",
    )
    assert res.success is True
    assert res.mode_used == "ami"
    assert "91" in res.masked_number


@pytest.mark.asyncio
async def test_ami_block_reader():
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.readline.side_effect = [
        b"Response: Success\r\n",
        b"Message: Authentication accepted\r\n",
        b"\r\n",
    ]
    headers = await AsteriskOriginateService._read_ami_block(reader)
    assert headers.get("response") == "Success"
    assert headers.get("message") == "Authentication accepted"


@pytest.mark.asyncio
async def test_ami_originate_success():
    reader = AsyncMock(spec=asyncio.StreamReader)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.wait_closed = AsyncMock()
    writer.drain = AsyncMock()

    # Banner, Login response, Originate response
    reader.readline.side_effect = [
        b"Asterisk Call Manager/5.0.4\r\n",
        b"Response: Success\r\n",
        b"Message: Authentication accepted\r\n",
        b"\r\n",
        b"Response: Success\r\n",
        b"Message: Originate successfully queued\r\n",
        b"\r\n",
    ]

    with patch("asyncio.open_connection", return_value=(reader, writer)):
        res = await AsteriskOriginateService.originate_via_ami(
            dial_number="09343418163",
            call_uuid="test-uuid-1234",
            caller_id="911168140253",
            trunk_id="10ca861d-d002-4f52-8b2a-fbefea08a6de",
            carrier_domain="cpbx.24ithub.com",
        )

        assert res.success is True
        assert res.mode_used == "ami"
        assert "PJSIP/09343418163" in res.channel

        # Verify AMI command written to socket contains PAI and CallerID
        calls = writer.write.call_args_list
        written_data = "".join(call[0][0].decode("utf-8") for call in calls)
        assert "Action: Originate" in written_data
        assert 'CallerID: "911168140253" <911168140253>' in written_data
        assert "PJSIP_HEADER(add,P-Asserted-Identity)=<sip:911168140253@cpbx.24ithub.com>" in written_data
        assert "PJSIP_HEADER(add,Remote-Party-ID)=" in written_data


@pytest.mark.asyncio
async def test_callfile_originate_success(tmp_path):
    spool_dir = tmp_path / "outgoing"
    spool_dir.mkdir()

    with patch("app.core.config.settings.asterisk_spool_outgoing_dir", str(spool_dir)):
        res = await AsteriskOriginateService.originate_via_callfile(
            dial_number="09343418163",
            call_uuid="test-uuid-callfile-999",
            caller_id="911168140253",
            trunk_id="10ca861d",
            carrier_domain="cpbx.24ithub.com",
            timeout_seconds=30,
        )

        assert res.success is True
        assert res.mode_used == "callfile"

        # Verify callfile exists and contents
        created_file = spool_dir / "vp_test-uuid-callfile-999.call"
        assert created_file.exists()
        content = created_file.read_text()
        assert "Channel: PJSIP/09343418163@provider-10ca861d" in content
        assert 'CallerID: "911168140253" <911168140253>' in content
        assert "Extension: test-uuid-callfile-999" in content
        assert "PJSIP_HEADER(add,P-Asserted-Identity)=<sip:911168140253@cpbx.24ithub.com>" in content


@pytest.mark.asyncio
async def test_originate_fallback_chain():
    # If AMI fails, it must fallback to callfile
    with patch.object(
        AsteriskOriginateService,
        "originate_via_ami",
        return_value=OriginateResult(
            success=False,
            mode_used="ami",
            channel="",
            call_uuid="test-uuid",
            masked_number="*",
            error="Connection refused",
        ),
    ):
        with patch.object(
            AsteriskOriginateService,
            "originate_via_callfile",
            return_value=OriginateResult(
                success=True,
                mode_used="callfile",
                channel="PJSIP/...",
                call_uuid="test-uuid",
                masked_number="*",
            ),
        ) as mock_callfile:
            res = await AsteriskOriginateService.originate_call(
                dial_number="09343418163",
                call_uuid="test-uuid",
                caller_id="911168140253",
                trunk_id="10ca861d",
                mode="ami",
            )
            assert res.success is True
            assert res.mode_used == "callfile"
            mock_callfile.assert_called_once()


def test_inbound_dialplan_untouched():
    """Safety check: confirm that from-provider-24ithub context remains intact."""
    extensions_conf_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "deploy", "asterisk", "extensions.conf")
    )
    with open(extensions_conf_path, "r") as f:
        content = f.read()

    assert "[from-provider-24ithub]" in content
    assert "exten => _[+0-9].,1,NoOp(=== INBOUND PSTN CALL DETECTED ON TRUNK ===)" in content
    assert "AudioSocket(${CALL_UUID},127.0.0.1:${AUDIOSOCKET_PORT})" in content
