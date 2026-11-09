import hashlib
import json

AUDIT_STATES = [('unknown', 'Not Verified'), ('pending', 'Under Review'), ('approved', 'Review Approved'),
                ('rejected', 'Review Rejected'), ('missing', 'Not Found in Latest Sync'), ('canceled', 'Review Canceled')]
CATEGORIES = [('notification', 'Notification'), ('otp', 'One-time code'), ('marketing', 'Marketing'), ('international', 'International')]
MESSAGE_STATES = [('queued', 'Queued'), ('unknown', 'Outcome Unknown'), ('accepted', 'Accepted'),
                  ('delivered', 'Delivered'), ('failed', 'Failed'), ('canceled', 'Canceled')]
LOCK_NAMESPACE = 734912


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
