CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_sessions_active_merchant
ON payment_sessions(merchant_id)
WHERE status IN ('awaiting_amount', 'processing');
