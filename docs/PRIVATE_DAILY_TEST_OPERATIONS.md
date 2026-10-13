# Private daily TEST activation and restart

This is an engineer-operated synthetic TEST package. The owner and worker receive a normal private HTTPS login page and separate credentials; they do not run terminal commands. No hosting account, production connection, public tunnel, or external dispatch is created by this package.

## Preconditions

Use an existing explicitly approved private HTTPS gateway and persistent PostgreSQL/storage on that host. The application listener remains loopback-only behind that gateway. If no such host is available, remote activation is BLOCKED; the CI demo is not represented as persistent daily access.

Deploy the exact reviewed commit into an immutable checkout. PostgreSQL must already be the isolated `greenhouse_owner_os_test` database, marked `r3-connected-pilot:synthetic-only:v1`, migrated to `20261012_0035` through the reviewed additive migrations, with a restricted runtime role. Keep PGDATA/original BYTEA storage on a persistent private volume. Never reuse a production/shared database. Bootstrap migrations and synthetic provisioning are a separate one-time engineer operation; the launcher does not migrate, recreate, truncate, seed an empty database, or reset credentials.

Provision the existing synthetic profile and participant identities offline through `PilotAuth.provision`. Fix one timezone-aware `PILOT_EXPIRES_AT` before activation, within seven days. Store it with deployment configuration, unchanged on every restart. Persisted deployment expiry, participant identity and draft keys cannot be replaced by initialization. Deliver temporary TEST credentials only through the owner's authorized private channel; never put them in commits, logs or evidence artifacts.

## Persistent application entrypoint

Provide protected environment configuration: `APP_ENV=test`, `R3_CONNECTED_PILOT=1`, `R3_DAILY_PILOT=1`, `PILOT_EXPECTED_SHA=<exact reviewed commit>`, `PILOT_EXPIRES_AT=<unchanged fixed expiry>`, `TEST_DATABASE_URL=<own loopback restricted TEST connection>`, `PREVIEW_PUBLIC_ORIGIN=<approved private HTTPS origin>`, `PREVIEW_BIND=127.0.0.1`, and a temporary TEST-only `PREVIEW_SECRET` satisfying the existing adapter contract. No production secret is accepted. The daily login uses persisted participant sessions, not a shared preview-key login.

Run `python scripts/run_private_daily_test.py --check` before starting; it checks exact source, schema, synthetic marker, runtime privileges, existing expiry and HTTPS/private-loopback boundaries. Run the same script without `--check` to serve. Its expiry timer stops only the application process at the fixed deadline; database files are preserved. The authentication layer also rejects access at persisted expiry, independent of the process timer. For an isolated local diagnostic only, `PILOT_LOCAL_TEST=1` permits HTTP on loopback; it is not remote access authorization.

Restart the same process with the same checkout, database volume and environment. Do not rerun the CI bootstrap launcher: `start_r3_connected_ci.sh` deliberately creates an ephemeral CI database and is not a persistent-host restart tool. Do not change expiry to recover access. Stop/restart only this task's application and database. Original sources, sessions, revoked participants, draft encryption keys, accounting history and cached command receipts remain in the same storage.

## Verification and limits

The dedicated private CI job runs owner/worker authentication plus selected connected domains, compares all persisted-table aggregate hashes before and after an owned PostgreSQL/application restart, and restores private browser storage state for source/report/draft readback. Participant/password/session/draft-key values and browser storage state stay outside artifacts. Artifact guards run before upload. Test rate-limit buckets may be cleared offline only between independent synthetic fixture phases; they are never cleared to pass a mid-journey or restart assertion. Production rate-limit/authorization rules are unchanged.

Physical iPhone acceptance remains pending. Workers R1/R2 internal TEST activity is not Telegram delivery. Unsupported capabilities retain their visible status. Remote hosting availability and owner acceptance must be reported separately from code/CI readiness.

## PUBLIC repository handling

Repository visibility is PUBLIC; the word "private" in this package describes
application authorization, not GitHub artifact permissions. The CI job name is
not an access control. Never use Actions artifacts as credential delivery.
Keep participant inventories and browser storage outside upload roots. Uploads
must depend on a successful artifact guard even when earlier verification fails;
missing or malformed required inventories must fail closed. Archive contents
need bounded inspection, not merely scanning compressed bytes.

The guard is accidental-disclosure defense in depth, not a sandbox against a
malicious PR that edits its own scanner. Read-only tokens, trusted review,
isolated synthetic runtimes and repository rules remain separate requirements.
Do not submit discovered credentials to a login endpoint to test their validity.
Retain masked incident evidence privately and obtain authorization for rotation,
revocation or deletion of published materials.

Historical preview recipes may exist in other refs and old runs. A default-branch
patch does not neutralize their reruns. Verify repository-wide Actions controls
and historical run containment separately before declaring that boundary closed.

## Separate, manually authorized public-repository owner TEST

`temporary-owner-test.yml` is a new, explicitly bounded exception for an owner
review session. The historical private-repository workflow guards and the
persistent daily launcher are unchanged. This is not production deployment,
persistent hosting, real-data import, or activation of worker/external channels.

The owner manually stores a **unique synthetic-only** 20–200 character password
as the repository Actions secret `TEST_PREVIEW_OWNER_PASSWORD`. It must not be
reused anywhere else. The operator never retrieves its value. Repository secrets
are accessible to authorized workflow writers: this flow assumes the owner's
confirmed owner/Codex-only writer model. It does not claim protected-environment,
branch-protection, or independent writer isolation.

From the reviewed default-branch workflow, the owner chooses **Run workflow** and
enters the exact reviewed default-branch SHA as `expected_sha`. Both initiating
and triggering actor must be `ivansamoydyuk`, the repository and ref are fixed,
and only attempt 1 is allowed. A failed/expired run is not rerun: a new explicitly
authorized dispatch is required. No password is a workflow input or CLI argument.

The runner uses verified immutable Actions pins, a digest-pinned official
PostgreSQL 17.11 image, and an official Cloudflare binary verified against its
release SHA256 before execution. Its database binds only to loopback. The existing
synthetic profile and individual PilotAuth owner account are used; no worker
credentials or legacy shared-key access are created. OCR tools remain installed.
Cloudflare supplies an ephemeral HTTPS URL; the application listener remains
loopback, and application authentication protects data and assets.

After local and external unauthenticated-denial checks pass, the summary contains
only the URL, username `owner`, build SHA, and fixed expiry. The owner signs in
using their secret. No artifacts, screenshots, credential files, session headers,
response bodies, or runtime logs are published. The session expires 35 minutes
after application initialization; persisted authorization rejects expiry even
before process teardown. Cancellation, failure, and expiry stop the owned tunnel,
application, and labelled database; the hosted runner provides final teardown on
hard cancellation. State is disposable and is not preserved for another session.
