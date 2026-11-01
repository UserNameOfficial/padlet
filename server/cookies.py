"""Load the operator's fixed Padlet cookies from server-only configuration."""
import os
import re
from pathlib import Path


def cookie_file():
    return Path(os.getenv("PADLET_COOKIE_FILE") or Path(__file__).resolve().parents[1] / ".local" / "padlet-cookie.txt")


def cookie_configured():
    return bool(os.getenv("PADLET_COOKIE_HEADER")) or cookie_file().is_file()


def fixed_cookies():
    value = os.getenv("PADLET_COOKIE_HEADER")
    if not value:
        try:
            value = cookie_file().read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            raise ValueError("서버에 고정 쿠키가 설정되지 않았습니다. 관리자에게 문의하세요.") from None
    # Also accept the supplied text export: alternating cookie names and values.
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) > 1:
        if len(lines) % 2:
            raise ValueError("서버 고정 쿠키 파일 형식을 확인하세요.")
        value = "; ".join(f"{lines[i]}={lines[i + 1]}" for i in range(0, len(lines), 2))
    return parse_cookie_header(value.strip())


def parse_cookie_header(value):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("서버 쿠키 설정은 16,384자 이내의 Cookie 헤더 값이어야 합니다.")
    value = value.strip()
    if value.lower().startswith("cookie:"):
        value = value[7:].strip()
    cookies, seen = [], set()
    for part in value.split(";"):
        if not part.strip():
            continue
        name, sep, content = part.strip().partition("=")
        if (not sep or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or name in seen or any(c in content for c in '\\" ,')):
            raise ValueError("서버 쿠키 형식을 확인하세요. 이름=값; 이름2=값2 형식이어야 합니다.")
        seen.add(name)
        cookies.append({"name": name, "value": content, "domain": ".padlet.com", "path": "/", "secure": True})
    if not cookies:
        raise ValueError("서버에 고정 Padlet 쿠키가 설정되지 않았습니다.")
    return cookies
