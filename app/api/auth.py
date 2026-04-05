import hashlib
import hmac
import time

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import get_settings

router = APIRouter(tags=["auth"])
settings = get_settings()

SESSION_DURATION = 86400 * 7  # 7 days


def _make_token(timestamp: int) -> str:
    """Create a signed session token."""
    msg = f"{timestamp}:{settings.admin_password}"
    sig = hmac.new(
        settings.app_secret_key.encode(),
        msg.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{timestamp}:{sig}"


def verify_token(token: str | None) -> bool:
    """Verify a session token is valid and not expired."""
    if not token:
        return False
    try:
        parts = token.split(":")
        if len(parts) != 2:
            return False
        timestamp = int(parts[0])
        if time.time() - timestamp > SESSION_DURATION:
            return False
        expected = _make_token(timestamp)
        return hmac.compare_digest(token, expected)
    except (ValueError, TypeError):
        return False


@router.post("/auth/login")
async def login(request: Request):
    """Authenticate and set session cookie."""
    form = await request.form()
    password = form.get("password", "")

    if password != settings.admin_password:
        return HTMLResponse(
            content=_login_page(error="Senha incorreta"),
            status_code=401,
        )

    token = _make_token(int(time.time()))
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session",
        value=token,
        max_age=SESSION_DURATION,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/auth/logout")
async def logout():
    """Clear session cookie."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response


@router.get("/login")
async def login_page():
    """Show login page."""
    return HTMLResponse(content=_login_page())


def _login_page(error: str = "") -> str:
    error_html = ""
    if error:
        error_html = f'<div class="error">{error}</div>'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AImobiliarIA — Login</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'DM Sans', sans-serif;
            background: #0a0a0b;
            color: #fafafa;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .login-card {{
            background: #18181b;
            border: 1px solid #27272a;
            border-radius: 12px;
            padding: 40px;
            width: 100%;
            max-width: 380px;
        }}
        .logo {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 32px;
        }}
        .logo .icon {{
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, #10b981, #059669);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
        }}
        .logo h1 {{
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.3px;
        }}
        .logo span {{
            font-size: 12px;
            color: #71717a;
            display: block;
        }}
        label {{
            display: block;
            font-size: 13px;
            font-weight: 500;
            color: #a1a1aa;
            margin-bottom: 6px;
        }}
        input {{
            width: 100%;
            padding: 12px 14px;
            background: #0a0a0b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
            color: #fafafa;
            font-size: 15px;
            font-family: 'DM Sans', sans-serif;
            outline: none;
            transition: border-color 0.2s;
        }}
        input:focus {{ border-color: #10b981; }}
        button {{
            width: 100%;
            padding: 12px;
            background: #10b981;
            color: #000;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 600;
            font-family: 'DM Sans', sans-serif;
            cursor: pointer;
            margin-top: 20px;
            transition: background 0.2s;
        }}
        button:hover {{ background: #059669; }}
        .error {{
            background: #3b1a1a;
            border: 1px solid #7f1d1d;
            color: #fca5a5;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 16px;
        }}
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo">
            <div class="icon">🏠</div>
            <div>
                <h1>AImobiliarIA</h1>
                <span>Painel Admin</span>
            </div>
        </div>
        {error_html}
        <form method="POST" action="/auth/login">
            <label>Senha de acesso</label>
            <input type="password" name="password" placeholder="Digite a senha" autofocus required>
            <button type="submit">Entrar</button>
        </form>
    </div>
</body>
</html>"""
