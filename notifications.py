"""
In-app notification center -- the bell/inbox in the top-right of every
authenticated page.

Targeting model: a notification is either aimed at one unit (unit_id set,
audience='unit' -- e.g. "your order was confirmed") or broadcast to every
admin (unit_id=None, audience='admin' -- e.g. "a dispute needs review"). This
mirrors the acting_as-unit / admin-role split every other permission check in
this app already uses, so there's no new concept to learn.

Callers create notifications with notify_unit()/notify_admins() from the
route handlers in app.py at the moment something notification-worthy
happens (order status change, dispute raised/resolved, new match found).
Every write here is best-effort: a notification failing to save should never
break the actual action that triggered it, same philosophy as the existing
_log_order_history helper in app.py.
"""

from models import db, Notification


def create_notification(unit_id=None, audience='unit', type='info', title='', message=None, link=None):
    try:
        n = Notification(
            unit_id=unit_id, audience=audience, type=type,
            title=title, message=message, link=link,
        )
        db.session.add(n)
        db.session.commit()
        return n
    except Exception as e:
        db.session.rollback()
        print(f"Error creating notification: {e}")
        return None


def notify_unit(unit_id, type, title, message=None, link=None):
    """Notify a single unit (e.g. an order update affecting them)."""
    if not unit_id:
        return None
    return create_notification(unit_id=unit_id, audience='unit', type=type, title=title, message=message, link=link)


def notify_admins(type, title, message=None, link=None):
    """Broadcast to every admin (e.g. a new dispute needing review)."""
    return create_notification(unit_id=None, audience='admin', type=type, title=title, message=message, link=link)


def _context_query(unit_id=None, include_admin=False):
    """Notifications visible to the current request: whichever unit the user
    is acting as, plus admin-broadcast ones if they're an admin. Returns None
    if neither applies (logged in, not acting as any company, not an admin) --
    there is nothing to show."""
    from sqlalchemy import or_

    conditions = []
    if unit_id:
        conditions.append(Notification.unit_id == unit_id)
    if include_admin:
        conditions.append(Notification.audience == 'admin')
    if not conditions:
        return None
    return Notification.query.filter(or_(*conditions))


def notifications_for_context(unit_id=None, include_admin=False, limit=30):
    q = _context_query(unit_id, include_admin)
    if q is None:
        return []
    return q.order_by(Notification.created_at.desc()).limit(limit).all()


def unread_count_for_context(unit_id=None, include_admin=False):
    q = _context_query(unit_id, include_admin)
    if q is None:
        return 0
    return q.filter(Notification.is_read.is_(False)).count()


def mark_read(notification_id, unit_id=None, include_admin=False):
    n = Notification.query.get(notification_id)
    if not n:
        return False
    allowed = (unit_id and n.unit_id == unit_id) or (include_admin and n.audience == 'admin')
    if not allowed:
        return False
    n.is_read = True
    db.session.commit()
    return True


def mark_all_read(unit_id=None, include_admin=False):
    q = _context_query(unit_id, include_admin)
    if q is None:
        return 0
    unread = q.filter(Notification.is_read.is_(False)).all()
    for n in unread:
        n.is_read = True
    db.session.commit()
    return len(unread)
