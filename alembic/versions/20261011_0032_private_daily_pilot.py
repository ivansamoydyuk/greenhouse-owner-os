"""Isolated synthetic TEST participant sessions; no domain posting changes."""
from alembic import op
revision = '20261011_0032'
down_revision = '20261011_0031'
branch_labels = depends_on = None

def upgrade():
    op.execute(r"""
CREATE TABLE core.pilot_deployments (
 id uuid PRIMARY KEY, entity_id uuid NOT NULL REFERENCES core.entities(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 UNIQUE(id,entity_id), CHECK(expires_at>created_at AND expires_at<=created_at+interval '7 days'));
CREATE TABLE core.pilot_participants (
 id uuid PRIMARY KEY, deployment_id uuid NOT NULL, entity_id uuid NOT NULL,
 actor_id uuid NOT NULL REFERENCES core.actors(id), worker_id uuid REFERENCES workers.people(id),
 role text NOT NULL CHECK(role IN ('owner','worker')), username text NOT NULL,
 password_hash text NOT NULL, draft_key bytea NOT NULL CHECK(octet_length(draft_key)=32), enabled boolean NOT NULL DEFAULT true, version integer NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(deployment_id,entity_id) REFERENCES core.pilot_deployments(id,entity_id),
 CHECK((role='owner' AND worker_id IS NULL) OR (role='worker' AND worker_id IS NOT NULL)),
 CHECK(username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'), CHECK(password_hash LIKE 'scrypt$%'),
 UNIQUE(deployment_id,username), UNIQUE(deployment_id,actor_id));
CREATE TABLE core.pilot_sessions (
 token_hash text PRIMARY KEY CHECK(token_hash ~ '^[0-9a-f]{64}$'),
 participant_id uuid NOT NULL REFERENCES core.pilot_participants(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 revoked_at timestamptz, CHECK(expires_at>created_at AND expires_at<=created_at+interval '24 hours'),
 CHECK(revoked_at IS NULL OR revoked_at>=created_at));
CREATE INDEX pilot_session_participant ON core.pilot_sessions(participant_id);
CREATE TABLE core.pilot_login_attempts (
 bucket text PRIMARY KEY CHECK(bucket ~ '^[0-9a-f]{64}$'), window_start timestamptz NOT NULL, attempts integer NOT NULL CHECK(attempts>0));
CREATE TABLE core.pilot_access_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), deployment_id uuid NOT NULL REFERENCES core.pilot_deployments(id),
 participant_id uuid REFERENCES core.pilot_participants(id), actor_participant_id uuid REFERENCES core.pilot_participants(id),
 kind text NOT NULL CHECK(kind IN ('PROVISION','LOGIN','LOGOUT','REVOKE')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE FUNCTION core.pilot_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d core.pilot_deployments; p core.pilot_participants;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Pilot history cannot be deleted' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME IN ('pilot_deployments','pilot_access_events') AND TG_OP='UPDATE' THEN
  RAISE EXCEPTION 'Pilot expiry and history are immutable' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='pilot_participants' THEN
  IF TG_OP='UPDATE' AND (NEW.id,NEW.deployment_id,NEW.entity_id,NEW.actor_id,NEW.worker_id,NEW.role,NEW.username,NEW.password_hash,NEW.draft_key,NEW.created_at)
    IS DISTINCT FROM (OLD.id,OLD.deployment_id,OLD.entity_id,OLD.actor_id,OLD.worker_id,OLD.role,OLD.username,OLD.password_hash,OLD.draft_key,OLD.created_at) THEN
    RAISE EXCEPTION 'Pilot participant identity is immutable' USING ERRCODE='23514'; END IF;
  IF TG_OP='UPDATE' AND (NEW.enabled OR NOT OLD.enabled OR NEW.version<>OLD.version+1) THEN
    RAISE EXCEPTION 'Pilot participant only permits versioned revocation' USING ERRCODE='23514'; END IF;
  IF NEW.role='owner' AND NOT core.r2_owner(NEW.entity_id,NEW.actor_id) THEN
    RAISE EXCEPTION 'Pilot owner scope mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.role='worker' AND NOT EXISTS(SELECT 1 FROM workers.people WHERE id=NEW.worker_id AND entity_id=NEW.entity_id AND actor_id=NEW.actor_id) THEN
    RAISE EXCEPTION 'Pilot worker scope mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='pilot_sessions' THEN
  SELECT * INTO p FROM core.pilot_participants WHERE id=NEW.participant_id;
  SELECT * INTO d FROM core.pilot_deployments WHERE id=p.deployment_id;
  IF NEW.expires_at>d.expires_at THEN RAISE EXCEPTION 'Session exceeds pilot expiry' USING ERRCODE='23514'; END IF;
  IF TG_OP='UPDATE' AND ((NEW.token_hash,NEW.participant_id,NEW.created_at,NEW.expires_at) IS DISTINCT FROM (OLD.token_hash,OLD.participant_id,OLD.created_at,OLD.expires_at) OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL) THEN
    RAISE EXCEPTION 'Session only permits revocation' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='pilot_access_events' THEN
  IF EXISTS(SELECT 1 FROM core.pilot_participants WHERE id IN(NEW.participant_id,NEW.actor_participant_id) AND deployment_id<>NEW.deployment_id) THEN
    RAISE EXCEPTION 'Pilot audit scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER pilot_guard BEFORE INSERT OR UPDATE OR DELETE ON core.pilot_deployments FOR EACH ROW EXECUTE FUNCTION core.pilot_guard();
CREATE TRIGGER pilot_guard BEFORE INSERT OR UPDATE OR DELETE ON core.pilot_participants FOR EACH ROW EXECUTE FUNCTION core.pilot_guard();
CREATE TRIGGER pilot_guard BEFORE INSERT OR UPDATE OR DELETE ON core.pilot_sessions FOR EACH ROW EXECUTE FUNCTION core.pilot_guard();
CREATE TRIGGER pilot_guard BEFORE INSERT OR UPDATE OR DELETE ON core.pilot_access_events FOR EACH ROW EXECUTE FUNCTION core.pilot_guard();
""")

def downgrade():
    raise RuntimeError('Forward-only migration: preserve pilot access history')
