import base64
import json
import httpx
import pytest
from config import Settings
from integrations.true_api import TrueApi,IntegrationError

SIGNATURE=base64.b64encode(b'0transport-fixture-not-a-real-signature').decode()


def config(**kwargs):
    return Settings(_env_file=None,true_api_enabled=True,**kwargs)


def test_auth_attached_signature_and_secret_not_in_status():
    seen=[]
    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/auth/key'):
            return httpx.Response(200,json={'uuid':'test-uuid','data':'exact-challenge'})
        return httpx.Response(200,json={'token':'secret-test-token'})
    api=TrueApi(config(),httpx.MockTransport(handler))
    try:
        challenge=api.challenge()
        assert challenge['data']=='exact-challenge'
        api.authenticate(challenge['uuid'],SIGNATURE)
        assert api.status()['hasToken']
        assert 'secret-test-token' not in json.dumps(api.status())
        assert json.loads(seen[1].content)['data']==SIGNATURE
        with pytest.raises(IntegrationError):api.authenticate(challenge['uuid'],SIGNATURE)
    finally:api.close()


def test_code_query_keeps_exact_cis_and_blocks_demo():
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json=[{'cisInfo':{'status':'TEST'}}])
    api=TrueApi(config(true_api_token='private-token'),httpx.MockTransport(handler))
    try:
        code='010000000000000021serial'
        api.code_info([code])
        assert json.loads(seen[0].content)==[code]
        assert seen[0].headers['Authorization']=='Bearer private-token'
        with pytest.raises(IntegrationError):api.code_info(['DEMO-1L-000001'])
        assert len(seen)==1
    finally:api.close()


def test_timeout_has_no_automatic_retry_and_http_error_hides_secret():
    calls=[]
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout('secret-token',request=request)
    api=TrueApi(config(true_api_token='secret-token'),httpx.MockTransport(timeout))
    with pytest.raises(IntegrationError,match='Тайм-аут') as exc:api.code_info(['test'])
    assert 'secret-token' not in str(exc.value) and len(calls)==1
    api.close()
    api=TrueApi(config(true_api_token='secret-token'),httpx.MockTransport(lambda r:httpx.Response(401,text='secret-token')))
    with pytest.raises(IntegrationError):api.code_info(['test'])
    assert not api.status()['hasToken']
    api.close()


def test_signed_bytes_are_not_reserialized_and_profile_required():
    seen=[]
    api=TrueApi(config(true_api_token='token'),httpx.MockTransport(lambda r:httpx.Response(200,json='external-id')))
    with pytest.raises(IntegrationError,match='Профиль'):api.submit_signed(b'{"x":1}',SIGNATURE)
    api.close()
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json='external-id')
    api=TrueApi(config(true_api_token='token',true_api_profile_reviewed=True,true_api_document_type='REVIEWED_TYPE',true_api_product_group='REVIEWED_GROUP'),httpx.MockTransport(handler))
    try:
        raw=b'{ "signed": "exact bytes", "n": 1 }\n'
        assert api.submit_signed(raw,SIGNATURE)=='external-id'
        assert base64.b64decode(json.loads(seen[0].content)['product_document'])==raw
        with pytest.raises(IntegrationError):api.submit_signed(b'{"id":"DEMO-BOX-1"}',SIGNATURE)
        assert len(seen)==1
    finally:api.close()
