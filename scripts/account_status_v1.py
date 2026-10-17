"""Pure account approval state rules, not connected to production."""

PENDING = 'pending'
APPROVED = 'approved'
REJECTED = 'rejected'
BLOCKED = 'blocked'


def registration_status():
    return PENDING


def can_sign_in(status):
    return status == APPROVED


def can_approve(actor_role, actor_status, target_status):
    return actor_role == 'admin' and actor_status == APPROVED and target_status == PENDING


def decide(actor_role, actor_status, target_status, decision):
    if not can_approve(actor_role, actor_status, target_status):
        raise PermissionError('Approval requires an active administrator and pending account')
    if decision not in ('approve', 'reject'):
        raise ValueError('Unknown decision')
    return APPROVED if decision == 'approve' else REJECTED
