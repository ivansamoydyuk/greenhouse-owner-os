"""Versioned worker UI evidence; each response has one atomic typed source."""
import json
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.domain.errors import ConflictError,NotFoundError,ValidationError
from greenhouse_owner_os.domain.workers import ReportInput,WorkerResponseResult,WorkerResponseHistory
from .inventory import rows
from .capture import BorrowedUow

SOURCE='worker:r2-ui'

class WorkersR2Mixin:
    def respond(self,e,a,key,t,w,b):
        if not key or len(key)>200:raise ValidationError('Stable request identity required')
        def run(u):
            task=self._task(u,e,t);worker=self._worker(u,e,w)
            if worker['actor_id']!=str(a):raise NotFoundError('worker identity mismatch')
            if not worker['active']:raise ValidationError('Worker inactive')
            if task['execution_contract']!='R2_RECEIVED_REPORT':raise ValidationError('Use the existing R1 task actions')
            assignments=rows(u,'SELECT * FROM workers.assignments WHERE id=:i AND task_id=:t AND worker_id=:w AND entity_id=:e',i=b.assignment_id,t=t,w=w,e=e)
            if not assignments:raise NotFoundError('assignment not found')
            assignment=assignments[0]
            snapshot=dict(schema_version=1,source_kind='UI_ACTION',entity_id=str(e),task_id=str(t),worker_id=str(w),actor_id=str(a),execution_contract=task['execution_contract'],**b.model_dump(mode='json'))
            locator=str(b.client_response_id)
            parents=rows(u,'SELECT * FROM workers.sources WHERE entity_id=:e AND source=:s AND locator=:l',e=e,s=SOURCE,l=locator)
            if parents and (parents[0]['event_type']!='r2_ui_response' or parents[0]['snapshot']!=snapshot):raise ConflictError('Оригинал ответа изменился; сохранён первый ответ')
            old=rows(u,'SELECT * FROM workers.actions WHERE entity_id=:e AND source=:s AND locator=:l',e=e,s=SOURCE+':action',l=locator)
            if old:
                # Independent request keys converge on the immutable response.
                previous=rows(u,"SELECT response_json AS response FROM audit.idempotency_keys WHERE actor_id=:a AND operation='worker_r2_response' AND resource_id=:id ORDER BY created_at LIMIT 1",a=a,id=old[0]['id'])
                if previous:return previous[0]['response']
                raise ConflictError('Ответ уже сохранён; обновите поручение')
            # A new logical response must use the current cycle. Exact old
            # originals above remain replayable without becoming current evidence.
            if task['cycle']!=b.expected_cycle or assignment['cycle']!=b.expected_cycle:raise ConflictError('Цикл задачи изменился; обновите поручение')
            if b.response=='DONE' and assignment['acknowledged_at'] is None:raise ValidationError('Сначала подтвердите получение поручения')
            u.session.execute(text("INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,:source,:loc,'r2_ui_response',CAST(:s AS jsonb))"),dict(e=e,source=SOURCE,loc=locator,s=json.dumps(snapshot,ensure_ascii=False)))
            if b.response=='RECEIVED' and assignment['acknowledged_at'] is not None:raise ConflictError('Получение уже подтверждено; обновите поручение')
            report=None
            if b.response!='RECEIVED':
                report=self.__class__(lambda:BorrowedUow(u)).report(e,a,'r2-report:'+str(e)+':'+locator,t,w,ReportInput(assignment_id=b.assignment_id,cycle=b.expected_cycle,
                    original_text=b.comment if b.comment else b.button_label,original_language=b.original_language,
                    normalized=dict(source_kind='UI_ACTION',response=b.response,button_label=b.button_label,comment_present=b.comment is not None),
                    claim='done' if b.response=='DONE' else 'issue',source=SOURCE+':report',locator=locator,source_timestamp=b.captured_at,event_at=b.captured_at))
            action=self._action(u,e,t,'acknowledge' if report is None else 'submit_report',None,w,b.assignment_id,UUID(report['id']) if report else None,
                                b.button_label if report is None else '',SOURCE+':action',locator,b.captured_at)
            current=self._task(u,e,t)
            return WorkerResponseResult(id=action['id'],client_response_id=b.client_response_id,task_id=t,assignment_id=b.assignment_id,cycle=b.expected_cycle,
                response=b.response,action_id=action['id'],report_id=report['id'] if report else None,task_version=current['version'],task_status=current['status']).model_dump(mode='json')
        return self._write(e,a,key,'worker_r2_response',dict(task_id=str(t),worker_id=str(w),**b.model_dump(mode='json')),run)

    def _response_history(self,u,e,t,w=None):
        data=rows(u,"""SELECT s.snapshot,s.received_at,a.id action_id,a.report_id FROM workers.sources s
            JOIN workers.actions a ON a.entity_id=s.entity_id AND a.source='worker:r2-ui:action' AND a.locator=s.locator
            WHERE s.entity_id=:e AND s.source='worker:r2-ui' AND s.event_type='r2_ui_response'
            AND s.snapshot->>'task_id'=:t AND (CAST(:w AS text) IS NULL OR s.snapshot->>'worker_id'=:w)
            ORDER BY s.received_at,s.locator""",e=e,t=str(t),w=str(w) if w else None)
        return [WorkerResponseHistory(**{k:r['snapshot'][k] for k in ('client_response_id','task_id','assignment_id','worker_id','execution_contract','response','source_kind','button_label','comment','original_language','captured_at')},
            cycle=r['snapshot']['expected_cycle'],received_at=r['received_at'],action_id=r['action_id'],report_id=r['report_id']).model_dump(mode='json') for r in data]
