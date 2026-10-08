"""Immutable owner instructions, reviewed renderings and preserved reports."""
from datetime import date,datetime
from uuid import UUID
from typing import Literal
from pydantic import Field,model_validator
from .commands import StrictModel
Language=Literal['ru','tr','fa','uz']
class WorkerInput(StrictModel):
    display_name:str=Field(min_length=1,max_length=200)
    native_name:str|None=None
    language:Language='ru'
    contact:str|None=None
    telegram_ref:str|None=None
    notes:str|None=None
    source:str=Field(min_length=1,max_length=200)
class TaskInput(StrictModel):
    owner_language:Language='ru'
    title:str=Field(min_length=1,max_length=200)
    original_instruction:str=Field(min_length=1,max_length=10000)
    description:str=Field(min_length=1,max_length=10000)
    category:str=Field(default='general',min_length=1,max_length=100)
    greenhouse_id:UUID|None=None
    work_area_id:UUID|None=None
    priority:Literal['normal','high','urgent']='normal'
    planned_date:date|None=None
    due_at:datetime|None=None
    source:str=Field(default='owner:tasks',min_length=1,max_length=200)
    locator:str=Field(min_length=1,max_length=2000)
    source_timestamp:datetime|None=None
    gaps:list[str]=Field(default_factory=list,max_length=100)
    @model_validator(mode='after')
    def aware(self):
        for t in (self.due_at,self.source_timestamp):
            if t and t.tzinfo is None:raise ValueError('Timestamp must include timezone')
        return self
class AssignmentInput(StrictModel):
    worker_id:UUID
    rendered_text:str=Field(min_length=1,max_length=10000)
    language:Language
    translation_source:Literal['original','owner_reviewed_translation']='original'
    translation_version:str=Field(default='manual-r1',min_length=1,max_length=200)
    rendering_reviewed:Literal[True]
class ActionInput(StrictModel):
    kind:Literal['acknowledge','start','complete','reopen','cancel','submit_report','review_report']
    reason:str=Field(default='',max_length=2000)
    assignment_id:UUID|None=None
    report_id:UUID|None=None
    expected_version:int|None=Field(default=None,gt=0)
    expected_cycle:int|None=Field(default=None,gt=0)
    source:str=Field(default='owner:tasks-action',min_length=1,max_length=200)
    locator:str=Field(min_length=1,max_length=2000)
    source_timestamp:datetime|None=None
    @model_validator(mode='after')
    def aware(self):
        if self.source_timestamp and self.source_timestamp.tzinfo is None:raise ValueError('Timestamp must include timezone')
        return self
class ReportInput(StrictModel):
    assignment_id:UUID
    cycle:int=Field(gt=0)
    original_text:str=Field(min_length=1,max_length=10000)
    original_language:Language
    normalized:dict=Field(default_factory=dict)
    issues:list[str]=Field(default_factory=list)
    attachments:list[dict]=Field(default_factory=list,max_length=10)
    claim:Literal['done','issue','note']='done'
    source:str=Field(default='worker:mobile',min_length=1,max_length=200)
    locator:str=Field(min_length=1,max_length=2000)
    source_timestamp:datetime|None=None
    event_at:datetime|None=None
    @model_validator(mode='after')
    def facts(self):
        import json
        if len(json.dumps([self.normalized,self.attachments,self.issues]))>20000:raise ValueError('Report metadata limit')
        for t in (self.source_timestamp,self.event_at):
            if t and t.tzinfo is None:raise ValueError('Timestamp must include timezone')
        duration=self.normalized.get('duration_minutes')
        if duration is not None and (isinstance(duration,bool) or not isinstance(duration,int) or not 0<duration<=1440):raise ValueError('Explicit duration must be positive integer minutes')
        if 'water_volume' in self.normalized:raise ValueError('Do not infer water volume from operational duration')
        return self
class RuleInput(StrictModel):
    task:TaskInput
    frequency:Literal['daily','weekly']
    weekday:int|None=Field(default=None,ge=0,le=6)
    effective_from:date
    effective_to:date|None=None
    active:bool=True
    replaces_id:UUID|None=None
    @model_validator(mode='after')
    def frequency_check(self):
        if (self.frequency=='weekly')!=(self.weekday is not None):raise ValueError('Weekly needs an explicit weekday')
        if self.effective_to and self.effective_to<self.effective_from:raise ValueError('Invalid date range')
        return self
class OccurrenceInput(StrictModel):
    occurrence_date:date
class LinkInput(StrictModel):
    domain:Literal['inventory','harvest','finance','inbox']
    target_id:UUID
