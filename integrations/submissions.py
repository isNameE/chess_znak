"""Persist intent before network I/O, never automatically retry an uncertain send."""
import hashlib
import json
from uuid import uuid4
import repository
from .true_api import IntegrationError,validate_signature


def submit(api,raw,signature):
    if not api.status()['documentProfileReady']:
        raise IntegrationError('Сначала согласуйте профиль документа True API.')
    validate_signature(signature)
    try:content=json.loads(raw.decode('utf-8'))
    except (ValueError,UnicodeError):raise IntegrationError('Документ должен содержать JSON в UTF-8.') from None
    if not isinstance(content,dict) or 'DEMO-' in json.dumps(content,ensure_ascii=False):
        raise IntegrationError('Идентификаторы DEMO отправлять запрещено.')
    identity=json.dumps([api.config.true_api_document_type,api.config.true_api_product_group]).encode()+raw
    digest=hashlib.sha256(identity).hexdigest()
    submission_id=str(uuid4())
    with repository.connect() as con:
        created=con.execute('INSERT INTO sandbox_submissions(id,payload_sha256,raw_payload,document_type,product_group,state,message) '
                            "VALUES(%s,%s,%s,%s,%s,'unknown','Отправка начата; результат ещё не подтверждён') "
                            'ON CONFLICT(payload_sha256) DO NOTHING RETURNING id',
                            (submission_id,digest,raw,api.config.true_api_document_type,api.config.true_api_product_group)).fetchone()
        if not created:raise IntegrationError('Этот документ уже отправлялся. Проверьте предыдущий результат; автоматический повтор запрещён.')
    try:response=api.submit_signed(raw,signature)
    except IntegrationError as exc:
        with repository.connect() as con:
            con.execute('UPDATE sandbox_submissions SET message=%s WHERE id=%s',(str(exc),submission_id))
        repository.audit('True API sandbox: результат отправки документа требует уточнения','warning')
        raise
    external_id=response if isinstance(response,str) else response.get('document_id') if isinstance(response,dict) else None
    with repository.connect() as con:
        con.execute('UPDATE sandbox_submissions SET state=%s,external_id=%s,message=%s WHERE id=%s',
                    ('submitted' if external_id else 'response_received',external_id,'Получен ответ HTTP; итоговая обработка не подтверждена',submission_id))
    repository.audit('True API sandbox: получен ответ на отправку; итоговая обработка не подтверждена')
    return {'submissionId':submission_id,'externalId':external_id,'result':response}
