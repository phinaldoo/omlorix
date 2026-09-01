from app.auth.twofa_provider import begin_setup, deactivate, normalize_otp_action, verify_setup
from app.users.models import get_user
from app.users.external_management import require_locally_managed_account


def setup_twofa(
    temp_secret: str | None,
    otp_code: str | None,
    user_id: str,
    email: str,
    group_id: str,
    db,
    *,
    otp_action: str | None = None,
    otp_destination: str | None = None,
    client_ip: str | None = None,
):
    """Backward-compatible setup endpoint wrapper.

    TOTP still supports ``temp_secret`` checks from older clients. Newer clients
    should use ``otp_action`` and ``otp_destination``.
    """
    user = get_user(db, user_id)
    if not user:
        return {"status": "error", "detail": "User not found"}
    require_locally_managed_account(user)

    action = normalize_otp_action(otp_action, "setup", otp_code)

    if temp_secret and otp_code:
        # Legacy TOTP verification path keeps compatibility while reusing the
        # shared verification flow, including throttling.
        from app.users.init import get_user_setting_value

        pending = get_user_setting_value(user.id, "secret", "2fa_secret_pending", db)
        if pending != temp_secret:
            return {"status": "error"}
        result = verify_setup(
            user,
            otp_code,
            action,
            otp_destination,
            db,
            provider="totp",
            client_ip=client_ip,
        )
        if result.get("status") == "success":
            return {"status": "success", "details": "2FA setup successful"}
        return result

    if otp_code:
        result = verify_setup(user, otp_code, action, otp_destination, db, client_ip=client_ip)
        if result.get("status") == "success":
            return {"status": "success", "details": "2FA setup successful"}
        return result
    return begin_setup(user, action, otp_destination, db)


def deactivate_twofa(user_id: str, db):
    user = get_user(db, user_id)
    if not user:
        return {"status": "error", "detail": "User not found"}
    require_locally_managed_account(user)
    return deactivate(user, db)
