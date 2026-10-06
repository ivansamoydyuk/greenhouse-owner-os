"""Token-free Telegram adapter boundary. Activation is a separate integration.
Caller must authenticate webhook/provider before constructing an incoming event.
Identity is chat+message ID, update ID retained in metadata; edits conflict.
"""
from dataclasses import dataclass
from uuid import UUID
from datetime import datetime
from greenhouse_owner_os.domain.workers import ActionInput,ReportInput
from greenhouse_owner_os.domain.errors import ValidationError
@dataclass(frozen=True)
class TelegramMessage:
    update_id:int
    chat_id:int
    message_id:int
    sender_reference:str
    text:str
    timestamp:datetime
    def locator(self):return str(self.chat_id)+':'+str(self.message_id)
class FakeDelivery:
    version='fake-telegram-r1'
    def __init__(self):self.messages={}
    def present(self,assignment,original_instruction):
        payload=dict(assignment_id=assignment['id'],language=assignment['language'],text=assignment['rendered_text'],original=original_instruction,transport='fake',status='presented')
        existing=self.messages.get(assignment['id'])
        if existing and existing!=payload:raise ValidationError('Outbound assignment changed')
        self.messages[assignment['id']]=payload;return payload
class TelegramTasksAdapter:
    def __init__(self,service,ingestion_actor_id=None):self.service=service;self.ingestion_actor_id=ingestion_actor_id
    def receive(self,entity_id,task_id,event,*,kind='report'):
        from .inventory import rows
        with self.service.factory() as u:
            workers=rows(u,'SELECT * FROM workers.people WHERE entity_id=:e AND telegram_ref=:ref AND active',e=entity_id,ref=event.sender_reference)
            if len(workers)!=1:return self._inbox(entity_id,task_id,event,'Неизвестный работник')
            w=workers[0];assignments=rows(u,'SELECT * FROM workers.assignments WHERE task_id=:t AND worker_id=:w',t=task_id,w=w['id'])
            if len(assignments)!=1:return self._inbox(entity_id,task_id,event,'Отчёт не сопоставлен с назначением')
            assignment=assignments[0]
        key='telegram:'+str(event.update_id)
        source='telegram:message'
        if kind=='acknowledge':
            # Full original text included in key-bound semantics via reason; edits cannot silently retry.
            return self.service.action(entity_id,UUID(w['actor_id']),key,task_id,ActionInput(kind='acknowledge',assignment_id=assignment['id'],reason=event.text,source=source,locator=event.locator(),source_timestamp=event.timestamp),worker_id=UUID(w['id']))
        return self.service.report(entity_id,UUID(w['actor_id']),key,task_id,UUID(w['id']),ReportInput(assignment_id=assignment['id'],original_text=event.text,original_language=w['language'],source=source,locator=event.locator(),source_timestamp=event.timestamp))

    def _inbox(self,e,t,event,issue):
        if self.ingestion_actor_id is None:raise ValidationError(issue+'; configured ingestion actor required')
        from .inbox import InboxService,InboxInput
        return InboxService(self.service.factory).ingest(e,self.ingestion_actor_id,'telegram:inbox:'+str(event.update_id),InboxInput(raw_text=event.text,source='telegram:message',locator=event.locator(),metadata=dict(task_report=True,sender_reference=event.sender_reference,task_id=str(t),source_timestamp=event.timestamp.isoformat(),update_id=event.update_id),received_at=None))
