from fastapi import Depends, HTTPException, Request


def require_app_header(request: Request) -> None:
    """These POSTs carry no JSON body, so SameSite=Lax alone would let a same-site page post
    a form to them. A custom header cannot be sent by a plain form or without CORS."""
    if request.headers.get("x-requested-with") != "vegapunk":
        raise HTTPException(403, "Missing request header.")


AppHeader = Depends(require_app_header)
