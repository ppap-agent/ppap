# Security Boundary

## Secrets

Historical PPAP repositories contain or have contained real-shaped credentials and private runtime material. Never copy their defaults here.

- Secrets enter through the deployment secret store or runtime environment.
- `.env` is local-only; `.env.example` contains placeholders only.
- Rotate any source credential before using an equivalent integration in the new stack.
- Never place Feishu app secrets, Partner AI credentials, database passwords, private keys, kubeconfigs, or signed tokens in Git.

## Database roles

Use separate least-privilege roles.

### API role

- CRUD on product tables required by product endpoints
- create engine jobs through a narrow permission/function/insert contract
- no permission to mutate worker lease/attempt internals arbitrarily

### Worker role

- claim/update engine jobs and attempts
- read immutable product inputs needed for execution
- append result revisions/findings through a controlled write path
- no user/session administration

Production administrative/migration credentials are separate from both.

## Object storage

- Object keys for audit originals are immutable.
- Server components hold object-store credentials; browsers receive only product/API operations or narrowly scoped upload/download URLs.
- Worker verifies expected object metadata/checksum where practical.
- Historical audit identity is the stored object version/checksum, not a live Feishu URL.

## Feishu

Feishu tokens are integration credentials, not product identity storage. Persist only the minimum token/session material needed for the defined OAuth/Drive flow and protect it at rest.

The product maps external identity to its own user/tenant records so Feishu does not become the only possible future identity model.

## Legacy DOC conversion

If `.doc` remains supported:

- LibreOffice runs in a separate sandbox
- no network access
- bounded CPU/memory/time/output
- local IPC only
- converter failure produces an explicit engine diagnostic rather than compromising API availability

## Browser trust boundary

`apps/web` is untrusted. It never receives database admin keys, object-store admin credentials, Partner AI server credentials, or worker secrets.
