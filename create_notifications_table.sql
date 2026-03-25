-- Travint.ai: Notifications System
-- Internal notification feed - no emails needed

-- Create notifications table
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    country_id UUID REFERENCES countries(id),
    identity_layer TEXT NOT NULL,
    notification_type TEXT NOT NULL,  -- 'level_change', 'new_analysis', 'significant_event'
    old_value TEXT,
    new_value TEXT,
    message TEXT NOT NULL,
    severity TEXT NOT NULL,  -- 'info', 'warning', 'critical'
    read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast queries
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_country ON notifications(country_id);

-- Example: View unread notifications
-- SELECT * FROM notifications WHERE read = FALSE ORDER BY created_at DESC;

-- Example: Mark as read
-- UPDATE notifications SET read = TRUE WHERE id = 'notification-uuid';

-- Example: Get notifications for specific country
-- SELECT n.*, c.name as country_name 
-- FROM notifications n 
-- JOIN countries c ON n.country_id = c.id 
-- WHERE c.name = 'Iran' 
-- ORDER BY n.created_at DESC;
