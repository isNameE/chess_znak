import base64
import httpx
import pytest
from config import Settings
from integrations.true_api import TrueApi,IntegrationError
from integrations.submissions import submit


def test_uncertain_document_is_journaled_and_not_sent_twice(db):
    calls=[]
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('timeout',request=request)
    cfg=Settings(_env_file=None,true_api_enabled=True,true_api_token='fixture',true_api_profile_reviewed=True,
                 true_api_document_type='REVIEWED_FIXTURE',true_api_product_group='REVIEWED_FIXTURE')
    api=TrueApi(cfg,httpx.MockTransport(handler))
    raw=b'{"signed":"transport-test"}'
    signature=base64.b64encode(b'0fixture').decode()
    try:
        with pytest.raises(IntegrationError,match='Тайм-аут'):submit(api,raw,signature)
        with pytest.raises(IntegrationError,match='уже отправлялся'):submit(api,raw,signature)
        assert len(calls)==1
        with db.connect() as con:
            row=con.execute('SELECT * FROM sandbox_submissions').fetchone()
            assert row['state']=='unknown' and row['raw_payload']==raw
    finally:api.close()
