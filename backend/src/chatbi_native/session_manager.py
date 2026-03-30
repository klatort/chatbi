"""
Session Management for ChatBI Agent
====================================
Manages user sessions, permissions, and context propagation.
"""

import time
import uuid
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from chatbi_native.user_context import get_user_context, invalidate_user_context
from chatbi_native.cache_manager import CacheType

logger = logging.getLogger(__name__)

@dataclass
class UserSession:
    """Represents a user session with context and permissions."""
    session_id: str
    user_id: str
    created_at: float
    last_activity: float
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    permissions: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    
    def is_active(self, timeout_seconds: int = 3600) -> bool:
        """Check if session is still active based on timeout."""
        return (time.time() - self.last_activity) < timeout_seconds
    
    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()
    
    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission."""
        return permission in self.permissions
    
    def add_permission(self, permission: str):
        """Add a permission to the user session."""
        if permission not in self.permissions:
            self.permissions.append(permission)
    
    def remove_permission(self, permission: str):
        """Remove a permission from the user session."""
        if permission in self.permissions:
            self.permissions.remove(permission)
    
    def update_context(self, key: str, value: Any):
        """Update session context."""
        self.context[key] = value
    
    def get_context(self, key: str, default: Any = None) -> Any:
        """Get value from session context."""
        return self.context.get(key, default)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary for serialization."""
        return {
            'session_id': self.session_id,
            'user_id': self.user_id,
            'created_at': datetime.fromtimestamp(self.created_at).isoformat(),
            'last_activity': datetime.fromtimestamp(self.last_activity).isoformat(),
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'permissions': self.permissions,
            'context_keys': list(self.context.keys())
        }


class SessionManager:
    """Manages user sessions."""
    
    def __init__(self):
        self.sessions: Dict[str, UserSession] = {}
        self.user_sessions: Dict[str, List[str]] = {}  # user_id -> list of session_ids
    
    def create_session(self, user_id: str, ip_address: Optional[str] = None, 
                      user_agent: Optional[str] = None) -> UserSession:
        """Create a new session for a user."""
        session_id = str(uuid.uuid4())
        now = time.time()
        
        session = UserSession(
            session_id=session_id,
            user_id=user_id,
            created_at=now,
            last_activity=now,
            ip_address=ip_address,
            user_agent=user_agent,
            permissions=self._get_default_permissions(user_id)
        )
        
        self.sessions[session_id] = session
        
        # Track sessions per user
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = []
        self.user_sessions[user_id].append(session_id)
        
        logger.info(f"Created session {session_id} for user {user_id}")
        return session
    
    def get_session(self, session_id: str) -> Optional[UserSession]:
        """Get a session by ID, updating activity if found."""
        session = self.sessions.get(session_id)
        if session and session.is_active():
            session.update_activity()
            return session
        elif session:
            # Session expired, remove it
            self._remove_session(session_id)
        return None
    
    def get_user_sessions(self, user_id: str) -> List[UserSession]:
        """Get all active sessions for a user."""
        active_sessions = []
        expired_sessions = []
        
        for session_id in self.user_sessions.get(user_id, []):
            session = self.sessions.get(session_id)
            if session and session.is_active():
                session.update_activity()
                active_sessions.append(session)
            elif session:
                expired_sessions.append(session_id)
        
        # Clean up expired sessions
        for session_id in expired_sessions:
            self._remove_session(session_id)
        
        return active_sessions
    
    def end_session(self, session_id: str):
        """End a session."""
        self._remove_session(session_id)
        logger.info(f"Ended session {session_id}")
    
    def end_all_user_sessions(self, user_id: str):
        """End all sessions for a user."""
        session_ids = self.user_sessions.get(user_id, [])
        for session_id in session_ids[:]:  # Copy list to avoid modification during iteration
            self._remove_session(session_id)
        
        logger.info(f"Ended all sessions for user {user_id}")
    
    def cleanup_expired_sessions(self, timeout_seconds: int = 3600):
        """Clean up expired sessions."""
        expired = []
        for session_id, session in self.sessions.items():
            if not session.is_active(timeout_seconds):
                expired.append(session_id)
        
        for session_id in expired:
            self._remove_session(session_id)
        
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired sessions")
    
    def invalidate_user_cache(self, user_id: str, cache_type: Optional[CacheType] = None):
        """Invalidate cache for a user."""
        invalidate_user_context(user_id, cache_type)
        
        # Also end all user sessions to force re-authentication if needed
        self.end_all_user_sessions(user_id)
        
        logger.info(f"Invalidated cache for user {user_id}" + 
                   (f" (type: {cache_type.value})" if cache_type else ""))
    
    def _remove_session(self, session_id: str):
        """Remove a session from all tracking structures."""
        session = self.sessions.pop(session_id, None)
        if session:
            user_id = session.user_id
            if user_id in self.user_sessions:
                self.user_sessions[user_id] = [
                    sid for sid in self.user_sessions[user_id] 
                    if sid != session_id
                ]
                # Remove user entry if no sessions left
                if not self.user_sessions[user_id]:
                    del self.user_sessions[user_id]
    
    def _get_default_permissions(self, user_id: str) -> List[str]:
        """Get default permissions for a user."""
        # In a real implementation, this would check user roles/groups
        # For now, return basic permissions
        return [
            "view_datasets",
            "view_charts",
            "view_dashboards",
            "create_charts",
            "execute_queries"
        ]
    
    def get_session_stats(self) -> Dict[str, Any]:
        """Get session statistics."""
        active_sessions = 0
        expired_sessions = 0
        users_with_sessions = 0
        
        for session in self.sessions.values():
            if session.is_active():
                active_sessions += 1
            else:
                expired_sessions += 1
        
        users_with_sessions = len(self.user_sessions)
        
        return {
            'total_sessions': len(self.sessions),
            'active_sessions': active_sessions,
            'expired_sessions': expired_sessions,
            'users_with_sessions': users_with_sessions,
            'sessions_per_user_avg': len(self.sessions) / max(users_with_sessions, 1)
        }


# Global session manager instance
_session_manager = None

def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def create_user_session(user_id: str, ip_address: Optional[str] = None, 
                       user_agent: Optional[str] = None) -> UserSession:
    """Create a new user session."""
    return get_session_manager().create_session(user_id, ip_address, user_agent)


def get_user_session(session_id: str) -> Optional[UserSession]:
    """Get a user session by ID."""
    return get_session_manager().get_session(session_id)


def end_user_session(session_id: str):
    """End a user session."""
    get_session_manager().end_session(session_id)


def cleanup_all_sessions():
    """Clean up all expired sessions."""
    get_session_manager().cleanup_expired_sessions()


def get_session_context(session_id: str, user_id: str) -> Dict[str, Any]:
    """
    Get comprehensive context for a session.
    
    Args:
        session_id: Session identifier
        user_id: User identifier
    
    Returns:
        Dictionary with session context
    """
    session = get_user_session(session_id)
    user_context = get_user_context(user_id)
    
    context = {
        'session': session.to_dict() if session else None,
        'user_context': user_context.to_dict(),
        'permissions': session.permissions if session else [],
        'cache_stats': user_context.cache.stats,
        'timestamp': datetime.now().isoformat()
    }
    
    return context


def validate_session_permission(session_id: str, permission: str) -> bool:
    """
    Validate if a session has a specific permission.
    
    Args:
        session_id: Session identifier
        permission: Permission to check
    
    Returns:
        True if session has permission, False otherwise
    """
    session = get_user_session(session_id)
    if not session:
        return False
    
    return session.has_permission(permission)


def update_session_activity(session_id: str):
    """Update session activity timestamp."""
    session = get_user_session(session_id)
    if session:
        session.update_activity()