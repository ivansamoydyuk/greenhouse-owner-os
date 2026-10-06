"""Workers/Tasks authority. Writes are atomic, source-bound and non-posting."""
import json
from uuid import UUID,uuid4
from datetime import datetime,timezone
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ValidationError,ConflictError,NotFoundError
from greenhouse_owner_os.domain.workers import TaskInput,ReportInput
from .finance import FinanceService,_hash_request
from .inventory import rows

def same_value(key,left,right):
    if key in ('source_timestamp','event_at','due_at') and left is not None and right is not None:
        return datetime.fromisoformat(left)==datetime.fromisoformat(right)
    return left==right

class WorkersService:
    def __init__(self,factory):self.factory=factory;self.finance=FinanceService(factory)
    def _write(self,e,a,key,operation,payload,fn):
        h=_hash_request(dict(entity_id=str(e),**payload))
        try:
            with self.factory() as u:
                u.idempotency.lock(a,operation,key)
                replay=self.finance._replay_if_exists(u,a,operation,key,h)
                if replay:return replay
                if not rows(u,'SELECT id FROM core.entities WHERE id=:e',e=e):raise NotFoundError('entity not found')
                if operation not in ('task_action','task_report') and not rows(u,"SELECT id FROM core.actors WHERE id=:a AND actor_type='owner' AND active",a=a):raise NotFoundError('owner identity required')
                u.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('workers:'||CAST(:e AS text),0))"),{'e':str(e)})
                r=fn(u)
                u.audit.append(actor_id=a,action=operation,entity_type='workers',entity_id=UUID(r['id']),before=None,after=r)
                u.idempotency.put(actor_id=a,operation=operation,key=key,request_hash=h,resource_type='workers',resource_id=UUID(r['id']),response=r)
                u.commit();return r
        except DBAPIError as ex:
            code=getattr(ex.orig,'sqlstate',None)
            if code=='23505':raise ConflictError('Повтор источника или назначения: проверьте исходную запись') from ex
            if code in ('23514','23503','55000','22P02'):raise ValidationError(str(ex.orig).split('\n')[0]) from ex
            raise
    def _task(self,u,e,t):
        rs=rows(u,'SELECT * FROM workers.tasks WHERE id=:t AND entity_id=:e FOR UPDATE',t=t,e=e)
        if not rs:raise NotFoundError('task not found')
        return rs[0]
    def _worker(self,u,e,w):
        rs=rows(u,'SELECT * FROM workers.people WHERE id=:w AND entity_id=:e',w=w,e=e)
        if not rs:raise NotFoundError('worker not found')
        return rs[0]
    def worker(self,e,a,key,b):
        def run(u):
            actor=uuid4();u.session.execute(text("INSERT INTO core.actors(id,actor_type,display_name,external_ref) VALUES(:id,'worker',:name,:ref)"),dict(id=actor,name=b.display_name,ref='worker:'+str(actor)))
            return rows(u,'INSERT INTO workers.people(id,entity_id,actor_id,display_name,native_name,language,contact,telegram_ref,notes,source,created_by) VALUES(:id,:e,:actor,:display_name,:native_name,:language,:contact,:telegram_ref,:notes,:source,:a) RETURNING *',id=uuid4(),e=e,a=a,actor=actor,**b.model_dump(mode='json'))[0]
        return self._write(e,a,key,'worker_create',b.model_dump(mode='json'),run)
    def active(self,e,a,key,w,active):
        def run(u):
            self._worker(u,e,w)
            return rows(u,'UPDATE workers.people SET active=:active WHERE id=:w RETURNING *',w=w,active=active)[0]
        return self._write(e,a,key,'worker_active',dict(worker_id=str(w),active=active),run)
    def _create(self,u,e,a,b,*,rule=None,occurrence=None):
        payload=b.model_dump(mode='json');old=rows(u,'SELECT * FROM workers.tasks WHERE entity_id=:e AND source=:source AND locator=:locator',e=e,source=b.source,locator=b.locator)
        if old:
            if old[0]['interpretation']!=payload:raise ConflictError('Исходное поручение или его трактовка изменились; оригинал сохранён')
            return old[0]
        p=dict(payload);gaps=list(b.gaps)
        if rows(u,'SELECT id FROM workers.tasks WHERE entity_id=:e AND original_instruction=:raw',e=e,raw=b.original_instruction):gaps.append('Возможный дубль поручения: другой идентификатор источника')
        p['gaps']=json.dumps(gaps,ensure_ascii=False)
        return rows(u,'INSERT INTO workers.tasks(id,entity_id,owner_language,title,original_instruction,description,category,greenhouse_id,work_area_id,priority,planned_date,due_at,source,locator,source_timestamp,interpretation,gaps,created_by,rule_id,template_id,occurrence_date) VALUES(:id,:e,:owner_language,:title,:original_instruction,:description,:category,:greenhouse_id,:work_area_id,:priority,:planned_date,:due_at,:source,:locator,:source_timestamp,CAST(:payload AS jsonb),CAST(:gaps AS jsonb),:a,:rule,:template,:occurrence) RETURNING *',id=uuid4(),e=e,a=a,payload=json.dumps(payload,ensure_ascii=False),rule=rule['id'] if rule else None,template=rule['template_id'] if rule else None,occurrence=occurrence,**p)[0]
    def create(self,e,a,key,b):return self._write(e,a,key,'task_create',b.model_dump(mode='json'),lambda u:self._create(u,e,a,b))
    def assign(self,e,a,key,t,b):
        def run(u):
            task=self._task(u,e,t);worker=self._worker(u,e,b.worker_id)
            if not worker['active']:raise ValidationError('Работник неактивен')
            if b.language!=(task['owner_language'] if b.translation_source=='original' else worker['language']):raise ValidationError('Проверьте язык оригинала/перевода')
            if b.translation_source=='original' and b.rendered_text!=task['original_instruction']:raise ValidationError('Оригинал нельзя менять под видом перевода')
            old=rows(u,'SELECT * FROM workers.assignments WHERE task_id=:t AND worker_id=:w',t=t,w=b.worker_id)
            if old:
                if any(old[0][k]!=v for k,v in b.model_dump(mode='json').items()):raise ConflictError('Назначение уже существует с другим текстом')
                return old[0]
            p=b.model_dump(mode='json');r=rows(u,'INSERT INTO workers.assignments(id,entity_id,task_id,worker_id,assigned_by,language,rendered_text,translation_source,translation_version,rendering_reviewed,cycle) VALUES(:id,:e,:t,:worker_id,:a,:language,:rendered_text,:translation_source,:translation_version,:rendering_reviewed,:cycle) RETURNING *',id=uuid4(),e=e,t=t,a=a,cycle=task['cycle'],**p)[0]
            self._action(u,e,t,'assign',a,None,r['id'],None,'', 'assignment',r['id'],None)
            return r
        return self._write(e,a,key,'task_assign',dict(task_id=str(t),**b.model_dump(mode='json')),run)
    def _action(self,u,e,t,kind,a,w,assignment,report,reason,source,locator,source_timestamp):
        task=self._task(u,e,t)
        p=dict(entity_id=str(e),task_id=str(t),kind=kind,actor_id=str(a) if a else None,worker_id=str(w) if w else None,assignment_id=str(assignment) if assignment else None,report_id=str(report) if report else None,reason=reason,source=source,locator=locator,source_timestamp=source_timestamp.isoformat() if source_timestamp else None)
        old=rows(u,'SELECT * FROM workers.actions WHERE entity_id=:e AND source=:source AND locator=:locator',e=e,source=source,locator=locator)
        if old:
            if any(not same_value(k,old[0][k],v) for k,v in p.items()):raise ConflictError('Сообщение изменено; нужна отдельная версия/проверка')
            return old[0]
        if kind in ('acknowledge','start'):
            previous=rows(u,'SELECT * FROM workers.actions WHERE assignment_id=:id AND cycle=:cycle AND kind=:kind',id=assignment,cycle=task['cycle'],kind=kind)
            if previous:
                snapshot=dict(p)
                u.session.execute(text("INSERT INTO workers.sources(entity_id,source,locator,event_type,snapshot) VALUES(:e,:s,:l,'actions',CAST(:p AS jsonb)) ON CONFLICT DO NOTHING"),dict(e=e,s=source,l=locator,p=json.dumps(snapshot,ensure_ascii=False)))
                known=rows(u,'SELECT * FROM workers.sources WHERE entity_id=:e AND source=:s AND locator=:l',e=e,s=source,l=locator)[0]
                if known['event_type']!='actions' or known['snapshot']!=snapshot:raise ConflictError('Source message changed')
                return previous[0]
        return rows(u,'INSERT INTO workers.actions(id,entity_id,task_id,kind,assignment_id,report_id,actor_id,worker_id,cycle,reason,source,locator,source_timestamp) VALUES(:id,:entity_id,:task_id,:kind,:assignment_id,:report_id,:actor_id,:worker_id,:cycle,:reason,:source,:locator,:source_timestamp) RETURNING *',id=uuid4(),cycle=task['cycle'],**p)[0]
    def action(self,e,a,key,t,b,*,worker_id=None):
        def run(u):
            task=self._task(u,e,t)
            if worker_id:
                worker=self._worker(u,e,worker_id)
                if worker['actor_id']!=str(a):raise NotFoundError('worker identity mismatch')
                if not worker['active']:raise ValidationError('Worker inactive')
                if b.kind not in ('acknowledge','start','submit_report'):raise ValidationError('Owner review required')
                assignment=rows(u,'SELECT * FROM workers.assignments WHERE task_id=:t AND worker_id=:w',t=t,w=worker_id)
                if not assignment or b.assignment_id and str(b.assignment_id)!=assignment[0]['id']:raise NotFoundError('assignment not found')
                if b.expected_cycle!=task['cycle']:raise ConflictError('Цикл задачи изменился; обновите поручение')
                assignment_id=assignment[0]['id']
            else:
                if b.kind not in ('complete','reopen','cancel','review_report'):raise ValidationError('Worker action requires scoped worker identity')
                assignment_id=None
                if b.expected_version is None or b.expected_version!=task['version']:raise ConflictError('Состояние задачи изменилось; обновите и проверьте')
            if b.kind=='submit_report':
                current_report=rows(u,'SELECT id FROM workers.reports WHERE id=:r AND task_id=:t AND cycle=:c AND state=\'draft\'',r=b.report_id,t=t,c=task['cycle'])
                if not current_report and not rows(u,'SELECT id FROM workers.actions WHERE entity_id=:e AND source=:s AND locator=:l',e=e,s=b.source,l=b.locator):raise ValidationError('Отчёт уже отправлен или относится к предыдущему циклу')
            self._action(u,e,t,b.kind,None if worker_id else a,worker_id,assignment_id,b.report_id,b.reason,b.source,b.locator,b.source_timestamp)
            return self._detail(u,e,t,worker_id)
        return self._write(e,a,key,'task_action',dict(task_id=str(t),worker_id=str(worker_id) if worker_id else None,**b.model_dump(mode='json')),run)
    def report(self,e,a,key,t,w,b):
        def run(u):
            task=self._task(u,e,t);worker=self._worker(u,e,w)
            if worker['actor_id']!=str(a) and not rows(u,"SELECT id FROM core.actors WHERE id=:a AND actor_type='owner' AND active",a=a):raise NotFoundError('worker identity mismatch')
            if not worker['active']:raise ValidationError('Worker inactive')
            assignment=rows(u,'SELECT * FROM workers.assignments WHERE id=:id AND task_id=:t AND worker_id=:w',id=b.assignment_id,t=t,w=w)
            if not assignment:raise NotFoundError('assignment not found')
            p=b.model_dump(mode='json');old=rows(u,'SELECT * FROM workers.reports WHERE entity_id=:e AND source=:source AND locator=:locator',e=e,source=b.source,locator=b.locator)
            if old:
                if old[0]['task_id']!=str(t) or old[0]['worker_id']!=str(w) or any(not same_value(k,old[0][k],v) for k,v in p.items()):raise ConflictError('Changed original report; use new message identity')
                return old[0]
            for k in ('normalized','issues','attachments'):p[k]=json.dumps(p[k],ensure_ascii=False)
            return rows(u,'INSERT INTO workers.reports(id,entity_id,task_id,assignment_id,worker_id,cycle,original_text,original_language,normalized,issues,attachments,claim,source,locator,source_timestamp,event_at) VALUES(:id,:e,:t,:assignment_id,:w,:cycle,:original_text,:original_language,CAST(:normalized AS jsonb),CAST(:issues AS jsonb),CAST(:attachments AS jsonb),:claim,:source,:locator,:source_timestamp,:event_at) RETURNING *',id=uuid4(),e=e,t=t,w=w,**p)[0]
        return self._write(e,a,key,'task_report',dict(task_id=str(t),worker_id=str(w),**b.model_dump(mode='json')),run)
    def rule(self,e,a,key,b):
        def run(u):
            predecessor=None
            if b.replaces_id:
                rs=rows(u,'SELECT * FROM workers.rules WHERE id=:id AND entity_id=:e',id=b.replaces_id,e=e)
                if not rs:raise NotFoundError('rule not found')
                predecessor=rs[0]
            return rows(u,'INSERT INTO workers.rules(id,entity_id,template_id,version,replaces_id,definition,frequency,weekday,effective_from,effective_to,active,created_by) VALUES(:id,:e,:template,:version,:replaces_id,CAST(:definition AS jsonb),:frequency,:weekday,:effective_from,:effective_to,:active,:a) RETURNING *',id=uuid4(),e=e,a=a,template=predecessor['template_id'] if predecessor else uuid4(),version=predecessor['version']+1 if predecessor else 1,definition=json.dumps(b.task.model_dump(mode='json'),ensure_ascii=False),**b.model_dump(mode='json',exclude={'task'}))[0]
        return self._write(e,a,key,'task_rule',b.model_dump(mode='json'),run)
    def occurrence(self,e,a,key,rule_id,d):
        def run(u):
            rs=rows(u,'SELECT * FROM workers.rules WHERE id=:id AND entity_id=:e',id=rule_id,e=e)
            if not rs:raise NotFoundError('rule not found')
            rule=rs[0]
            versions=rows(u,'SELECT * FROM workers.rules WHERE entity_id=:e AND template_id=:template AND effective_from<=:d ORDER BY version DESC LIMIT 1',e=e,template=rule['template_id'],d=d)
            if not versions or versions[0]['id']!=str(rule_id) or not rule['active'] or rule['effective_to'] and d.isoformat()>rule['effective_to'] or d.isoformat()<rule['effective_from'] or rule['frequency']=='weekly' and d.weekday()!=rule['weekday']:raise ValidationError('Rule does not apply on this date')
            old=rows(u,'SELECT * FROM workers.tasks WHERE entity_id=:e AND template_id=:template AND occurrence_date=:d',e=e,template=rule['template_id'],d=d)
            if old:return old[0]
            f=dict(rule['definition']);f.update(source='recurrence',locator=rule['template_id']+':'+d.isoformat(),planned_date=d.isoformat(),due_at=None)
            return self._create(u,e,a,TaskInput(**f),rule=rule,occurrence=d)
        return self._write(e,a,key,'task_occurrence',dict(rule_id=str(rule_id),date=d.isoformat()),run)
    def link(self,e,a,key,t,b):
        def run(u):
            self._task(u,e,t)
            old=rows(u,'SELECT * FROM workers.links WHERE task_id=:t AND domain=:domain AND target_id=:target_id',t=t,**b.model_dump(mode='json'))
            if old:return old[0]
            return rows(u,'INSERT INTO workers.links(id,entity_id,task_id,domain,target_id,created_by) VALUES(:id,:e,:t,:domain,:target_id,:a) RETURNING *',id=uuid4(),e=e,t=t,a=a,**b.model_dump(mode='json'))[0]
        return self._write(e,a,key,'task_link',dict(task_id=str(t),**b.model_dump(mode='json')),run)
    def _detail(self,u,e,t,w=None):
        task=self._task(u,e,t)
        if w and not rows(u,'SELECT id FROM workers.assignments WHERE task_id=:t AND worker_id=:w',t=t,w=w):raise NotFoundError('assignment not found')
        task['assignments']=rows(u,'SELECT a.*,p.display_name FROM workers.assignments a JOIN workers.people p ON p.id=a.worker_id WHERE a.task_id=:t AND (CAST(:w AS uuid) IS NULL OR a.worker_id=CAST(:w AS uuid)) ORDER BY a.assigned_at',t=t,w=str(w) if w else None)
        task['reports']=rows(u,'SELECT * FROM workers.reports WHERE task_id=:t AND (CAST(:w AS uuid) IS NULL OR worker_id=CAST(:w AS uuid)) ORDER BY received_at',t=t,w=str(w) if w else None)
        task['actions']=rows(u,'SELECT * FROM workers.actions WHERE task_id=:t AND (CAST(:w AS uuid) IS NULL OR worker_id IS NULL OR worker_id=CAST(:w AS uuid)) ORDER BY received_at,id',t=t,w=str(w) if w else None)
        if w:
            # Worker payload intentionally omits downstream accounting links and other assignees.
            for k in ('interpretation','source','locator','created_by','description','gaps'):task.pop(k,None)
        else:
            task['can_complete']=task['status']=='reported' and bool(task['assignments']) and all(any(r['assignment_id']==a['id'] and r['cycle']==task['cycle'] and r['state']=='submitted' and r['claim']=='done' for r in task['reports']) for a in task['assignments']) and not any(r['cycle']==task['cycle'] and r['state']=='submitted' and r['claim']=='issue' and not any(x['kind']=='review_report' and x['report_id']==r['id'] for x in task['actions']) for r in task['reports'])
            task['links']=rows(u,'SELECT * FROM workers.links WHERE task_id=:t ORDER BY created_at',t=t)
        return task
    def get(self,e,t,w=None):
        with self.factory() as u:
            if w:
                worker=self._worker(u,e,w)
                if not worker['active']:raise NotFoundError('worker inactive')
            return self._detail(u,e,t,w)
    def overview(self,e,w=None):
        with self.factory() as u:
            if w:
                worker=self._worker(u,e,w)
                if not worker['active']:raise NotFoundError('worker inactive')
                tasks=rows(u,'SELECT t.id,a.rendered_text AS title,t.status,t.planned_date,t.due_at,t.category,t.greenhouse_id FROM workers.tasks t JOIN workers.assignments a ON a.task_id=t.id WHERE t.entity_id=:e AND a.worker_id=:w ORDER BY t.created_at DESC',e=e,w=w)
                return dict(tasks=tasks,worker=dict(id=worker['id'],display_name=worker['display_name'],language=worker['language']))
            tasks=rows(u,"SELECT t.*,COALESCE((SELECT jsonb_agg(a.worker_id) FROM workers.assignments a WHERE a.task_id=t.id),'[]'::jsonb) AS assigned_worker_ids FROM workers.tasks t WHERE entity_id=:e ORDER BY created_at DESC",e=e)
            attention=[];now=datetime.now(timezone.utc);today=now.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Istanbul')).date()
            for t in tasks:
                reasons=list(t['gaps']) if t['status']=='draft' else []
                if t['status'] not in ('completed','cancelled') and ((t['due_at'] and datetime.fromisoformat(t['due_at'])<now) or (not t['due_at'] and t['planned_date'] and t['planned_date']<today.isoformat())):reasons.append('Просрочено')
                if t['status']=='reported':reasons.append('Отчёт требует решения владельца')
                if t['status'] not in ('completed','cancelled') and rows(u,'SELECT id FROM workers.assignments WHERE task_id=:t AND acknowledged_at IS NULL',t=t['id']):reasons.append('Ожидает подтверждения работником')
                if rows(u,"SELECT r.id FROM workers.reports r WHERE r.task_id=:t AND r.cycle=:cycle AND r.state='submitted' AND r.claim='issue' AND NOT EXISTS(SELECT 1 FROM workers.actions a WHERE a.report_id=r.id AND a.kind='review_report')",t=t['id'],cycle=t['cycle']):reasons.append('Замечание требует проверки')
                if t['status']=='reopened':reasons.append('Возвращено в работу')
                late=rows(u,"SELECT r.* FROM workers.reports r WHERE r.task_id=:t AND (r.state='draft' OR r.claim IN ('note','issue') OR (r.received_at>COALESCE(CAST(:completed AS timestamptz),r.received_at) AND CAST(:completed AS timestamptz) IS NOT NULL))",t=t['id'],completed=t['completed_at'] or t['cancelled_at'])
                if any(not rows(u,"SELECT id FROM workers.actions WHERE task_id=:t AND ((report_id=:r AND kind='review_report') OR (kind IN ('complete','reopen') AND cycle=:c AND received_at>=CAST(:stamp AS timestamptz)))",t=t['id'],r=r['id'],c=r['cycle'],stamp=r['submitted_at'] or r['received_at']) for r in late):reasons.append('Новый или поздний отчёт требует проверки')
                if reasons:attention.append(dict(id=t['id'],title=t['title'],reasons=reasons))
            rules=rows(u,'SELECT r.* FROM workers.rules r WHERE entity_id=:e AND NOT EXISTS(SELECT 1 FROM workers.rules n WHERE n.replaces_id=r.id) ORDER BY created_at',e=e)
            today=now.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Istanbul')).date()
            applicable=rows(u,'SELECT r.* FROM workers.rules r WHERE entity_id=:e AND effective_from<=:d AND NOT EXISTS(SELECT 1 FROM workers.rules n WHERE n.entity_id=r.entity_id AND n.template_id=r.template_id AND n.version>r.version AND n.effective_from<=:d)',e=e,d=today)
            for r in applicable:
                if r['active'] and r['effective_from']<=today.isoformat() and (not r['effective_to'] or r['effective_to']>=today.isoformat()) and (r['frequency']=='daily' or r['weekday']==today.weekday()) and not any(t['template_id']==r['template_id'] and t['occurrence_date']==today.isoformat() for t in tasks):attention.append(dict(id=r['id'],title='Повтор не создан',reasons=['Создайте отдельную задачу на сегодня'],rule_id=r['id']))
            return dict(tasks=tasks,workers=rows(u,'SELECT * FROM workers.people WHERE entity_id=:e ORDER BY display_name',e=e),rules=rules,attention=attention,areas=rows(u,'SELECT * FROM core.work_areas WHERE entity_id=:e ORDER BY name',e=e))
