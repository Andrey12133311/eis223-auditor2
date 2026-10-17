"""Short-lived browser-session authentication for the production site.

Session cookie deliberately has no Max-Age or Expires: closing the browser
ends the client session. Server session expires independently after 2h.
Cookie name v2 invalidates old persistent browser cookies without changing
accounts, passwords, audit data or EIS worker tokens.
"""

SESSION_COOKIE = "eis223_session_v2"
SESSION_MAX_AGE = 2 * 60 * 60


def cookie_options():
    return {
        "httponly": True,
        "secure": True,
        "samesite": "strict",
        "path": "/",
    }


def no_store_headers():
    return {"Cache-Control": "no-store"}
