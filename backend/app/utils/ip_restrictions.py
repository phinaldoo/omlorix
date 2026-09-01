import os


IP_RESTRICTIONS_DISABLE_ENV = "OMLORIX_DISABLE_IP_RESTRICTIONS"


def ip_restrictions_disabled_by_environment() -> bool:
    """Return whether the deployment-level emergency IP bypass is enabled.

    This helper is deliberately independent from database settings so operators
    can recover from a bad IP policy or accidental IP ban even when the admin UI
    cannot be reached.
    """
    return str(os.getenv(IP_RESTRICTIONS_DISABLE_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}
