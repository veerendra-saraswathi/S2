import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from s2_common.auth import AuthError, TokenVerifier

ISSUER = "https://idp.test/realms/s2"
AUDIENCE = "s2-api"
TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(key: rsa.RSAPrivateKey) -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _token: key.public_key())


def mint(key: rsa.RSAPrivateKey, **overrides: object) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "exp": int(time.time()) + 300,
        "tenant_id": TENANT,
        "realm_access": {"roles": ["operator", "viewer"]},
        "preferred_username": "olive",
    }
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def test_valid_token_maps_to_principal(verifier: TokenVerifier, key: rsa.RSAPrivateKey) -> None:
    principal = verifier.verify(mint(key))
    assert principal.subject == "user-1"
    assert principal.tenant_id == uuid.UUID(TENANT)
    assert principal.roles == {"operator", "viewer"}
    assert principal.actor_type == "user" and principal.on_behalf_of is None
    assert principal.has_any_role("auditor", "viewer") and not principal.has_any_role("auditor")


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"exp": int(time.time()) - 3600}, "(?i)expired"),
        ({"aud": "someone-else"}, "(?i)audience"),
        ({"iss": "https://evil.test/realms/s2"}, "(?i)issuer"),
        ({"sub": None}, "sub"),
        ({"tenant_id": None}, "tenant_id"),
        ({"tenant_id": "not-a-uuid"}, "UUID"),
        ({"s2_actor_type": "robot"}, "s2_actor_type"),
        ({"s2_actor_type": "agent"}, "acts for"),
    ],
)
def test_rejects_bad_tokens(
    verifier: TokenVerifier, key: rsa.RSAPrivateKey, overrides: dict[str, object], needle: str
) -> None:
    with pytest.raises(AuthError, match=needle):
        verifier.verify(mint(key, **overrides))


def test_rejects_tokens_not_signed_by_the_issuer(verifier: TokenVerifier) -> None:
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 60, "tenant_id": TENANT}
    forgeries = [
        mint(stranger),
        jwt.encode(claims, None, algorithm="none"),
        jwt.encode(claims, "shared-secret-shared-secret-shared-secret", algorithm="HS256"),
        "not-a-jwt",
    ]
    for token in forgeries:
        with pytest.raises(AuthError):
            verifier.verify(token)


def test_agent_token_carries_the_human_it_acts_for(verifier: TokenVerifier, key: rsa.RSAPrivateKey) -> None:
    principal = verifier.verify(mint(key, s2_actor_type="agent", s2_on_behalf_of="user-9"))
    assert principal.actor_type == "agent"
    assert principal.on_behalf_of == "user-9"


def test_a_verifier_needs_a_key_source() -> None:
    with pytest.raises(ValueError):
        TokenVerifier(issuer=ISSUER, audience=AUDIENCE)
