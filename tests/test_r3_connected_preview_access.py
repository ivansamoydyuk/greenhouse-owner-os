"""No live network: temporary-secret smoke and private-artifact contract."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from uuid import uuid4

import httpx
import pytest

from scripts.r3_connected_preview_access import VerificationError, prepare_access, settings, source_hashes


NOW=datetime(2026,10,10,12,0,tzinfo=timezone.utc)


def fixture(tmp_path, **edits):
    secret=secrets.token_urlsafe(32); session=secrets.token_urlsafe(48)
    entity=str(uuid4()); owner=str(uuid4())
    env=dict(APP_ENV='test',R3_CONNECTED_PILOT='1',PREVIEW_PUBLIC_ORIGIN='https://preview.example',
        OWNER_PREVIEW_FEATURE_SHA='a'*40,PREVIEW_SECRET=secret,GITHUB_ENV=str(tmp_path/'github-env'))
    config=dict(mode='CONNECTED_TEST',environment='TEST_PREVIEW',backend_connected=True,synthetic_only=True,
        actual_operations=False,feature_sha=env['OWNER_PREVIEW_FEATURE_SHA'],entity_id=entity,owner_id=owner,
        expires_at=(NOW+timedelta(minutes=35)).isoformat(),r2_fixtures=False,
        capabilities=dict(real_dispatch=False,external_integrations=False),
        synthetic_fixture=dict(owner_opening_loaded=False,synthetic_only=True,entity=dict(id=entity,classification='TEST_PREVIEW')))
    config.update(edits)
    return env,config,session


def client_for(env,config,session,*,cookie_flags='; Secure; HttpOnly; SameSite=strict', public_config=False):
    def respond(request):
        path=request.url.path
        authed=request.headers.get('cookie') == 'preview_session='+session
        if path=='/preview/login':
            assert request.method=='POST' and request.headers['origin']==env['PREVIEW_PUBLIC_ORIGIN']
            assert json.loads(request.content)['secret']==env['PREVIEW_SECRET']
            return httpx.Response(200,json={'ok':True},headers={'set-cookie':'preview_session='+session+'; Path=/'+cookie_flags})
        if path=='/r3/':return httpx.Response(200,text='Login')
        if not authed:
            return httpx.Response(200 if public_config and path=='/preview/config' else 401,json={})
        if path=='/preview/config':return httpx.Response(200,json=config)
        if path=='/r3/connected.html':return httpx.Response(200,text='Connected',headers={'content-security-policy':"connect-src 'self'",'cache-control':'no-store'})
        if '00000000-0000-0000-0000-000000000000' in path:return httpx.Response(403,json={})
        if path.endswith('/finance-overview'):return httpx.Response(200,json={'entity_id':config['entity_id'],'reference_class':'TEST_PREVIEW'})
        if path.endswith('/inventory'):return httpx.Response(200,json={'invariants':{'ok':True}})
        if path.endswith('/reconciliation'):return httpx.Response(200,json={'ok':True})
        if path.endswith('/cutover'):return httpx.Response(200,json={'opening_policy':'ZERO'})
        return httpx.Response(200,json={})
    return httpx.Client(base_url=env['PREVIEW_PUBLIC_ORIGIN'],transport=httpx.MockTransport(respond),follow_redirects=False)


def test_open_verifies_real_contract_and_writes_only_private_key_artifact(tmp_path):
    env,config,session=fixture(tmp_path)
    output=tmp_path/'output'
    with client_for(env,config,session) as client:
        result=prepare_access(output,env,client=client,now=NOW)
    assert result['url']=='https://preview.example/r3/' and result['backend_connected'] is True
    private=(output/'private-access.txt').read_text()
    metadata=(output/'preview-metadata.json').read_text()
    assert env['PREVIEW_SECRET'] in private and config['expires_at'] in private and env['OWNER_PREVIEW_FEATURE_SHA'] in private
    assert env['PREVIEW_SECRET'] not in metadata and session not in metadata
    assert 'set-cookie' not in metadata.lower() and 'password' not in metadata.lower()
    assert stat.S_IMODE((output/'private-access.txt').stat().st_mode)==0o600
    assert stat.S_IMODE((output/'preview-metadata.json').stat().st_mode)==0o600
    assert (tmp_path/'github-env').read_text()=='PREVIEW_EXPIRES_AT='+config['expires_at']+'\n'
    assert json.loads(metadata)==result


@pytest.mark.parametrize('updates',[
    dict(backend_connected=False),dict(feature_sha='b'*40),dict(actual_operations=True),dict(synthetic_only=False),
    dict(expires_at=(NOW-timedelta(seconds=1)).isoformat()),dict(expires_at=(NOW+timedelta(hours=2)).isoformat()),
    dict(expires_at='2026-10-10T12:35:00'),dict(capabilities={'real_dispatch':True,'external_integrations':False}),
    dict(synthetic_fixture={'owner_opening_loaded':True,'synthetic_only':True}),
])
def test_open_fails_closed_without_producing_access_files(tmp_path,updates):
    env,config,session=fixture(tmp_path,**updates)
    with client_for(env,config,session) as client,pytest.raises(VerificationError):
        prepare_access(tmp_path/'output',env,client=client,now=NOW)
    assert not (tmp_path/'output'/'private-access.txt').exists()


@pytest.mark.parametrize('kwargs',[dict(cookie_flags='; HttpOnly; SameSite=strict'),dict(cookie_flags='; Secure; SameSite=strict'),dict(public_config=True)])
def test_unauthenticated_or_weak_cookie_boundary_cannot_pass(tmp_path,kwargs):
    env,config,session=fixture(tmp_path)
    with client_for(env,config,session,**kwargs) as client,pytest.raises(VerificationError):
        prepare_access(tmp_path/'output',env,client=client,now=NOW)
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('origin',['http://preview.example','https://user@preview.example','https://preview.example/?token=bad','https://preview.example/path','https://preview.example:8443'])
def test_origin_must_be_explicit_https_without_credentials_or_tokens(tmp_path,origin):
    env,_,_=fixture(tmp_path);env['PREVIEW_PUBLIC_ORIGIN']=origin
    with pytest.raises(VerificationError):settings(env)


def test_audit_hashes_keep_no_original_content_or_location():
    original='TEST source '+secrets.token_hex(8);raw=original.encode();rid=uuid4()
    rows=[dict(id=rid,fingerprint=hashlib.sha256(raw).hexdigest(),file_bytes=raw,raw_text=None),
        dict(id=uuid4(),fingerprint=hashlib.sha256(raw).hexdigest(),file_bytes=None,raw_text=original),
        dict(id=uuid4(),fingerprint=hashlib.sha256(raw).hexdigest(),file_bytes=None,raw_text=None,source_reference=original),
        dict(id=uuid4(),fingerprint=hashlib.sha256(raw).hexdigest(),file_bytes=raw,raw_text=None,original_source_id=rid)]
    result=source_hashes(rows,fallback_reference=True)
    assert all(row['fingerprint_matches'] for row in result)
    assert original not in json.dumps(result) and all('raw_text' not in row and 'file_bytes' not in row for row in result)
    assert result[-1]['original_source_id']==str(rid)
    changed=deepcopy(rows[:1]);changed[0]['file_bytes']=b'changed'
    assert source_hashes(changed)[0]['fingerprint_matches'] is False
