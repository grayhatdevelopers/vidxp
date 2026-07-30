import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from vidxp.application_models import ApplicationError
from vidxp.authentication import (
    OIDCBearerAuthenticator,
    StaticBearerAuthenticator,
)
from vidxp.settings import ApplicationMode, VidXPSettings


class AuthenticationTests(unittest.TestCase):
    def test_static_bearer_uses_one_typed_principal(self):
        authenticator = StaticBearerAuthenticator("a" * 32)

        principal = authenticator.authenticate("a" * 32)

        self.assertEqual(principal.subject, "static")
        self.assertIn("*", principal.scopes)
        with self.assertRaises(ApplicationError) as caught:
            authenticator.authenticate("b" * 32)
        self.assertEqual(caught.exception.code, "authentication_required")

    def test_oidc_uses_cached_jwks_and_fixed_validation_contract(self):
        decoder = Mock(
            return_value={
                "sub": "user-1",
                "client_id": "client-1",
                "scope": "vidxp.read vidxp.write",
            }
        )
        signing_key = Mock(key="public-key")
        jwks = Mock()
        jwks.get_signing_key_from_jwt.return_value = signing_key
        fake_jwt = Mock()
        fake_jwt.PyJWTError = Exception
        fake_jwt.PyJWKClient.return_value = jwks
        fake_jwt.decode = decoder

        with patch.dict("sys.modules", {"jwt": fake_jwt}):
            authenticator = OIDCBearerAuthenticator(
                issuer="https://issuer.example/",
                audience="https://api.example",
                jwks_url="https://issuer.example/jwks",
                algorithms=("RS256",),
                required_scopes=("vidxp.read",),
            )
            principal = authenticator.authenticate("signed-token")

        self.assertEqual(principal.subject, "user-1")
        self.assertEqual(principal.client_id, "client-1")
        fake_jwt.PyJWKClient.assert_called_once_with(
            "https://issuer.example/jwks",
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )
        self.assertEqual(decoder.call_args.kwargs["algorithms"], ["RS256"])
        self.assertEqual(
            decoder.call_args.kwargs["issuer"],
            "https://issuer.example/",
        )
        self.assertEqual(
            decoder.call_args.kwargs["audience"],
            "https://api.example",
        )
        self.assertEqual(
            decoder.call_args.kwargs["options"]["require"],
            ["exp", "iss", "aud", "sub"],
        )

    def test_oidc_preserves_jwk_algorithm_binding(self):
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        public_jwk = jwt.PyJWK.from_dict(
            {
                **jwt.algorithms.RSAAlgorithm.to_jwk(
                    private_key.public_key(),
                    as_dict=True,
                ),
                "kid": "test-key",
                "alg": "RS256",
                "use": "sig",
            }
        )
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": "user-1",
                "iss": "https://issuer.example",
                "aud": "https://api.example",
                "scope": "vidxp.read",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS512",
            headers={"kid": "test-key"},
        )
        authenticator = OIDCBearerAuthenticator(
            issuer="https://issuer.example",
            audience="https://api.example",
            jwks_url="https://issuer.example/jwks",
            algorithms=("RS256", "RS512"),
            required_scopes=("vidxp.read",),
        )
        authenticator._jwks = Mock()
        authenticator._jwks.get_signing_key_from_jwt.return_value = public_jwk

        with self.assertRaises(ApplicationError) as caught:
            authenticator.authenticate(token)

        self.assertEqual(caught.exception.code, "authentication_required")

    def test_oidc_rejects_missing_configured_scope(self):
        signing_key = Mock(key="public-key")
        jwks = Mock()
        jwks.get_signing_key_from_jwt.return_value = signing_key
        fake_jwt = Mock()
        fake_jwt.PyJWTError = Exception
        fake_jwt.PyJWKClient.return_value = jwks
        fake_jwt.decode.return_value = {"sub": "user-1", "scope": "other"}

        with (
            patch.dict("sys.modules", {"jwt": fake_jwt}),
            self.assertRaises(ApplicationError) as caught,
        ):
            OIDCBearerAuthenticator(
                issuer="https://issuer.example",
                audience="https://api.example",
                jwks_url="https://issuer.example/jwks",
                algorithms=("RS256",),
                required_scopes=("vidxp.read",),
            ).authenticate("signed-token")

        self.assertEqual(caught.exception.code, "insufficient_scope")

    def test_oidc_readiness_checks_cached_signing_keys(self):
        key_set = Mock(
            keys=[
                Mock(
                    public_key_use="sig",
                    algorithm_name="RS256",
                    _jwk_data={"key_ops": ["verify"]},
                )
            ]
        )
        jwks = Mock()
        jwks.get_jwk_set.return_value = key_set
        fake_jwt = Mock()
        fake_jwt.PyJWTError = Exception
        fake_jwt.PyJWKClient.return_value = jwks

        with patch.dict("sys.modules", {"jwt": fake_jwt}):
            authenticator = OIDCBearerAuthenticator(
                issuer="https://issuer.example",
                audience="https://api.example",
                jwks_url="https://issuer.example/jwks",
                algorithms=("RS256",),
                required_scopes=(),
            )
            ready = authenticator.readiness()
            jwks.get_jwk_set.return_value = Mock(
                keys=[
                    Mock(
                        public_key_use="enc",
                        algorithm_name="RSA-OAEP",
                        _jwk_data={"key_ops": ["decrypt"]},
                    )
                ]
            )
            unusable = authenticator.readiness()
            jwks.get_jwk_set.side_effect = OSError("private-network-detail")
            unavailable = authenticator.readiness()

        self.assertTrue(ready.ready)
        self.assertFalse(unusable.ready)
        self.assertFalse(unavailable.ready)
        self.assertNotIn("private-network-detail", unavailable.message)

    def test_server_mode_refuses_unauthenticated_http(self):
        settings = VidXPSettings(
            mode=ApplicationMode.server,
            runtime_backend="cpu",
        )

        with self.assertRaisesRegex(ValueError, "requires static bearer or OIDC"):
            settings.validate_http_server()

    def test_oidc_settings_require_an_explicit_scope(self):
        with self.assertRaisesRegex(ValueError, "at least one scope"):
            VidXPSettings(
                runtime_backend="cpu",
                http_auth_mode="oidc",
                http_oidc_issuer="https://issuer.example",
                http_oidc_audience="https://api.example",
                http_oidc_jwks_url="https://issuer.example/jwks",
            )

    def test_oidc_settings_preserve_exact_issuer(self):
        settings = VidXPSettings(
            runtime_backend="cpu",
            http_auth_mode="oidc",
            http_oidc_issuer="https://issuer.example",
            http_oidc_audience="https://api.example",
            http_oidc_jwks_url="https://issuer.example/jwks",
            http_required_scopes=("vidxp.read",),
        )

        self.assertEqual(
            settings.http_oidc_issuer,
            "https://issuer.example",
        )

    def test_oidc_settings_preserve_uppercase_scheme_for_exact_issuer(self):
        settings = VidXPSettings(
            runtime_backend="cpu",
            http_auth_mode="oidc",
            http_oidc_issuer="HTTPS://issuer.example",
            http_oidc_audience="https://api.example",
            http_oidc_jwks_url="https://issuer.example/jwks",
            http_required_scopes=("vidxp.read",),
        )

        self.assertEqual(
            settings.http_oidc_issuer,
            "HTTPS://issuer.example",
        )

    def test_oidc_http_server_requires_canonical_mcp_resource(self):
        settings = VidXPSettings(
            runtime_backend="cpu",
            http_auth_mode="oidc",
            http_oidc_issuer="https://issuer.example",
            http_oidc_audience="https://api.example",
            http_oidc_jwks_url="https://issuer.example/jwks",
            http_required_scopes=("vidxp.read",),
        )

        with self.assertRaisesRegex(ValueError, "mcp_public_url"):
            settings.validate_http_server()

    def test_mcp_public_resource_and_host_policy_are_strict(self):
        with self.assertRaisesRegex(ValidationError, "ending in /mcp"):
            VidXPSettings(mcp_public_url="https://api.example/not-mcp")
        with self.assertRaisesRegex(
            ValidationError,
            "host wildcards",
        ):
            VidXPSettings(mcp_allowed_hosts=("*.example.com",))
        for origin in (
            "null",
            "file://local",
            "https://user@example.com",
            "https://client.example/path",
            "https://client.example?query",
        ):
            with self.subTest(origin=origin), self.assertRaisesRegex(
                ValidationError,
                "serialized HTTP origins",
            ):
                VidXPSettings(mcp_allowed_origins=(origin,))

        settings = VidXPSettings(
            mcp_public_url="https://api.example/mcp/",
            mcp_allowed_hosts=("API.EXAMPLE.COM",),
            mcp_allowed_origins=("http://localhost:*",),
        )
        self.assertEqual(
            settings.mcp_public_url,
            "https://api.example/mcp",
        )
        self.assertEqual(
            settings.mcp_allowed_hosts,
            ("api.example.com",),
        )
        self.assertEqual(
            settings.mcp_allowed_origins,
            ("http://localhost:*",),
        )

        disabled = VidXPSettings(mcp_allowed_hosts=())
        with self.assertRaisesRegex(ValueError, "allowed MCP host"):
            disabled.validate_http_server()

    def test_oidc_rejects_empty_query_and_fragment_delimiters(self):
        for field, value in (
            ("http_oidc_issuer", "https://issuer.example?"),
            ("http_oidc_issuer", "https://issuer.example#"),
            ("http_oidc_jwks_url", "https://issuer.example/jwks#"),
        ):
            values = {
                "runtime_backend": "cpu",
                "http_auth_mode": "oidc",
                "http_oidc_issuer": "https://issuer.example",
                "http_oidc_audience": "https://api.example",
                "http_oidc_jwks_url": "https://issuer.example/jwks",
                "http_required_scopes": ("vidxp.read",),
                field: value,
            }
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    VidXPSettings(**values)

    def test_oidc_rejects_cleartext_non_loopback_metadata(self):
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            VidXPSettings(
                runtime_backend="cpu",
                http_auth_mode="oidc",
                http_oidc_issuer="http://issuer.example",
                http_oidc_audience="https://api.example",
                http_oidc_jwks_url="http://issuer.example/jwks",
                http_required_scopes=("vidxp.read",),
            )

    def test_oidc_rejects_url_parser_disagreement_characters(self):
        with self.assertRaisesRegex(ValueError, "unsafe URL character"):
            VidXPSettings(
                runtime_backend="cpu",
                http_auth_mode="oidc",
                http_oidc_issuer="http://localhost",
                http_oidc_audience="https://api.example",
                http_oidc_jwks_url=(
                    "http://localhost\\@evil.example/jwks"
                ),
                http_required_scopes=("vidxp.read",),
            )


if __name__ == "__main__":
    unittest.main()
