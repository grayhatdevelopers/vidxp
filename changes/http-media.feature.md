Add HTTP media transfer primitives:

- stream small multipart uploads through the shared ingestion service with a
  256 MiB ceiling
- persist subject- and repository-scoped upload idempotency records
- deliver authorized media and artifacts with strong checksum ETags and
  Starlette range responses
- keep large resumable uploads assigned to the separate `tusd` deployment
  phase
