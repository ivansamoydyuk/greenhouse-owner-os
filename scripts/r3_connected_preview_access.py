"""Verify the already-running bounded R3 preview; never launch or deploy it.

open --output DIR writes a private0600 owner access file plus sanitized evidence.
audit --output DIR reads the guarded local synthetic DB after the review window.
All access credentials are supplied only by the temporary process environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class VerificationError(RuntimeError):
    """Intentionally sanitized: never include response bodies or credentials."""


def require(value, message):
    if not value:
        raise VerificationError(message)


def settings(env):
    require(env.get('APP_ENV') == 'test' and env.get('R3_CONNECTED_PILOT') == '1', 'Explicit connected TEST mode required')
    origin = env.get('PREVIEW_PUBLIC_ORIGIN', '').rstrip('/')
    parsed = urlsplit(origin)
    require(parsed.scheme == 'https' and parsed.hostname and parsed.path == '' and not parsed.query
        and not parsed.fragment and not parsed.username and not parsed.password, 'Explicit public HTTPS origin required')
    require(parsed.port in (None,443), 'Public preview must use HTTPS port443')
    sha = env.get('OWNER_PREVIEW_FEATURE_SHA', '')
    require(re.fullmatch(r'[0-9a-f]{40}', sha), 'Exact40-character feature SHA required')
    secret = env.get('PREVIEW_SECRET', '')
    require(len(secret) >= 24 and not any(c in secret for c in '\r\n'), 'Temporary preview secret required in environment')
    return origin, sha, secret


def json_read(client, path):
    response = client.get(path)
    require(response.status_code == 200, 'Authenticated read failed: ' + path.split('?')[0])
    try:
        value = response.json()
    except (ValueError, TypeError):
        raise VerificationError('Invalid JSON in authenticated read') from None
    require(isinstance(value, dict), 'Expected an object from authenticated read')
    return value


def verify_preview(client, env, now=None):
    origin, sha, secret = settings(env)
    now = now or datetime.now(timezone.utc)
    for path in ('/preview/config','/r3/connected.html','/r3/styles.css','/r3/connected.js'):
        require(client.get(path).status_code == 401, 'Unauthenticated access is not blocked: ' + path)
    require(client.get('/r3/').status_code == 200, 'Public login shell unavailable')
    response = client.post('/preview/login', json={'secret':secret}, headers={'Origin':origin})
    require(response.status_code == 200, 'Preview authentication failed')
    cookies = response.headers.get_list('set-cookie')
    session_cookie = next((cookie for cookie in cookies if cookie.startswith('preview_session=')), '')
    lowered = session_cookie.lower()
    require(session_cookie and '; secure' in lowered and '; httponly' in lowered and 'samesite=strict' in lowered,
        'Secure HttpOnly SameSite cookie required')
    config = json_read(client, '/preview/config')
    require(config.get('feature_sha') == sha, 'Feature SHA does not match requested candidate')
    require(config.get('mode') == 'CONNECTED_TEST' and config.get('environment') == 'TEST_PREVIEW'
        and config.get('backend_connected') is True and config.get('synthetic_only') is True
        and config.get('actual_operations') is False, 'Connected synthetic TEST identity not proven')
    fixture = config.get('synthetic_fixture', {})
    require(fixture.get('owner_opening_loaded') is False and fixture.get('synthetic_only') is True,
        'Synthetic-only fixture boundary not proven')
    require(config.get('r2_fixtures') is False and config.get('capabilities', {}).get('real_dispatch') is False
        and config.get('capabilities', {}).get('external_integrations') is False, 'External integration boundary not proven')
    try:
        entity = str(UUID(config['entity_id'])); owner = str(UUID(config['owner_id']))
        expiry = datetime.fromisoformat(config['expires_at'])
    except (KeyError,TypeError,ValueError):
        raise VerificationError('Entity, owner or expiry metadata invalid') from None
    require(expiry.tzinfo is not None and 0 < (expiry-now).total_seconds() <= 3600, 'Preview expiry must be future and bounded to one hour')
    require(fixture.get('entity', {}).get('id') == entity and fixture.get('entity', {}).get('classification') == 'TEST_PREVIEW',
        'Synthetic entity identity mismatch')
    root = '/v1/entities/' + entity
    readings = {name:json_read(client, root+suffix) for name,suffix in (
        ('home','/owner/home'),('finance','/finance-overview'),('harvest','/harvest'),('inventory','/inventory'))}
    require(readings['finance'].get('entity_id') == entity and readings['finance'].get('reference_class') == 'TEST_PREVIEW',
        'Backend Finance entity mismatch')
    require(readings['inventory'].get('invariants',{}).get('ok') is True, 'Inventory invariants failed')
    reconciliation = json_read(client, '/v1/finance/reconciliation?entity_id='+entity)
    require(reconciliation.get('ok') is True, 'Finance reconciliation failed')
    require(json_read(client, root+'/cutover').get('opening_policy') == 'ZERO', 'Owner opening profile must not be loaded')
    foreign = '00000000-0000-0000-0000-000000000000'
    require(client.get('/v1/entities/'+foreign+'/owner/home').status_code == 403, 'Fixed pilot entity boundary failed')
    asset = client.get('/r3/connected.html')
    require(asset.status_code == 200 and "connect-src 'self'" in asset.headers.get('content-security-policy',''),
        'Authenticated connected UI or same-origin CSP missing')
    require(asset.headers.get('cache-control') == 'no-store', 'Connected UI must not be cached')
    return dict(mode='CONNECTED_TEST',environment='TEST_PREVIEW',backend_connected=True,synthetic_only=True,
        feature_sha=sha,origin=origin,url=origin+'/r3/',entity_id=entity,owner_id=owner,
        verified_at=now.astimezone(timezone.utc).isoformat(),expires_at=expiry.astimezone(timezone.utc).isoformat(),
        owner_opening_loaded=False,real_dispatch=False,external_integrations=False,
        checks=['unauthenticated401','HTTPS_secure_session','exact_sha','synthetic_identity','bounded_expiry',
            'authenticated_connected_assets','same_origin_CSP','home_finance_harvest_inventory_reads',
            'zero_opening_policy','reconciliation','fixed_entity403'])


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def prepare_access(output, env, client=None, now=None):
    origin, sha, secret = settings(env)
    if client is None:
        # Keep certificate verification enabled and do not follow redirects with credentials.
        with httpx.Client(base_url=origin,timeout=30,verify=True,follow_redirects=False) as active:
            metadata = verify_preview(active,env,now)
    else:
        metadata = verify_preview(client,env,now)
    output = Path(output)
    private = '\n'.join((
        'R3 CONNECTED TEST · synthetic data only', 'URL: '+metadata['url'],
        'Temporary access key: '+secret, 'Exact feature SHA: '+sha,
        'Expires at (UTC): '+metadata['expires_at'],
        'Real integrations and production are disabled. Access ends at the stated expiry.', ''))
    write_private(output/'private-access.txt',private)
    write_private(output/'preview-metadata.json',json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    if env.get('GITHUB_ENV'):
        with open(env['GITHUB_ENV'],'a',encoding='utf-8') as stream:
            stream.write('PREVIEW_EXPIRES_AT='+metadata['expires_at']+'\n')
    return metadata


def source_hashes(rows, *, fallback_reference=False):
    result=[]
    for row in rows:
        binary = row['file_bytes']
        original = bytes(binary) if binary is not None else (row['raw_text'] if row['raw_text'] is not None else row.get('source_reference') if fallback_reference else None)
        actual = hashlib.sha256(original.encode() if isinstance(original,str) else original).hexdigest() if original is not None else None
        result.append(dict(id=str(row['id']),original_source_id=str(row['original_source_id']) if row.get('original_source_id') else None,
            stored_fingerprint=row['fingerprint'],actual_sha256=actual,
            fingerprint_matches=actual==row['fingerprint'],has_file=binary is not None))
    return result


def audit(output, env):
    from sqlalchemy import text
    from greenhouse_owner_os.infrastructure.database import make_session_factory
    from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
    from greenhouse_owner_os.preview import _require_connected_runtime
    from greenhouse_owner_os.services.reconciliation import reconcile
    from greenhouse_owner_os.synthetic_pilot import SOURCE
    require(env.get('APP_ENV') == 'test' and env.get('R3_CONNECTED_PILOT') == '1', 'Explicit connected TEST mode required')
    url = env['TEST_DATABASE_URL']
    require_disposable_database(url,env['APP_ENV'])
    from sqlalchemy.engine import make_url
    require(not make_url(url).query,'Connection overrides are not allowed')
    metadata=json.loads((Path(output)/'preview-metadata.json').read_text())
    require(metadata.get('feature_sha') == env.get('OWNER_PREVIEW_FEATURE_SHA'), 'Final audit feature SHA mismatch')
    entity=UUID(metadata['entity_id']); owner=UUID(metadata['owner_id'])
    sf=make_session_factory(url)
    with sf() as session:
        session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        _require_connected_runtime(session)
        require(session.scalar(text("SELECT count(*) FROM core.reference_records WHERE id=:e AND entity_id=:e AND kind='entity' AND reference_class='TEST_PREVIEW' AND source=:s"),dict(e=entity,s=SOURCE))==1,
            'Audit target is not the synthetic pilot entity')
        require(session.scalar(text('SELECT core.r2_owner(:e,:a)'),dict(e=entity,a=owner)), 'Audit owner/entity binding failed')
        role=dict(session.execute(text('''SELECT current_user AS current_role,session_user AS session_role,
            current_database() AS database_name,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls
            FROM pg_roles r WHERE r.rolname=current_user''')).mappings().one())
        tables=('core.inbox_items','core.proposed_actions','core.proposal_revisions','core.confirmation_effects','core.owner_tasks',
            'finance.postings','finance.direct_expenses','finance.obligations','finance.payments','finance.opening_payables',
            'inventory.events','inventory.movements','harvest.worker_reports','harvest.depot_settlements','workers.tasks','workers.assignments')
        counts={table:session.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=entity)) for table in tables}
        hashes={}
        rows=session.execute(text('SELECT id,fingerprint,raw_text,file_bytes FROM core.inbox_items WHERE entity_id=:e ORDER BY id'),dict(e=entity)).mappings()
        hashes['core.inbox_items']=source_hashes(rows)
        # Source-owned depot evidence can reference the immutable Inbox original
        # rather than duplicating bytes. Follow only persisted same-entity links.
        rows=session.execute(text("""SELECT h.id,h.fingerprint,h.source_reference,
            CASE WHEN h.source='owner:r2-action' THEN original.raw_text ELSE h.raw_text END AS raw_text,
            CASE WHEN h.source='owner:r2-action' THEN original.file_bytes ELSE h.file_bytes END AS file_bytes,
            original.id AS original_source_id
            FROM harvest.evidence h
            LEFT JOIN core.proposed_actions p ON h.source='owner:r2-action'
                AND p.id::text=h.source_reference AND p.entity_id=h.entity_id
            LEFT JOIN core.inbox_items original ON original.id=p.inbox_id AND original.entity_id=h.entity_id
            WHERE h.entity_id=:e ORDER BY h.id"""),dict(e=entity)).mappings()
        hashes['harvest.evidence']=source_hashes(rows,fallback_reference=True)
        reconciliation=reconcile(session,entity_id=entity,snapshot_established=True)
        result=dict(mode='CONNECTED_TEST',feature_sha=metadata['feature_sha'],entity_id=str(entity),
            audited_at=datetime.now(timezone.utc).isoformat(),read_only=True,role=role,counts=counts,
            reconciliation=reconciliation,source_hashes=hashes)
        result['source_hashes_ok']=all(item['fingerprint_matches'] for rows in hashes.values() for item in rows)
        result['status']='PASS' if reconciliation.get('ok') is True and result['source_hashes_ok'] else 'FAIL'
    write_private(Path(output)/'final-audit.json',json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    require(result['reconciliation'].get('ok') is True,'Final Finance reconciliation failed; sanitized audit retained')
    require(result['source_hashes_ok'],'Immutable source hash mismatch; sanitized audit retained')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('open','audit'))
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        result=prepare_access(args.output,os.environ) if args.mode=='open' else audit(args.output,os.environ)
    except Exception as exc:
        # Network/DB exceptions can contain credentials. Print only the type.
        print('R3 preview '+args.mode+' FAILED: '+(str(exc) if isinstance(exc,VerificationError) else type(exc).__name__))
        raise SystemExit(1) from None
    print('R3 preview '+args.mode+' PASS; sanitized evidence written; feature '+result['feature_sha'])


if __name__=='__main__':main()
