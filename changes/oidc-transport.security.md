OIDC issuer and JWKS endpoints must use HTTPS outside explicit loopback
development addresses. Unsafe URL characters that different HTTP parsers can
interpret inconsistently are rejected without rewriting the exact issuer
identifier used for token validation.
