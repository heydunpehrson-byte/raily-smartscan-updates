# RAILY laptop workstation connection

The Dispatch Brain remains localhost-only by default. To permit a trusted LAN workstation, set `RAILY_BIND_HOST` to the Brain PC's private LAN IPv4 address (for example `192.168.1.42`) and keep `RAILY_PORT=8765`. Do not bind to a public address or forward this port from the router.

The Brain's read-only discovery endpoint is:

`GET http://<brain-lan-ip>:8765/connection`

It reports the configured host, port, login route, and whether the bind is a LAN address. `GET /health` is suitable for Diagnostics. The workstation should store only this base URL, such as `http://192.168.1.42:8765`.

All document and job routes require `Authorization: Bearer <session-token>`. Obtain the token with:

`POST /login` JSON `{ "username", "password", "workstation_id", "workstation_name" }`.

The authenticated workstation routes are:

- `GET /me`, `POST /logout`
- `POST /documents/upload` (multipart `file`; acknowledgement includes the stable numeric `job_id` and `job_uuid` after the upload is durably received)
- `GET /jobs`, `GET /jobs/{job_id}`
- `GET /dashboard`
- `GET /review`, `GET /review/{job_id}/preview`, `POST /review/{job_id}` for Supervisor/Conductor/Admin review
- `POST /jobs/{job_id}/retry` for Supervisor/Conductor/Admin retry

The upload creates an ARRIVING job and persists the original bytes before returning success. Repeating a submission with the same client request should use the returned `job_uuid`; the Brain's SHA-256 duplicate pipeline prevents a second filed copy and routes exact matches to Duplicate Siding.

Administrators use `GET /admin/users`, `POST /admin/users`, `PATCH /admin/users/{id}/role`, `PATCH /admin/users/{id}/enabled`, and `POST /admin/users/{id}/reset-password`. Supported role names include `Administrator`/`Admin`, `Supervisor`, and `User` (legacy Conductor/Scanner/Viewer names remain accepted). Disabling an account revokes its active sessions. User creation, role changes, password resets, and enable/disable operations are audit logged.

The Brain launcher reads `RAILY_BIND_HOST` and `RAILY_PORT`; no credentials or secrets are placed in source code. Windows Firewall should be limited to TCP 8765 from the trusted private LAN profile and subnet by the local administrator if the laptop cannot connect. Do not open the port on Public networks.
