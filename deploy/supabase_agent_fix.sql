-- ==============================================================================
-- Supabase Agent & Trunk Synchronization
-- Aligns Agent 3c57e938-e485-4848-96d5-ff571d63a070 with Sarvam Bulbul v3 (Shreya)
-- ==============================================================================

UPDATE agents
SET 
    voice_id = 'shreya',
    voice_provider = 'sarvam',
    language = 'hi-IN',
    status = 'active',
    updated_at = NOW()
WHERE id = '3c57e938-e485-4848-96d5-ff571d63a070';

-- Ensure DID +911168140253 / 911168140253 is mapped to this agent and active
UPDATE did_numbers
SET 
    agent_id = '3c57e938-e485-4848-96d5-ff571d63a070',
    status = 'active',
    inbound_enabled = true,
    outbound_enabled = true,
    updated_at = NOW()
WHERE phone_number IN ('911168140253', '+911168140253');

-- Ensure SIP Trunk is active
UPDATE sip_trunk_providers
SET 
    status = 'active',
    outbound_caller_id = '911168140253',
    updated_at = NOW()
WHERE id = '10ca861d-d002-4f52-8b2a-fbefea08a6de';
