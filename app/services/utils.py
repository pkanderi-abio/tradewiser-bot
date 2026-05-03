from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

_audit_lock = RLock()
_audit_log: List[Dict[str, Any]] = []
_audit_next_id = 1


def record_audit_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Save a trade audit entry for later retrieval and return the saved entry."""
    global _audit_next_id
    with _audit_lock:
        log_entry = {
            "id": _audit_next_id,
            **entry,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _audit_log.append(log_entry)
        _audit_next_id += 1
        return log_entry


def get_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    """Return the most recent audit entries."""
    with _audit_lock:
        return list(_audit_log[-limit:])


def get_audit_entry(entry_id: int) -> Optional[Dict[str, Any]]:
    """Return a single audit entry by its ID."""
    with _audit_lock:
        for entry in _audit_log:
            if entry.get("id") == entry_id:
                return entry
    return None
