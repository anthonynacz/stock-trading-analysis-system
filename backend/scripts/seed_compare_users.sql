-- Two test users with deliberately contrasting risk + signal-weight profiles.
-- Idempotent: re-running updates existing rows in place.
--
-- USER 1: cautious@vela.io  — STARTER tier, conservative profile
--         Heavy on drawdown / value / greeks / earnings; light on
--         options-flow / candlestick / momentum-tech. Defensive industries.
--
-- USER 2: momentum@vela.io  — PRO tier, aggressive profile
--         Heavy on technical / options-flow / reversal / OHLC; light on
--         drawdown / value. AI-sector tilt, PRO-tier alerts enabled.

BEGIN;

-- ── USER 1: cautious@vela.io ────────────────────────────────────────────────
INSERT INTO users (email, provider, provider_user_id, role, is_active)
VALUES ('cautious@vela.io', 'vela-dev', 'dev:cautious@vela.io', 'USER', TRUE)
ON CONFLICT (email) DO UPDATE
SET provider = EXCLUDED.provider,
    provider_user_id = EXCLUDED.provider_user_id,
    is_active = TRUE;

INSERT INTO subscriptions (user_id, tier, status, started_at)
SELECT id, 'STARTER', 'ACTIVE', NOW()
FROM users WHERE email = 'cautious@vela.io'
ON CONFLICT (user_id) DO UPDATE
SET tier = EXCLUDED.tier, status = EXCLUDED.status;

INSERT INTO user_preferences (
    user_id, risk_profile,
    signal_group_weights, industry_weights,
    custom_universe, alerts_config, digest_config
)
SELECT
    u.id,
    'conservative',
    json_build_object(
        'analyst',           1.2,
        'earnings',          1.2,
        'technical',         0.7,
        'drawdown',          1.6,
        'reversal',          1.4,
        'value',             1.5,
        'relative_strength', 1.0,
        'ohlc',              0.6,
        'options_flow',      0.5,
        'greeks',            1.4,
        'news_sentiment',    1.1,
        'geopolitical',      0.8,
        'insider',           1.0
    )::json,
    json_build_object(
        'AI/Semiconductors',          0.7,
        'Fintech/Payments',           0.8,
        'Energy/Commodities',         1.1,
        'Healthcare/Biotech',         1.4,
        'Consumer/Cloud/Enterprise',  1.2,
        'Industrials/Defense',        1.3,
        'Power/Utilities/Nuclear',    1.3,
        'Communications/Media',       1.0
    )::json,
    '[]'::json,
    json_build_object(
        'channel',             'email',
        'rec_change',          TRUE,
        'conviction_breach',   FALSE,
        'conviction_threshold', 60,
        'earnings_proximity',  TRUE,
        'earnings_days_before', 5,
        'unusual_flow',        FALSE,
        'news_spike',          FALSE,
        'insider_filing',      FALSE,
        'discord_webhook_url', NULL
    )::json,
    json_build_object(
        'enabled',                   TRUE,
        'send_time_utc',             '11:00',
        'include_top_picks',         TRUE,
        'include_position_health',   TRUE,
        'include_catalysts_3d',      TRUE,
        'include_industry_summary',  FALSE
    )::json
FROM users u WHERE u.email = 'cautious@vela.io'
ON CONFLICT (user_id) DO UPDATE
SET risk_profile         = EXCLUDED.risk_profile,
    signal_group_weights = EXCLUDED.signal_group_weights,
    industry_weights     = EXCLUDED.industry_weights,
    alerts_config        = EXCLUDED.alerts_config,
    digest_config        = EXCLUDED.digest_config,
    updated_at           = NOW();


-- ── USER 2: momentum@vela.io ────────────────────────────────────────────────
INSERT INTO users (email, provider, provider_user_id, role, is_active)
VALUES ('momentum@vela.io', 'vela-dev', 'dev:momentum@vela.io', 'USER', TRUE)
ON CONFLICT (email) DO UPDATE
SET provider = EXCLUDED.provider,
    provider_user_id = EXCLUDED.provider_user_id,
    is_active = TRUE;

INSERT INTO subscriptions (user_id, tier, status, started_at)
SELECT id, 'PRO', 'ACTIVE', NOW()
FROM users WHERE email = 'momentum@vela.io'
ON CONFLICT (user_id) DO UPDATE
SET tier = EXCLUDED.tier, status = EXCLUDED.status;

INSERT INTO user_preferences (
    user_id, risk_profile,
    signal_group_weights, industry_weights,
    custom_universe, alerts_config, digest_config
)
SELECT
    u.id,
    'aggressive',
    json_build_object(
        'analyst',           1.2,
        'earnings',          1.2,
        'technical',         1.6,
        'drawdown',          0.6,
        'reversal',          1.4,
        'value',             0.5,
        'relative_strength', 1.4,
        'ohlc',              1.4,
        'options_flow',      1.7,
        'greeks',            0.8,
        'news_sentiment',    1.1,
        'geopolitical',      1.2,
        'insider',           1.0
    )::json,
    json_build_object(
        'AI/Semiconductors',          1.6,
        'Fintech/Payments',           1.4,
        'Energy/Commodities',         0.9,
        'Healthcare/Biotech',         0.6,
        'Consumer/Cloud/Enterprise',  1.2,
        'Industrials/Defense',        0.8,
        'Power/Utilities/Nuclear',    1.5,
        'Communications/Media',       1.2
    )::json,
    '[]'::json,
    json_build_object(
        'channel',             'email',
        'rec_change',          TRUE,
        'conviction_breach',   TRUE,
        'conviction_threshold', 50,
        'earnings_proximity',  TRUE,
        'earnings_days_before', 3,
        'unusual_flow',        TRUE,
        'news_spike',          TRUE,
        'insider_filing',      TRUE,
        'discord_webhook_url', NULL
    )::json,
    json_build_object(
        'enabled',                   TRUE,
        'send_time_utc',             '11:00',
        'include_top_picks',         TRUE,
        'include_position_health',   TRUE,
        'include_catalysts_3d',      TRUE,
        'include_industry_summary',  TRUE
    )::json
FROM users u WHERE u.email = 'momentum@vela.io'
ON CONFLICT (user_id) DO UPDATE
SET risk_profile         = EXCLUDED.risk_profile,
    signal_group_weights = EXCLUDED.signal_group_weights,
    industry_weights     = EXCLUDED.industry_weights,
    alerts_config        = EXCLUDED.alerts_config,
    digest_config        = EXCLUDED.digest_config,
    updated_at           = NOW();

COMMIT;

-- Verification
SELECT u.id, u.email, s.tier, p.risk_profile
FROM users u
LEFT JOIN subscriptions s ON s.user_id = u.id
LEFT JOIN user_preferences p ON p.user_id = u.id
WHERE u.email IN ('cautious@vela.io', 'momentum@vela.io')
ORDER BY u.email;
