Protect the HTTP control plane:

- support loopback, constant-time static bearer, and cached
  PyJWT/JWKS-backed OIDC profiles
- enforce repository-wide read, write, and admin scopes after authentication
- return one safe typed error envelope with correlation IDs, including host,
  CORS, validation, and unexpected failures
- enforce trusted hosts, scoped CORS, streamed request-body limits, exact
  issuer/audience checks, and JWK-bound asymmetric algorithms
