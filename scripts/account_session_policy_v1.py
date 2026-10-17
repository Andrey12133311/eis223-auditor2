"""Pure account session policy, independent of the production application."""

SESSION_COOKIE = "eis223_session"
SESSION_MAX_AGE = 12 * 60 * 60


def cookie_options():
    return {
        "httponly": True,
        "secure": True,
        "samesite": "strict",
        "max_age": SESSION_MAX_AGE,
        "path": "/",
    }


def no_store_headers():
    return {"Cache-Control": "no-store"}
