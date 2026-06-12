from app.core.security import (
    hash_password, verify_password,
    make_access_token, make_refresh_token, decode_token,
)


def test_password_round_trip():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_access_token_round_trip():
    tok = make_access_token("u1", {"superuser": True})
    claims = decode_token(tok)
    assert claims["sub"] == "u1"
    assert claims["typ"] == "access"
    assert claims["superuser"] is True


def test_refresh_token_round_trip():
    tok = make_refresh_token("u1")
    claims = decode_token(tok)
    assert claims["typ"] == "refresh"
