"""
Tests for shared authentication utilities.

Validates auth context extraction, user access verification,
and context tier enforcement patterns.
"""

import pytest
from unittest.mock import Mock
from uuid import uuid4
from fastapi import HTTPException, Request

from shared.api.auth import (
    get_auth_context,
    get_user_id, 
    require_context_tier,
    verify_user_access
)


class TestGetAuthContext:
    """Test auth context extraction from request."""
    
    def test_get_auth_context_success(self):
        """Test successful auth context extraction."""
        # Arrange
        user_id = uuid4()
        request = Mock(spec=Request)
        request.state.user_id = user_id
        request.state.context_tier = 2
        request.state.email = "test@example.com"
        
        # Act
        result = get_auth_context(request)
        
        # Assert
        assert result == {
            "user_id": user_id,
            "context_tier": 2,
            "email": "test@example.com"
        }
    
    def test_get_auth_context_missing_user_id(self):
        """Test auth context extraction with missing user_id."""
        # Arrange
        request = Mock(spec=Request)
        request.state = Mock()
        # user_id attribute missing
        
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            get_auth_context(request)
        
        assert exc_info.value.status_code == 401
        assert "Authentication required" in str(exc_info.value.detail)
    
    def test_get_auth_context_incomplete_context(self):
        """Test auth context extraction with missing attributes."""
        # Arrange
        user_id = uuid4()
        request = Mock(spec=Request)
        request.state.user_id = user_id
        request.state.context_tier = 2
        # email attribute missing
        
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            get_auth_context(request)
        
        assert exc_info.value.status_code == 401
        assert "Incomplete authentication context" in str(exc_info.value.detail)


class TestGetUserId:
    """Test user ID extraction convenience function."""
    
    def test_get_user_id_success(self):
        """Test successful user ID extraction."""
        # Arrange
        user_id = uuid4()
        request = Mock(spec=Request)
        request.state.user_id = user_id
        request.state.context_tier = 2
        request.state.email = "test@example.com"
        
        # Act
        result = get_user_id(request)
        
        # Assert
        assert result == user_id


class TestVerifyUserAccess:
    """Test user access verification."""
    
    def test_verify_user_access_success(self):
        """Test successful user access verification."""
        # Arrange
        user_id = uuid4()
        auth_context = {
            "user_id": user_id,
            "context_tier": 2,
            "email": "test@example.com"
        }
        
        # Act & Assert - Should not raise
        verify_user_access(user_id, auth_context)
    
    def test_verify_user_access_forbidden(self):
        """Test user access verification with different user."""
        # Arrange
        user_id = uuid4()
        other_user_id = uuid4()
        auth_context = {
            "user_id": user_id,
            "context_tier": 2,
            "email": "test@example.com"
        }
        
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            verify_user_access(other_user_id, auth_context)
        
        assert exc_info.value.status_code == 403
        assert "Cannot access other users' resources" in str(exc_info.value.detail)


class TestRequireContextTier:
    """Test context tier enforcement."""
    
    def test_require_context_tier_success(self):
        """Test successful context tier enforcement."""
        # Arrange
        user_id = uuid4()
        request = Mock(spec=Request)
        request.state.user_id = user_id
        request.state.context_tier = 3
        request.state.email = "test@example.com"
        
        # Act & Assert - Should not raise
        tier_check = require_context_tier(2)
        result = tier_check(request)
        assert result is None  # Dependency satisfied
    
    def test_require_context_tier_insufficient(self):
        """Test context tier enforcement with insufficient tier."""
        # Arrange
        user_id = uuid4()
        request = Mock(spec=Request)
        request.state.user_id = user_id
        request.state.context_tier = 1
        request.state.email = "test@example.com"
        
        # Act & Assert
        tier_check = require_context_tier(2)
        with pytest.raises(HTTPException) as exc_info:
            tier_check(request)
        
        assert exc_info.value.status_code == 403
        assert "Requires context tier 2" in str(exc_info.value.detail)
    
    def test_require_context_tier_exact_match(self):
        """Test context tier enforcement with exact tier match."""
        # Arrange
        user_id = uuid4()
        request = Mock(spec=Request)
        request.state.user_id = user_id
        request.state.context_tier = 2
        request.state.email = "test@example.com"
        
        # Act & Assert - Should not raise
        tier_check = require_context_tier(2)
        result = tier_check(request)
        assert result is None  # Dependency satisfied


class TestConvenienceDependencies:
    """Test convenience dependency objects."""
    
    def test_require_tier2_import(self):
        """Test that RequireTier2 convenience dependency exists."""
        from shared.api.auth import RequireTier2
        assert RequireTier2 is not None
    
    def test_require_tier3_import(self):
        """Test that RequireTier3 convenience dependency exists."""
        from shared.api.auth import RequireTier3
        assert RequireTier3 is not None
    
    def test_require_tier4_import(self):
        """Test that RequireTier4 convenience dependency exists."""
        from shared.api.auth import RequireTier4
        assert RequireTier4 is not None


# Integration test example
def test_auth_integration_example():
    """
    Integration test showing how shared auth utilities work together.
    
    This demonstrates the typical usage pattern in API routes.
    """
    # Arrange - Simulate authenticated request
    user_id = uuid4()
    request = Mock(spec=Request)
    request.state.user_id = user_id
    request.state.context_tier = 2
    request.state.email = "test@example.com"
    
    # Act - Extract auth context
    auth_context = get_auth_context(request)
    
    # Act - Verify user can access their own resources
    verify_user_access(user_id, auth_context)  # Should pass
    
    # Act - Verify context tier requirement
    tier_check = require_context_tier(2)
    tier_result = tier_check(request)  # Should pass
    
    # Assert
    assert auth_context["user_id"] == user_id
    assert auth_context["context_tier"] == 2
    assert tier_result is None
    
    # Test cross-user access prevention
    other_user_id = uuid4()
    with pytest.raises(HTTPException):
        verify_user_access(other_user_id, auth_context)