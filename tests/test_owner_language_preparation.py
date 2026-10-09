"""Focused runtime safety regressions independent of benchmark labels/holdout."""
from datetime import date
from types import SimpleNamespace as Ref
from uuid import uuid4
import pytest
from greenhouse_owner_os.domain.owner_capture import DTO
from greenhouse_owner_os.services.owner_preparation import prepare_owner_source
from greenhouse_owner_os.services.preparation_language import (corrected,date_value,entity_matches,fold,money_amount,number,source_currency)

DAY=date(2026,10,9)

def ref(name,**kw):return Ref(id=uuid4(),name=name,active=True,**kw)

@pytest.fixture
def references():
    return dict(counterparties=[ref('Серкан Ремонт насосов'),ref('Kemal Bey'),ref('Умар')],
                areas=[ref('İmamlı',kind='greenhouse'),ref('Özvadi',kind='greenhouse'),ref('Yeşilöz',kind='greenhouse')],
                accounts=[ref('Cash TR',currency='TRY',account_type='cash'),ref('Bank TR',currency='TRY',account_type='bank')],
                workers=[dict(id=str(uuid4()),display_name=n,native_name=nn,active=True,language='fa') for n,nn in [('Умар','Umar'),('Инаят','Inayat'),('Асыф Газипаша','Asif Gazipasa'),('Асыф Алания','Asif Alanya')]],
                products=[dict(id=str(uuid4()),name='KNO3',aliases=['калийная селитра'],active=True),dict(id=str(uuid4()),name='Multilex',aliases=['Мультилекс'],active=True)],
                locations=[dict(id=str(uuid4()),name='TEST A',active=True),dict(id=str(uuid4()),name='TEST B',active=True)])

def prepare(raw,references):
    result=prepare_owner_source(raw,today=DAY,refs=references)
    for kind,payload,_ in result:DTO[kind].model_validate(payload)
    return result

@pytest.mark.parametrize(('raw','want'),[
    ('пять тысяч','5000'),('5 тысяч','5000'),('две тысячи пятьсот','2500'),('2,5 тысячи','2500'),('5 тысяч пятьсот','5500'),
    ('девятьсот девяносто девять','999'),('1 250,50','1250.50'),('полтора','1.5'),
    ('ноль',None),('ноль тысяч',None),('пять пять',None),('15 тысяч миллионов',None),('пятьдесят двадцать',None),('-500',None),('9999999999999999999',None),
])
def test_bounded_exact_number_grammar(raw,want):assert number(raw)==want

@pytest.mark.parametrize(('raw','want'),[
    ('Заплатил 5 тысяч лир','5000'),('Заплатил две тысячи пятьсот TL','2500'),('Оплатил 25000 TRY, половину аренды','25000'),
    ('Оплатил около 4000 долларов',None),('Должен 5000 TRY и 200 USD',None),('Заплатил половину',None),('За три дня сумму еще не посчитали',None),
    ('Чек за 18 июня оплатил неделю с 18 по 25 июня, за предыдущие 7 дней',None),('Заплатил 0 тысяч лир',None),
])
def test_money_does_not_consume_counts_dates_or_approximation(raw,want):assert money_amount(raw,implicit=True)==want

@pytest.mark.parametrize(('raw','want'),[('вчера','2026-10-08'),('позавчера','2026-10-07'),('послезавтра','2026-10-11'),('через две недели','2026-10-23'),('31.02.2026',None),('15 октября',None),('сегодня или завтра',None),('завтра или после завтра',None),('2026-12-31','2026-12-31')])
def test_explicit_dates_only(raw,want):assert date_value(raw,DAY,role='planned')[0]==want


def test_event_date_is_not_due_date():
    text='Вчера должен Серкану 5000 TRY, заплатить 15 октября 2026'
    assert date_value(text,DAY)[0]=='2026-10-08'
    assert date_value(text,DAY,role='due')[0]=='2026-10-15'

@pytest.mark.parametrize(('raw','currency'),[('500TL','TRY'),('500 лир','TRY'),('500USD','USD'),('5€','EUR'),('500 GBP',None),('500TRY и 2USD',None),('500','TRY')])
def test_currency_never_hides_explicit_alternative(raw,currency):assert source_currency(raw)==currency


def test_normalization_preserves_cyrillic_and_turkish_names(references):
    assert fold('İmamlı')=='imamli' and fold('Закрой')=='закрой'
    task=prepare('Умар завтра полить İmamlı',references)[0]
    assert task[0]=='WORKER_TASK' and task[1]['greenhouse_id']==str(references['areas'][0].id)
    # An unverified transliteration is not an identity alias.
    assert not entity_matches('Serkan',references['counterparties'],inflect=True)


def test_qualified_person_does_not_erase_other_ambiguous_person(references):
    found=entity_matches('Серкану и Kemal Bey',references['counterparties'],inflect=True)
    assert len(found)==2
    found=entity_matches('Асыф и Умар',references['workers'],'worker',inflect=True)
    assert len(found)==3
    found=entity_matches('Асыф Газипаша',references['workers'],'worker',inflect=True)
    assert len(found)==1 and found[0]['display_name']=='Асыф Газипаша'
    task=prepare('Асыф и Умар завтра проверить Имамлы',references)[0]
    assert task[1]['worker_id'] is None


def test_inactive_entity_and_ambiguous_accounts_are_not_selected(references):
    references['counterparties'][0].active=False
    references['accounts'].append(ref('Другая касса',currency='TRY',account_type='cash'))
    p=prepare('Заплатил Серкану 5000 TRY наличными',references)[0][1]
    assert p['counterparty_id'] is None and p['money_account_id'] is None

@pytest.mark.parametrize('text',[
    'Сегодня не оплатил Серкану 5000 TRY','Не выдал зарплату, только подготовил ведомость',
    'Перевел 20000 TRY из Cash TR в Bank TR','Перевел деньги между своими счетами',
    'Внес свои 50000 TRY в бизнес','Получил деньги от депо 5000 TRY',
    'Завтра привезли 10 кг KNO3 в TEST A','Пока не поливать Имамлы','Не списывать со склада 5 кг KNO3',
    'Наверное, нужно будет заплатить за воду','Серкан просит 5000 TRY','Дал Умару аванс 10000 TRY',
])
def test_no_actual_business_candidate_from_unsupported_or_negated_source(text,references):
    assert {kind for kind,_,_ in prepare(text,references)}=={'UNRESOLVED'}


def test_purchase_on_credit_is_not_paid_expense(references):
    p=prepare('Купил у Серкана KNO3 за 10000 лир в долг',references)
    assert [x[0] for x in p]==['PAYABLE'] and p[0][1]['amount']=='10000'
    assert p[0][1]['money_account_id'] is None


def test_generic_paid_party_stays_incomplete_debt_candidate(references):
    p=prepare('Заплатил Серкану 5000 TRY',references)[0]
    assert p[0]=='PAYMENT' and not p[1].get('payment_purpose') and not p[1].get('obligation_id')
    p=prepare('Закрыл долг Серкану 5000 TRY наличными',references)[0]
    assert p[1]['payment_purpose']=='debt' and not p[1].get('obligation_id')


def test_multi_event_and_unsupported_remainder_are_independent(references):
    raw='Серкан починил насос, я заплатил ему 7000 наличными'
    p=prepare(raw,references)
    assert [x[0] for x in p]==['UNRESOLVED','PAYMENT']
    assert p[0][1]['note']=='Серкан починил насос'
    p=prepare('Привезли 100 кг KNO3 в Имамлы, счет потом пришлют',references)
    assert [x[0] for x in p]==['STOCK_RECEIPT','UNRESOLVED']
    assert 'счет потом' in p[1][1]['note']
    assert not p[0][1]['lines'][0].get('unit_cost')
    p=prepare('Заплатил Серкану 5000 TRY, а Умару 3000 TRY',references)
    assert len(p)==2 and p[1][0]=='UNRESOLVED' and 'Умару 3000' in p[1][1]['note']


def test_independent_task_statements_and_coherent_sequence(references):
    p=prepare('Умар завтра полить Имамлы. Инаят завтра убрать Озвади',references)
    assert len(p)==2 and p[0][1]['worker_id']!=p[1][1]['worker_id']
    raw='Умар завтра польет Ешилез и вечером закроет окна'
    p=prepare(raw,references)
    assert len(p)==1 and p[0][1]['original_instruction']==raw and not p[0][1].get('rendered_text')

@pytest.mark.parametrize(('raw','field','want'),[
    ('Сегодня заплатил 5000 TRY, нет, 7000 TRY','amount','7000'),
    ('Должен Серкану не 5000, а 7000 TRY','amount','7000'),
    ('Умар не завтра, а послезавтра полить Имамлы','planned_date','2026-10-11'),
])
def test_explicit_correction_retains_original(raw,field,want,references):
    p=prepare(raw,references)[0]
    assert p[1][field]==want
    assert p[2][field]['normalization']=='explicit_same_source_correction'
    assert p[2][field]['source_text']==raw
    assert raw in p[1].values()


def test_stock_multiple_products_and_remaining_not_consumed(references):
    p=prepare('Сегодня внесли 8 кг KNO3 из TEST A в Имамлы, осталось 7 кг на завтра',references)[0]
    assert p[0]=='STOCK_APPLICATION' and len(p[1]['lines'])==1
    assert p[1]['lines'][0]['quantity']=='8' and p[1]['event_date']=='2026-10-09'
    p=prepare('Сегодня привезли KNO3 10 кг и Multilex 5 л в TEST A',references)[0]
    assert [ln['product_id'] for ln in p[1]['lines']]==[r['id'] for r in references['products']]
    assert [ln['unit'] for ln in p[1]['lines']]==['kg','L']


def test_depot_dated_header_and_unreadable_line(references):
    references['counterparties'].append(ref('Görgün Depo',counterparty_type='depot'))
    raw='Görgün Depo 09.10.2026\nGrand 10 100 20 2000\nAlacak 2000'
    p=prepare(raw,references)[0]
    assert p[0]=='DEPOT_RECEIPT' and not p[1]['unreadable_lines']
    assert p[1]['event_date']=='2026-10-09' and p[1]['alacak_value']=='2000'
    p=prepare(raw+'\nРабочие написали 12 веток',references)[0]
    assert p[1]['unreadable_lines']==['Рабочие написали 12 веток']


def test_two_explicit_worker_clauses_but_not_team_ambiguity(references):
    p=prepare('Умар завтра полить Имамлы и Инаят завтра убрать Озвади',references)
    assert len(p)==2 and p[0][1]['worker_id']!=p[1][1]['worker_id']
    p=prepare('Умар и Инаят завтра полить Имамлы',references)
    assert len(p)==1 and p[0][1]['worker_id'] is None


def test_money_identity_correction_is_not_split_as_second_payment(references):
    p=prepare('Должен не Серкану, а Kemal Bey 5000 TRY',references)
    assert len(p)==1 and p[0][1]['counterparty_id']==str(references['counterparties'][1].id)
    assert p[0][1]['amount']=='5000'


def test_question_is_not_payment_assertion(references):
    assert prepare('Ты заплатил Серкану 5000 TRY?',references)[0][0]=='UNRESOLVED'


def test_unknown_future_worker_subject_does_not_become_owner_task(references):
    p=prepare('Завтра Рустам Анталия починит пленку в Özvadi.',references)[0]
    assert p[0]=='WORKER_TASK' and p[1]['worker_id'] is None


def test_conflicting_account_channel_remains_unknown(references):
    p=prepare('Заплатил Серкану 5000 TRY наличными из Bank TR',references)[0]
    assert p[1]['money_account_id'] is None
    p=prepare('Заплатил Серкану 5000 TRY не наличными, с банка',references)[0]
    assert p[1]['money_account_id']==str(references['accounts'][1].id)


@pytest.mark.parametrize('prefix',['ну вот ','это самое ','смотри '])
def test_voice_whitespace_and_fillers_preserve_ambiguity_and_corrections(prefix,references):
    text=prefix+'Перевел 20000 TRY из Cash TR в Bank TR'
    assert prepare(text.replace(' ','  '),references)[0][0]=='UNRESOLVED'
    text=prefix+'Заплатил Серкану не 5000, а 7000 TRY не наличными, а из Bank TR'
    p=prepare(text.replace(' ','  '),references)[0][1]
    assert p['amount']=='7000' and p['money_account_id']==str(references['accounts'][1].id)
    p=prepare((prefix+'Завтра или после завтра разберусь с документами').replace(' ','  '),references)[0][1]
    assert p['planned_date'] is None


@pytest.mark.parametrize('text',[
    'И попробую закрыть половину задолженности по теплице',
    'Скажите какой минимум нужно внести?',
    '1900 отдам на протяжении двух недель, должен был раньше',
    'В банк сегодня не успею уже заехать',
    'Son gelen fatura tutarını yazarsanız sevinirim',
])
def test_financial_promises_and_document_requests_are_not_actuals(text,references):
    assert {kind for kind,_,_ in prepare(text,references)}=={'UNRESOLVED'}


def test_explicit_bill_list_and_paid_amount_not_equipment_count(references):
    p=prepare('Сегодняшние счета: 83000 один насос, 123000 второй насос',references)
    assert [s[0] for s in p]==['PAYABLE','PAYABLE']
    assert [s[1]['amount'] for s in p]==['83000','123000']
    p=prepare('Три месяца назад я заплатил 162000 (за два насоса)',references)[0][1]
    assert p['amount']=='162000' and p['event_date'] is None


@pytest.mark.parametrize('prefix',['в общем ','короче ','ага ','так вот '])
def test_lowercase_unknown_addressee_is_candidate_not_invented_identity(prefix,references):
    p=prepare(prefix+'рустам завтра убрать Озвади',references)[0]
    assert p[0]=='WORKER_TASK' and p[1]['worker_id'] is None
    p=prepare(prefix+'насос завтра починить в Озвади',references)[0]
    assert p[0]=='PERSONAL_TASK'


def test_credit_purchase_future_payment_is_due_not_event_date(references):
    p=prepare('Купил 10 кг KNO3 за 5000 TRY, деньги отдам завтра',references)[0]
    assert p[0]=='PAYABLE' and p[1]['event_date']=='2026-10-09' and p[1]['due_date']=='2026-10-10'
    assert p[1]['amount']=='5000'


def test_rejected_quantity_alternative_is_never_application_line(references):
    p=prepare('Сегодня внесли 10 кг KNO3, а не 15 кг, из TEST A в Имамлы',references)[0]
    assert p[0]=='STOCK_APPLICATION' and len(p[1]['lines'])==1
    assert p[1]['lines'][0]['quantity']=='10'
    assert 'не 15' in p[1]['notes']


@pytest.mark.parametrize('quantity',['около 100 кг','от 100 до 120 кг','между 100 и 120 кг','100–120 кг','примерно сто кг'])
def test_approximate_stock_never_becomes_exact_quantity(quantity,references):
    p=prepare('Сегодня привезли '+quantity+' KNO3 на TEST A',references)[0]
    assert p[0]=='STOCK_RECEIPT' and all(ln.get('quantity') is None for ln in p[1]['lines'])


def test_partial_payment_never_uses_full_debt_total(references):
    p=prepare('Серкану заплатил половину долга 10000 TRY наличными',references)[0]
    assert p[0]=='PAYMENT' and p[1]['amount'] is None
    assert prepare('Заплатил 5000 TRY, половину долга',references)[0][1]['amount']=='5000'


def test_depot_and_explicit_payment_are_independent(references):
    references['counterparties'].append(ref('Görgün Depo',counterparty_type='depot'))
    raw='Görgün Depo\n09.10.2026\nGrand 100 20 2000\nAlacak 2000\nОплатил 1000 наличными'
    p=prepare(raw,references)
    assert [s[0] for s in p]==['DEPOT_RECEIPT','PAYMENT']
    assert p[0][1]['alacak_value']=='2000' and p[1][1]['amount']=='1000'
    assert p[1][1]['counterparty_id'] is None


def test_account_not_inferred_from_later_negated_instruction(references):
    p=prepare('Заплатил Серкану 5000 TRY, он сказал не переводить с банка',references)
    assert [s[0] for s in p]==['PAYMENT','UNRESOLVED']
    assert p[0][1]['money_account_id'] is None and 'не переводить с банка' in p[1][1]['note']


def test_explicit_per_line_greenhouses_are_not_globally_collapsed(references):
    p=prepare('Сегодня внесли 10 кг KNO3 в Имамлы и 5 л Multilex в Озвади из TEST A',references)[0]
    assert [ln['greenhouse_id'] for ln in p[1]['lines']]==[str(a.id) for a in references['areas'][:2]]


@pytest.mark.parametrize('currency',['£','¥','₽','₹','AUD','PLN','KZT','A$'])
def test_unsupported_explicit_currency_is_never_try(currency,references):
    p=prepare('Купил топливо за 500'+currency,references)[0]
    assert p[0]=='DIRECT_EXPENSE' and p[1]['currency'] is None


@pytest.mark.parametrize(('quantity','amount'),[('5 тыс.','5000'),('2 млн.','2000000')])
def test_magnitude_abbreviation_does_not_detach_explicit_currency(quantity,amount,references):
    p=prepare('Заплатил Серкану '+quantity+' USD',references)
    assert len(p)==1 and p[0][1]['currency']=='USD' and p[0][1]['amount']==amount


@pytest.mark.parametrize('text',[
    'Это образец счета, не реальный долг 5400 TRY',
    'В инструкции есть слово «внести», применения не было',
    'Два документа смешаны, кто кому должен не разобрал',
    'В отчете написали «закрыть», нового поручения нет',
    'Вода должна идти самотеком, это описание устройства',
    'Товар вернули поставщику, это не новый приход',
])
def test_quoted_descriptions_and_nonfinancial_modality_stay_unresolved(text,references):
    assert {s[0] for s in prepare(text,references)}=={'UNRESOLVED'}


@pytest.mark.parametrize('text',[
    'Должен Серкану по счету 71529, саму сумму не знаю',
    'Оплатил Серкану, номер платежа 91421, сумму не записал',
    'Серкану должен остаток: скидка составляет 450 TRY, исходная сумма неизвестна',
])
def test_identifier_or_discount_is_not_the_transaction_amount(text,references):
    assert prepare(text,references)[0][1]['amount'] is None


def test_worker_modality_is_not_debt(references):
    p=prepare('Он завтра должен проверить Озвади',references)[0]
    assert p[0]=='WORKER_TASK' and p[1]['worker_id'] is None and p[1]['planned_date']=='2026-10-10'


def test_capacity_rate_disputed_quantity_and_batch_date_scope(references):
    for phrase in [
        'Привезли KNO3 в TEST B, мешок рассчитан на 20 кг, сколько внутри неизвестно',
        'Сегодня внесли KNO3 из TEST A в Имамлы по 4 кг на донюм, общая площадь не указана',
        'Сегодня внесли KNO3 в Имамлы: один говорит 8 кг, другой 12 кг',
    ]:
        p=prepare(phrase,references)[0]
        assert all(line.get('quantity') is None for line in p[1]['lines'])
    p=prepare('Сегодня получили 2 л Multilex в TEST A, дата партии 2025-11-01',references)[0]
    assert p[0]=='STOCK_RECEIPT' and p[1]['event_date']=='2026-10-09'
    p=prepare('Сегодня привезли один мешок KNO3 по 25 кг в TEST A',references)[0]
    assert len(p[1]['lines'])==1 and p[1]['lines'][0]['quantity']=='1' and p[1]['lines'][0]['unit']=='bag'


def test_preparation_version_is_additive_provenance_not_job_identity(references):
    from greenhouse_owner_os.services.owner_capture import ENGINE
    assert ENGINE=='owner-preparation-v1'
    for raw in ['Неизвестный текст','Должен Серкану не 5000, а 5400 TRY','Умар завтра полить Имамлы']:
        for _,payload,prov in prepare(raw,references):
            assert set(prov)==set(payload)
            assert all(v['interpretation_version']=='owner-language-preparation-r2-v3' for v in prov.values())


def test_iso_date_hyphens_are_not_an_amount_range(references):
    p=prepare('2026-10-09 заплатил Серкану 5000 TRY наличными',references)[0][1]
    assert p['amount']=='5000' and p['event_date']=='2026-10-09'
    assert money_amount('2026-10-09 заплатил от 5000 до 6000 TRY',implicit=True) is None


@pytest.mark.parametrize('raw',[
    '2026-10-10 заплатил Серкану 5000 TRY',
    '2026-10-10 привезли 20 кг KNO3 в TEST A',
    '2026-10-10 купил топливо за 500 TRY наличными',
])
def test_explicit_future_date_does_not_become_actual_paid_or_physical_event(raw,references):
    assert prepare(raw,references)[0][0]=='UNRESOLVED'


def test_payment_event_and_future_remainder_promise_have_separate_date_scope(references):
    p=prepare('Сегодня заплатил Серкану 5000 TRY, остаток отдам завтра',references)[0]
    assert p[0]=='PAYMENT' and p[1]['amount']=='5000' and p[1]['event_date']=='2026-10-09'


def test_long_unsupported_source_does_not_hide_late_remainder(references):
    last='Перевел 7000 TRY между своими счетами'
    raw=('Наблюдение без новых действий. '*110)+last
    p=prepare(raw,references)
    assert all(kind=='UNRESOLVED' and len(payload['note'])<=2000 for kind,payload,_ in p)
    assert any(last in payload['note'] for _,payload,_ in p)
    raw=('Непонятное наблюдение '*170)+'конечный факт'
    p=prepare(raw,references)
    assert ''.join(payload['note'] for _,payload,_ in p)==raw


@pytest.mark.parametrize('raw',[
    'Серкан заплатил мне 5000 TRY',
    'Мне перевели 5000 TRY из Bank TR',
    'Серкан перевел 5000 TRY мне',
    'Перевел себе 5000 TRY из Bank TR',
])
def test_incoming_or_self_recipient_is_not_outgoing_debt_payment(raw,references):
    assert prepare(raw,references)[0][0]=='UNRESOLVED'


def test_counterparty_dative_stays_outgoing_payment(references):
    p=prepare('Серкану заплатил 5000 TRY наличными',references)[0]
    assert p[0]=='PAYMENT' and p[1]['counterparty_id']==str(references['counterparties'][0].id)
    assert p[1]['amount']=='5000'


def test_scaled_number_precision_is_local_exact_and_bounded():
    from decimal import getcontext
    before=getcontext().prec
    tiny='1.'+'0'*35+'1 тысяч'
    assert number(tiny,2) is None and number(tiny,12) is None
    assert number('123456789012345.123456789012345 тысяч',12)=='123456789012345123.456789012345'
    assert number('9'*10001+' тысяч') is None
    assert number(1.25) is None
    assert getcontext().prec==before

# Exposed-regression remediation: general grammar contrast pairs, authored
# independently of the sealed first-evaluation assertions.
@pytest.mark.parametrize('source',[
    'Привез ли поставщик 24 кг KNO3 на TEST B?',
    'Склад TEST B принял KNO3: 24 кг?',
    'Если склад TEST B принял KNO3, уточним количество',
    'На TEST B не приняли поставку KNO3 24 кг',
    'KNO3 не оприходован физически на TEST B',
    'Перечислил предоплату Серкану 4200 TRY',
    'Платеж Серкану будет завершен позже: 4200 TRY',
    'Если оплата Серкану прошла, сверим 4200 TRY',
    'Оплата пока не прошла ни с первого, ни со второго раза',
    'Я Умару объяснял, как нужно открывать окна',
    'Я тебе напишу, чтобы ты мог приехать забрать товар',
    'Хотел напомнить: завтра выплата уже записана в плане',
])
def test_evidence_mood_is_not_an_actual_effect_or_new_instruction(source,references):
    assert {k for k,_,_ in prepare(source,references)}=={'UNRESOLVED'}

@pytest.mark.parametrize('source',[
    'На TEST B сегодня приняли поставку KNO3: 24 кг',
    'KNO3 24 кг оприходован физически на TEST B сегодня',
    'Полученная сегодня партия на TEST B: KNO3 24 кг',
    'Сегодня склад TEST B принял KNO3: 24 кг',
])
def test_completed_receipt_alternative_grammar_prefills_same_facts(source,references):
    p=prepare(source,references)
    assert len(p)==1 and p[0][0]=='STOCK_RECEIPT'
    fields=p[0][1];line=fields['lines'][0]
    assert fields['event_date']==DAY.isoformat()
    assert line['product_id']==references['products'][0]['id'] and line['quantity']=='24'
    assert line['location_id']==references['locations'][1]['id'] and not line.get('unit_cost')


def test_explicit_personal_audience_overrides_negated_worker_mention(references):
    source='Для меня, не для работника: завтра отвезти образцы из Имамлы'
    p=prepare(source,references)[0]
    assert p[0]=='PERSONAL_TASK' and p[1]['worker_id'] is None
    assert p[1]['greenhouse_id']==str(references['areas'][0].id)
    p=prepare('Для Умара: завтра отвезти образцы из Имамлы',references)[0]
    assert p[0]=='WORKER_TASK' and p[1]['worker_id']==references['workers'][0]['id']
    # A numbered task is not itself evidence of a hidden chat recipient.
    p=prepare('Задача номер 7: проверить насос',references)[0]
    assert p[0]=='PERSONAL_TASK' and p[1]['worker_id'] is None


def test_passive_settlement_and_explicit_unpaid_balance_are_distinct(references):
    p=prepare('Погашение долга Серкану проведено вчера с Bank TR, перечислено 4200 TRY',references)
    assert len(p)==1 and p[0][0]=='PAYMENT'
    assert p[0][1]['amount']=='4200' and p[0][1]['event_date']=='2026-10-08'
    assert p[0][1]['counterparty_id']==str(references['counterparties'][0].id)
    assert p[0][1]['money_account_id']==str(references['accounts'][1].id)
    assert p[0][1]['payment_purpose']=='debt' and not p[0][1].get('obligation_id')
    p=prepare('Мой долг Серкану составляет 4200 TRY, еще не оплачено',references)
    assert len(p)==1 and p[0][0]=='PAYABLE' and p[0][1]['amount']=='4200'


def test_same_event_metadata_keeps_exact_original_and_context(references):
    source='Серкан получил от меня вчера 4200 TRY. Деньги отправлены с Bank TR в погашение долга.'
    p=prepare(source,references)
    assert len(p)==1 and p[0][0]=='PAYMENT' and p[0][1]['description']==source
    assert p[0][1]['event_date']=='2026-10-08'
    source='Использование подтверждаю: вчера из TEST B взяли и внесли в Озвади KNO3, 24 кг'
    p=prepare(source,references)
    assert len(p)==1 and p[0][0]=='STOCK_APPLICATION' and p[0][1]['notes']==source
    assert p[0][1]['event_date']=='2026-10-08'
    assert p[0][1]['lines'][0]['location_id']==references['locations'][1]['id']
    # Explicit independent events are never merged merely because facts match.
    p=prepare('Вчера заплатил Серкану 4200 TRY. Сегодня заплатил Серкану 4200 TRY',references)
    assert [x[0] for x in p]==['PAYMENT','PAYMENT']
    assert [x[1]['event_date'] for x in p]==['2026-10-08','2026-10-09']


@pytest.mark.parametrize('source',[
    'За бензин вчера отдал 800 TRY с Bank TR',
    'На заправке вчера рассчитался за топливо: 800 TRY, из Bank TR',
    'Бензин куплен и оплачен вчера; по кассе Bank TR расход 800 TRY',
])
def test_explicit_paid_fuel_is_direct_expense(source,references):
    p=prepare(source,references)
    assert len(p)==1 and p[0][0]=='DIRECT_EXPENSE'
    assert p[0][1]['amount']=='800' and p[0][1]['event_date']=='2026-10-08'


def test_depot_prose_labels_do_not_shift_columns_or_turn_document_into_cash(references):
    references['counterparties'].append(ref('Görgün Depo',counterparty_type='depot'))
    source='Görgün Depo выдал приемку 08.10.2026. Birinci: количество 6, вес 72 кг, цена 15 TRY, всего 1080 TRY. Alacak 1080.'
    p=prepare(source,references)
    assert len(p)==1 and p[0][0]=='DEPOT_RECEIPT'
    fields=p[0][1];line=fields['lines'][0]
    assert fields['event_date']=='2026-10-08' and fields['qualities']==['FIRST']
    assert (line['count'],line['net_weight_kg'],line['unit_price'],line['stated_amount'])==(6,'72','15','1080')
    assert fields['alacak_value']==fields['stated_total']=='1080'
    assert p[0][2]['stated_total']['category']==p[0][2]['qualities']['category']=='INFERENCE'
    assert not fields.get('arithmetic_reviewed') and not fields.get('quality_reviewed')
    p=prepare(source+'\nОплатил Серкану 500 TRY наличными',references)
    assert [x[0] for x in p]==['DEPOT_RECEIPT','PAYMENT']

@pytest.mark.parametrize('source',[
    'Планирую погасить долг Серкану 2300 TRY завтра',
    'Хочу отдать долг Серкану 2300 TRY',
    'Завтра погашу долг Серкану 2300 TRY',
    'Нужно будет оплатить Серкану 2300 TRY через неделю',
    'Не погасил долг Серкану 2300 TRY',
    'Собираюсь погасить долг Серкану 2300 TRY, еще не оплатил',
    'Серкан сказал: «Я заплатил 2300 TRY. Потом перевел 400 TRY».',
    'По словам Серкана, он оплатил 2300 TRY за насос',
])
def test_financial_speaker_intention_and_negation_precede_route(source,references):
    assert {k for k,_,_ in prepare(source,references)}=={'UNRESOLVED'}


def test_current_debt_and_future_due_date_survive_intention_scope(references):
    source='Должен Серкану 2300 TRY, отдам завтра'
    p=prepare(source,references)[0]
    assert p[0]=='PAYABLE' and p[1]['amount']=='2300' and p[1]['due_date']=='2026-10-10'
    p=prepare('Сегодня заплатил Серкану 2300 TRY, остаток отдам завтра',references)[0]
    assert p[0]=='PAYMENT' and p[1]['event_date']==DAY.isoformat()
    p=prepare('Вчера погасила часть долга Серкану: 2300 TRY наличными',references)[0]
    assert p[0]=='PAYMENT' and p[1]['amount']=='2300' and p[1]['payment_purpose']=='debt'
    p=prepare('Из долга Серкану 18000 TRY вчера оплатил только 2300 TRY наличными',references)[0]
    assert p[1]['amount']=='2300' and p[1]['event_date']=='2026-10-08'


def test_quoted_report_does_not_contaminate_separate_owner_payment(references):
    source='Серкан сказал: «Я заплатил 2300 TRY. Потом перевел 400 TRY». Сегодня я заплатил Kemal Bey 900 TRY'
    p=prepare(source,references)
    assert [x[0] for x in p]==['UNRESOLVED','PAYMENT']
    assert 'Потом перевел 400 TRY' in p[0][1]['note']
    assert p[1][1]['amount']=='900' and p[1][1]['counterparty_id']==str(references['counterparties'][1].id)


def test_purchase_settlement_continuation_and_independent_payment(references):
    p=prepare('Вчера купил фильтр за 900 TRY и оплатил наличными; Умар завтра проверить насос',references)
    assert [x[0] for x in p]==['DIRECT_EXPENSE','WORKER_TASK']
    assert p[0][1]['event_date']=='2026-10-08' and p[0][1]['money_account_id']==str(references['accounts'][0].id)
    p=prepare('Купил фильтр за 900 TRY и заплатил Серкану долг 400 TRY',references)
    assert [x[0] for x in p]==['DIRECT_EXPENSE','PAYMENT']
    assert p[1][1]['amount']=='400'
    p=prepare('Серкан починил насос, остался долг Серкану 2300 TRY',references)
    assert [x[0] for x in p]==['UNRESOLVED','PAYABLE'] and 'починил насос' in p[0][1]['note']


def test_explicit_enumeration_preserves_equal_separate_payment_slots(references):
    source='Вчера дважды заплатил Серкану в счет долга с Bank TR: первый платеж 400 TRY, второй платеж тоже 400 TRY'
    p=prepare(source,references)
    assert [x[0] for x in p]==['PAYMENT','PAYMENT']
    assert all(x[1]['amount']=='400' and x[1]['event_date']=='2026-10-08' and x[1]['payment_purpose']=='debt' for x in p)
    assert all(x[2]['amount']['scope']['shared_context'].startswith('Вчера дважды') for x in p)
    assert {k for k,_,_ in prepare(source.replace('дважды','трижды'),references)}=={'UNRESOLVED'}
    assert {k for k,_,_ in prepare(source.replace('второй платеж','первый платеж'),references)}=={'UNRESOLVED'}


def test_explicit_receipts_keep_separate_source_boundaries(references):
    references['counterparties'].append(ref('Görgün Depo',counterparty_type='depot'))
    source='Два разных чека Görgün Depo от 08.10.2026. Чек первый: Birinci 30 кг по 20 TRY сумма 600 TRY. Чек второй: Birinci 40 кг по 15 TRY сумма 600 TRY.'
    p=prepare(source,references)
    assert [x[0] for x in p]==['DEPOT_RECEIPT','DEPOT_RECEIPT']
    assert [x[1]['lines'][0]['net_weight_kg'] for x in p]==['30','40']
    assert all(len(x[1]['lines'])==1 and x[1]['event_date']=='2026-10-08' for x in p)
    assert all(not x[1].get('arithmetic_reviewed') for x in p)


def test_positive_instruction_and_receipt_keep_scoped_negative_constraints(references):
    source='Умар завтра внести 3 кг KNO3 в Имамлы, со склада пока не списывать'
    p=prepare(source,references)
    assert len(p)==1 and p[0][0]=='WORKER_TASK' and p[0][1]['original_instruction']==source
    p=prepare('Сегодня привезли 3 кг KNO3 на TEST A, но Multilex не привезли',references)
    assert len(p)==1 and p[0][0]=='STOCK_RECEIPT'
    assert len(p[0][1]['lines'])==1 and p[0][1]['lines'][0]['product_id']==references['products'][0]['id']
    assert 'Multilex не привезли' in p[0][1]['notes']
    assert {k for k,_,_ in prepare('Сегодня привезли 3 кг KNO3, но KNO3 не привезли',references)}=={'UNRESOLVED'}


def test_currency_self_correction_retains_original_and_unknown_account(references):
    source='Сегодня погасил долг Серкану: заплатил 400 не долларов, а евро наличными'
    p=prepare(source,references)[0]
    assert p[0]=='PAYMENT' and p[1]['amount']=='400' and p[1]['currency']=='EUR'
    assert p[1]['money_account_id'] is None and p[1]['description']==source
    assert p[2]['currency']['normalization']=='explicit_same_source_correction'

@pytest.mark.parametrize('source',[
    'Он вчера заплатил 2300 TRY за насос',
    'Серкан вчера заплатил 2300 TRY за насос',
    'Серкан говорит я заплатил 2300 TRY',
    'Серкан сказал Умару, что заплатил 2300 TRY',
])
def test_explicit_other_payer_cannot_inherit_owner_authority(source,references):
    assert {k for k,_,_ in prepare(source,references)}=={'UNRESOLVED'}


def test_dative_recipient_and_explicit_from_me_are_outgoing(references):
    for source in ['Серкану вчера заплатил 2300 TRY','Серкан получил от меня вчера 2300 TRY']:
        p=prepare(source,references)[0]
        assert p[0]=='PAYMENT' and p[1]['amount']=='2300'


def test_explicit_future_personal_task_has_no_financial_actual(references):
    p=prepare('Завтра перевести деньги Серкану',references)
    assert len(p)==1 and p[0][0]=='PERSONAL_TASK'
    assert p[0][1]['planned_date']=='2026-10-10'

@pytest.mark.parametrize('source',[
    'Kemal Bey вчера заплатил 2600 TRY за ремонт',
    'ASAT оплатил, чек номер 1800, сумму посмотрю позже',
    'Умар сообщил о вчерашнем платеже: заплатил 2600 TRY',
    "Серкан сказал 'я заплатил 2600 TRY. Перевел еще 300 TRY'",
    'Kemal Bey: «Я вчера заплатил 2600 TRY»',
    'Серкан рассказал про перевод: я перевел 2600 TRY',
])
def test_payer_and_report_scope_is_script_and_quote_independent(source,references):
    references['counterparties'].append(ref('ASAT'))
    assert {k for k,_,_ in prepare(source,references)}=={'UNRESOLVED'}


def test_owner_actor_and_received_from_owner_remain_supported(references):
    for source in ['Я вчера заплатил Kemal Bey 2600 TRY','Kemal Bey получил от меня вчера 2600 TRY','Серкану вчера заплатил 2600 TRY']:
        p=prepare(source,references)
        assert len(p)==1 and p[0][0]=='PAYMENT' and p[0][1]['event_date']=='2026-10-08'

@pytest.mark.parametrize('labels',[('A','B'),('А','Б'),('Север','Юг')])
def test_explicit_document_labels_are_independent_of_ordinal_grammar(labels,references):
    references['counterparties'].append(ref('Görgün Depo',counterparty_type='depot'))
    a,b=labels
    source=f'Görgün Depo 08.10.2026. Два чека: чек {a}: Grand 30 кг по 20 TRY сумма 600 TRY; чек {b}: Grand 40 кг по 15 TRY сумма 600 TRY.'
    p=prepare(source,references)
    assert [x[0] for x in p]==['DEPOT_RECEIPT','DEPOT_RECEIPT']
    assert [x[1]['lines'][0]['net_weight_kg'] for x in p]==['30','40']
    assert all(x[1]['event_date']=='2026-10-08' for x in p)


def test_unlabelled_explicit_multiple_receipts_remain_reviewable(references):
    source='Два чека Görgün Depo 08.10.2026: Grand 30 20 600; Birinci 40 15 600'
    p=prepare(source,references)
    assert [x[0] for x in p]==['UNRESOLVED'] and p[0][1]['note']==source

@pytest.mark.parametrize('source',[
    'Серкан должен Kemal Bey 2600 TRY',
    'Мне Серкан должен 2600 TRY',
    'Мне должен Серкан 2600 TRY',
    'Серкан должен мне 2600 TRY',
    'Kemal Bey обязан заплатить Серкану 2600 TRY',
])
def test_third_party_debt_and_owner_receivable_never_become_owner_payable(source,references):
    assert {k for k,_,_ in prepare(source,references)}=={'UNRESOLVED'}


def test_explicit_owner_and_dative_creditor_remain_payable(references):
    for source in ['Я должен Серкану 2600 TRY','Серкану должен 2600 TRY','Kemal Bey я должен 2600 TRY']:
        p=prepare(source,references)
        assert len(p)==1 and p[0][0]=='PAYABLE' and p[0][1]['amount']=='2600'

@pytest.mark.parametrize('source',[
    'Kemal Bey вчера купил фильтр за 2600 TRY',
    'Умар вчера потратил 2600 TRY на бензин',
    'Долг Серкана 2600 TRY',
    'Долг Kemal Bey 2600 TRY',
    'Долг мне 2600 TRY',
])
def test_purchase_and_nominal_debt_share_conservative_actor_scope(source,references):
    assert {k for k,_,_ in prepare(source,references)}=={'UNRESOLVED'}


def test_owner_purchase_and_dative_nominal_debt_keep_known_facts(references):
    p=prepare('Я вчера купил фильтр за 2600 TRY',references)[0]
    assert p[0]=='DIRECT_EXPENSE' and p[1]['amount']=='2600'
    p=prepare('Долг Серкану 2600 TRY',references)[0]
    assert p[0]=='PAYABLE' and p[1]['counterparty_id']==str(references['counterparties'][0].id)


def test_depot_document_object_does_not_bypass_financial_actor_scope(references):
    references['counterparties'].append(ref('Görgün Depo',counterparty_type='depot'))
    receipt='Görgün Depo выдал приемку 08.10.2026\nGrand 30 кг по 20 TRY сумма 600 TRY'
    p=prepare(receipt,references)
    assert [x[0] for x in p]==['DEPOT_RECEIPT'] and p[0][1]['lines'][0]['net_weight_kg']=='30'
    for remainder in ['Серкан должен Kemal Bey 2600 TRY','Kemal Bey заплатил 2600 TRY за насос','Мне Серкан должен 2600 TRY']:
        p=prepare(receipt+'\n'+remainder,references)
        assert [x[0] for x in p]==['DEPOT_RECEIPT','UNRESOLVED']
        assert p[0][1]['lines'][0]['net_weight_kg']=='30' and remainder in p[1][1]['note']
    p=prepare(receipt+'\nЯ заплатил Серкану 300 TRY наличными',references)
    assert [x[0] for x in p]==['DEPOT_RECEIPT','PAYMENT'] and p[1][1]['amount']=='300'
    mixed='Görgün Depo Grand 30 кг по 20 TRY сумма 600 TRY и Серкан должен Kemal Bey 2600 TRY'
    p=prepare(mixed,references)
    assert [x[0] for x in p]==['UNRESOLVED'] and p[0][1]['note']==mixed

@pytest.mark.parametrize('actor',['Артур','murat','Hasan Kaya','حسن'])
@pytest.mark.parametrize('statement',['вчера заплатил 2300 TRY за ремонт','должен Серкану 2300 TRY','вчера купил насос за 2300 TRY'])
def test_unbound_preposed_actor_is_not_owner_proof(actor,statement,references):
    assert {k for k,_,_ in prepare(actor+' '+statement,references)}=={'UNRESOLVED'}

@pytest.mark.parametrize('source',['Долг Артура 2300 TRY','Долг Hasan Kaya 2300 TRY','Артура долг 2300 TRY'])
def test_unknown_nominal_debt_possessor_is_not_owner_proof(source,references):
    assert {k for k,_,_ in prepare(source,references)}=={'UNRESOLVED'}


def test_actor_proof_preserves_owner_ellipsis_and_dative_creditor(references):
    for source in ['Я вчера заплатил 2300 TRY за ремонт','Заплатил вчера 2300 TRY за ремонт','Серкану вчера заплатил 2300 TRY в счет долга']:
        p=prepare(source,references)
        assert len(p)==1 and p[0][0]=='PAYMENT' and p[0][1]['amount']=='2300'

@pytest.mark.parametrize('known',[False,True])
@pytest.mark.parametrize('actor',['Борис','murat','Hasan Kaya','حسن','Repair 24 LLC'])
@pytest.mark.parametrize('statement',[
    'вчера заплатил 1700 TRY за ремонт',
    'за ремонт вчера заплатил 1700 TRY',
    'вчера купил насос за 1700 TRY',
    'должен Серкану 1700 TRY',
    'вчера получил invoice на 1700 TRY',
])
def test_financial_ownership_does_not_depend_on_reference_membership(actor,statement,known,references):
    if known:references['counterparties'].append(ref(actor))
    raw=actor+' '+statement
    slots=prepare(raw,references)
    assert [s[0] for s in slots]==['UNRESOLVED']
    assert slots[0][1]['note']==raw

@pytest.mark.parametrize('source',[
    'Долг Бориса 1700 TRY, еще не оплачено',
    'Долг Hasan Kaya 1700 TRY, пока не оплачен',
    'Бориса долг 1700 TRY, еще не оплачено',
    'Долг Бориса 1700 TRY',
    'Долг мне 1700 TRY, еще не оплачен',
])
def test_nominal_debt_direction_precedes_passive_payment_qualification(source,references):
    assert [s[0] for s in prepare(source,references)]==['UNRESOLVED']

@pytest.mark.parametrize('next_event',['купил насос за 500 TRY','заплатил Серкану 500 TRY','должен Серкану 500 TRY'])
def test_third_party_financial_actor_survives_coordinated_ellipsis(next_event,references):
    raw='Борис вчера заплатил 1700 TRY за ремонт и '+next_event
    slots=prepare(raw,references)
    assert [s[0] for s in slots]==['UNRESOLVED'] and slots[0][1]['note']==raw


def test_explicit_owner_resets_actor_and_keeps_positive_financial_routes(references):
    raw='Борис вчера заплатил 1700 TRY за ремонт, я купил насос за 500 TRY'
    slots=prepare(raw,references)
    assert [s[0] for s in slots]==['UNRESOLVED','DIRECT_EXPENSE']
    assert slots[1][1]['amount']=='500'
    for raw,kind in [
        ('Я вчера заплатил 1700 TRY за ремонт','PAYMENT'),
        ('Заплатил вчера 1700 TRY за ремонт','PAYMENT'),
        ('Серкану вчера заплатил 1700 TRY в счет долга','PAYMENT'),
        ('Серкану за ремонт вчера заплатил 1700 TRY','PAYMENT'),
        ('Мой долг Серкану 1700 TRY, еще не оплачено','PAYABLE'),
        ('Долг Серкану 1700 TRY','PAYABLE'),
        ('Серкану долг 1700 TRY','PAYABLE'),
        ('Я вчера получил invoice на 1700 TRY','PAYABLE'),
    ]:
        slots=prepare(raw,references)
        assert slots[0][0]==kind and slots[0][1]['amount']=='1700'

@pytest.mark.parametrize('prefix',['В учете нужен','Учесть','Отражен'])
@pytest.mark.parametrize('role',['поставщику','подрядчику','исполнителю'])
def test_explicit_accounting_creditor_role_is_not_nominal_possessor(prefix,role,references):
    creditor=ref('Atlas Servis');references['counterparties'].append(creditor)
    raw=f'{prefix} долг {role} Atlas Servis: 6400 TRY за уже выполненную работу.'
    slots=prepare(raw,references)
    assert len(slots)==1 and slots[0][0]=='PAYABLE'
    assert slots[0][1]['amount']=='6400' and slots[0][1]['counterparty_id']==str(creditor.id)
    assert slots[0][1]['description']==raw

@pytest.mark.parametrize(('frame','completed'),[('Погашение','проведено'),('Закрытие','завершено'),('Выплата','произведена')])
@pytest.mark.parametrize('partial',['','части '])
def test_completed_nominal_settlement_keeps_bound_creditor_and_paid_facts(frame,completed,partial,references):
    creditor=ref('Rotor AS');references['counterparties'].append(creditor)
    raw=f'{frame} {partial}долга Rotor AS {completed} вчера с Bank TR, перечислено 1300 TRY.'
    slots=prepare(raw,references)
    assert len(slots)==1 and slots[0][0]=='PAYMENT'
    fields=slots[0][1]
    assert fields['amount']=='1300' and fields['currency']=='TRY' and fields['event_date']=='2026-10-08'
    assert fields['counterparty_id']==str(creditor.id) and fields['money_account_id']==str(references['accounts'][1].id)
    assert fields['payment_purpose']=='debt' and not fields.get('obligation_id')
    assert fields['description']==raw

@pytest.mark.parametrize('raw',[
    'Борис долг поставщику Atlas Servis 6400 TRY',
    'Долг поставщика Atlas Servis 6400 TRY',
    'Борис долг Серкану 6400 TRY',
    'Погашение долга Бориса проведено вчера с Bank TR, перечислено 1300 TRY.',
    'Погашение части долга Бориса проведено вчера с Bank TR, перечислено 1300 TRY.',
    'Борис погасил часть долга Rotor AS, перечислено 1300 TRY.',
    'Погашение части долга Rotor AS не проведено вчера с Bank TR, перечислено 1300 TRY.',
    'Погашение части долга Rotor AS будет проведено завтра, перечислено 1300 TRY.',
])
def test_creditor_and_settlement_frames_do_not_authorize_other_debtors(raw,references):
    references['counterparties'] += [ref('Atlas Servis'),ref('Rotor AS'),ref('Борис')]
    assert [s[0] for s in prepare(raw,references)]==['UNRESOLVED']


def test_explicit_creditor_role_does_not_invent_unknown_reference(references):
    raw='В учете нужен долг поставщику Unknown Workshop: 6400 TRY за выполненную работу.'
    slots=prepare(raw,references)
    assert slots[0][0]=='PAYABLE' and slots[0][1]['amount']=='6400'
    assert slots[0][1]['counterparty_id'] is None
    assert slots[0][1]['description']==raw

@pytest.mark.parametrize('verb',['возник','образовался','сформировался','появился','остался'])
@pytest.mark.parametrize('subject',['','у меня ','у нас '])
def test_explicit_obligation_accrual_keeps_event_and_due_dates(verb,subject,references):
    raw=f'Вчера {subject}{verb} долг Серкану 4200 TRY за ремонт, оплатить до послезавтра.'
    slots=prepare(raw,references)
    assert len(slots)==1 and slots[0][0]=='PAYABLE'
    fields=slots[0][1]
    assert fields['amount']=='4200' and fields['currency']=='TRY'
    assert fields['event_date']=='2026-10-08' and fields['due_date']=='2026-10-11'
    assert fields['counterparty_id']==str(references['counterparties'][0].id)
    assert fields['description']==raw

@pytest.mark.parametrize('verb',['возник','образовался','сформировался','появился','остался'])
@pytest.mark.parametrize('subject',['у Бориса','у Hasan Kaya','у него'])
def test_third_party_obligation_accrual_cannot_be_owner_payable(verb,subject,references):
    raw=f'Вчера {subject} {verb} долг Серкану 4200 TRY за ремонт.'
    assert [s[0] for s in prepare(raw,references)]==['UNRESOLVED']

@pytest.mark.parametrize('raw',[
    'Вчера возник долг Бориса 4200 TRY за ремонт.',
    'Вчера образовался долг поставщика 4200 TRY за ремонт.',
    'Вчера у Бориса остался долг поставщику Atlas Servis 4200 TRY.',
    'Борис подтвердил, что возник долг Серкану 4200 TRY.',
])
def test_accrual_frame_does_not_override_possessor_or_report_scope(raw,references):
    assert [s[0] for s in prepare(raw,references)]==['UNRESOLVED']
