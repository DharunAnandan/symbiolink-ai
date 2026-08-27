"""
Authentication and authorization decorators for role-based access control.

Provides decorators to protect routes based on user roles:
- admin: Full system access
- unit: Standard unit access (can manage own listings and orders)
- auditor: Read-only access for compliance and reporting
"""

from functools import wraps
from flask_login import current_user
from flask import abort, redirect, url_for, flash, request


def role_required(*allowed_roles):
    """Decorator to restrict access to users with specific roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login', next=request.url))
            
            if current_user.role not in allowed_roles:
                flash(f'Access denied. Required role: {", ".join(allowed_roles)}', 'danger')
                return abort(403)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f):
    """Decorator to restrict access to admin users only."""
    return role_required('admin')(f)


def unit_required(f):
    """Decorator to restrict access to unit users only."""
    return role_required('unit')(f)


def auditor_required(f):
    """Decorator to restrict access to auditor users only."""
    return role_required('auditor')(f)


def owns_unit_or_admin(f):
    """Decorator to allow access if user owns the unit or is admin."""
    @wraps(f)
    def decorated_function(unit_id, *args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login', next=request.url))
        
        # Admins can access any unit
        if current_user.role == 'admin':
            return f(unit_id, *args, **kwargs)
        
        # Unit users can only access their own unit
        if current_user.role == 'unit' and current_user.unit_id == unit_id:
            return f(unit_id, *args, **kwargs)
        
        flash('Access denied. You can only access your own unit.', 'danger')
        return abort(403)
    
    return decorated_function


def check_permission(permission):
    """Check if current user has a specific permission."""
    if not current_user.is_authenticated:
        return False
    
    # Define permission matrix
    permissions = {
        'admin': [
            'manage_users', 'manage_units', 'manage_listings', 'manage_orders',
            'view_analytics', 'edit_settings', 'view_all_data', 'audit_logs'
        ],
        'unit': [
            'manage_own_listings', 'manage_own_orders', 'view_own_matches',
            'search_listings', 'place_orders', 'rate_transactions'
        ],
        'auditor': [
            'view_all_data', 'view_analytics', 'audit_logs', 'export_reports'
        ]
    }
    
    user_permissions = permissions.get(current_user.role, [])
    return permission in user_permissions