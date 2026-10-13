"""Bounded local linguistic evidence for a task's addressee, never identity.

Dictionary-only OpenCorpora lookups: no prediction, suffix guessing, probability
threshold, network call or business reference creation. The pinned lexicon's
known Name analysis may establish a human audience; it never supplies a worker
ID. A compatible ordinary-word/place reading keeps the audience unresolved.
"""
import re
from functools import lru_cache

from .preparation_language import fold, names

LEXICON_BASIS = {
    'source': 'OpenCorpora', 'source_revision': '417150',
    'analyzer': 'pymorphy3', 'analyzer_version': '2.0.6',
    'dictionary_package': 'pymorphy3-dicts-ru', 'dictionary_version': '2.4.417150.4580142',
    'rule': 'dictionary_known_leading_recipient_v1',
}
# Travel by a directly addressed person supplies stronger discourse evidence
# than an imperative about inspecting an object-topic (including an OOV typo).
PERSON_MOVEMENT_IMPERATIVE = r'заедь(?:те)?|заедьте|езжай(?:те)?|приходи(?:те)?'


def task_actor_scope(raw, *, workers=()):
    """Exclude recipients/objects of contact, help or recruitment actions.

    Keep an explicit preceding actor, e.g. Умар позвонить Инаяту. A contact
    object is not an executor, even when it is an exact registered person.
    This is only an exclusion view; source text is never rewritten.
    """
    text = fold(raw)
    delegation = re.search(r'\b(?:поручаю|поручи(?:ть|те)?|назначаю|назначь(?:те)?)\b', text)
    if delegation:
        header = re.match(r'^\s*(?:исполнитель|работник)\b[^.!?;]{1,120}[.!?;]', text[:delegation.start()])
        text = (header.group() + ' ' if header else '') + text[delegation.start():]
    contact_action = r'позвон(?:ить|и(?:те)?|ю)|помочь|помогать|помоги(?:те)?|отправить|отправь(?:те)?|прислать|пришли(?:те)?|написать|напиши(?:те)?|передать|передай(?:те)?'
    receiver_action = contact_action + r'|связаться|найти|искать|вызвать|вызывать|спросить|письмо|сообщение'
    # A short status clause about a person does not make that person the actor
    # of a later instruction. Dictionary grammar supplies this veto, not a
    # growing word/name blacklist. An actual instruction in the clause wins.
    first = re.split(r'[,;]', text, maxsplit=1)
    if len(first) > 1:
        words = re.findall(r'[а-яё]+', first[0])
        if any(_status_predicate(word) for word in words) and not any(_instruction_predicate(word) for word in words):
            text = first[1].lstrip()
    # Positive actor boundary: an ordinary operative predicate ends the
    # potential actor frame. Later people may be possessions, locations or
    # recipients. Delegation/reminder verbs instead introduce their recipient.
    for word in re.finditer(r'[а-яё]+', text):
        # A future third-person clause may place its explicit role subject
        # after the infinitive: будет резать брат…. This is not an object.
        postposed_role = (re.search(r'\bбуд(?:ет|ут)\s*$', text[:word.start()]) and
                          re.match(r'\s+(?:брат|мастер|работник(?:и)?|ребята)(?!\w)', text[word.end():]))
        task_registration = word.group() in ('запиши', 'запишите') and re.search(r'\b(?:поручение|задачу)\b', text[word.end():])
        if postposed_role or task_registration:
            continue
        if _instruction_predicate(word.group()) and not any(p.normal_form in
                ('поручить', 'поручать', 'назначить', 'назначать', 'напомнить', 'попросить', 'просить', 'сказать', 'говорить')
                for p in _analyses(word.group())):
            text = text[:word.end()]
            break
    # Inflected registered names before that boundary are not automatically
    # actors either: permit explicit recipient framing, not possessive genitive.
    current_names = {fold(alias) for worker in workers for alias in names(worker, 'worker')}
    for name in sorted(current_names, key=len, reverse=True):
        words = name.split()
        if not words or not re.fullmatch(r'[а-я]*[бвгджзклмнпрстфхцчшщ]', words[0]):
            continue
        for suffix in ('а', 'ом', 'е'):
            form = words[0] + suffix + (' ' + ' '.join(words[1:]) if len(words) > 1 else '')
            if form in current_names:
                continue
            def recipient_only(found):
                before = text[:found.start()]
                explicit = re.search(r'\b(?:для|поручи(?:ть|те)?|поручаю|назначаю|назначь(?:те)?|попроси(?:ть|те)?|скажи(?:те)?|напомни(?:ть|те)?)\s*$', before)
                explicit = explicit or re.search(r'^(?:поручаю|поручи(?:ть|те)?|назначаю|назначь(?:те)?)\b[^.!?;]*$', before)
                return found.group() if explicit else ''
            text = re.sub(r'(?<!\w)' + re.escape(form) + r'(?!\w)', recipient_only, text)
    match = re.search(r'\b(?:' + receiver_action + r')\b', text)
    if not match:
        return text
    actor = text[:match.end()]
    if re.fullmatch(contact_action, match.group()):
        # Fronted dative is still the contact/help recipient. Retain a
        # separate nominative actor and explicit для task framing. A lexical
        # case veto can remove a candidate; it never establishes identity.
        prefix = text[:match.start()]
        if not re.search(r'\bдля\b', prefix):
            current_names = {fold(alias) for worker in workers for alias in names(worker, 'worker')}
            # Mirror only the existing reference matcher's consonant-name + у
            # form. Exact aliases win; this excludes an object, never picks ID.
            dative_forms = {name + 'у' for name in current_names if re.fullmatch(r'[а-я]*[бвгджзклмнпрстфхцчшщ]', name)} - current_names
            def without_dative(found):
                readings = _analyses(found.group())
                dative_only = readings and all(p.tag.POS == 'NOUN' and p.tag.case == 'datv' for p in readings)
                return '' if dative_only or found.group() in dative_forms else found.group()
            actor = re.sub(r'[а-яё]+', without_dative, prefix) + match.group()
    return actor



@lru_cache(maxsize=1)
def _dictionary():
    from pymorphy3 import MorphAnalyzer
    from pymorphy3.units.by_lookup import DictionaryAnalyzer
    from pymorphy3_dicts_ru import get_path
    return MorphAnalyzer(path=get_path(), lang='ru', units=[DictionaryAnalyzer()],
                         probability_estimator_cls=None, char_substitutes={})


@lru_cache(maxsize=512)
def _analyses(token):
    if not re.fullmatch(r'[а-яё]{2,60}', token):
        return ()
    return tuple(p for p in _dictionary().parse(token) if p.is_known)


def _instruction_predicate(token, *, subjunctive=False):
    readings = _analyses(token)
    # A modal/function-word reading (есть, пускай) cannot close the actor
    # frame just because another dictionary reading is an operative verb.
    return bool(readings) and all(p.normal_form != 'быть' and
               (p.tag.POS == 'INFN' or p.tag.POS == 'VERB' and
                (p.tag.mood == 'impr' or p.tag.tense == 'futr' or subjunctive and p.tag.tense == 'past')) for p in readings)


def _status_predicate(token):
    readings = _analyses(token)
    return bool(readings) and all(p.tag.POS in ('ADJS', 'PRTS') or
                                 p.tag.POS == 'VERB' and p.tag.tense == 'past' for p in readings)


def task_recipient_evidence(raw, *, areas=(), subjunctive=False):
    """Inspect only a leading addressee frame; preserve uncertainty otherwise.

    Leading actor/direct-object syntax admits nominative, accusative or vocative
    readings. A dative-plural common noun cannot be that leading direct object
    (e.g. Рустам vs рустам), but Roman/roman and Hasan/place stay ambiguous.
    Explicit "для" instead requires genitive. Unknown vocatives need a direct
    person-movement imperative, while known common nouns are not vocatives here.
    """
    desired = re.search(r'\b(?:нужно|надо|необходимо)\s*,?\s*чтобы\s+(.*)', fold(raw))
    if desired:
        raw, subjunctive = desired.group(1), True
    normalized = re.sub(r'\s+', ' ', task_actor_scope(raw)).strip()
    normalized = re.sub(r'^(?:(?:сегодня|завтра|послезавтра|утром|вечером)\s+)+', '', normalized)
    first = re.match(r'^(?:(для|напомни|напомнить|назначаю|назначь|назначьте)\s+)?([а-яё]{2,60})(?!\w)(.*)$', normalized)
    if not first:
        return None
    preposition, token, rest = first.groups()
    area_aliases = {fold(alias) for area in areas for alias in names(area, 'area')}
    if not rest.strip() or any(re.match(re.escape(alias) + r'(?!\w)', normalized) for alias in area_aliases):
        return None
    analyses = _analyses(token)
    cases = {'gent'} if preposition == 'для' else {'accs', 'datv'} if preposition and preposition.startswith('назнач') else {'datv'} if preposition else {'nomn', 'accs', 'voct'}
    compatible = [p for p in analyses if p.tag.POS != 'NOUN' or p.tag.case is None or p.tag.case in cases]
    same_clause = re.split(r'[,;.!?]', rest, maxsplit=1)[0]
    predicate = any(_instruction_predicate(word, subjunctive=subjunctive) for word in re.findall(r'[а-яё]+', same_clause))
    vocative = rest.lstrip().startswith(',')
    actor_case = cases if preposition else {'nomn', 'voct'}
    named_actor = any('Name' in p.tag and p.tag.case in actor_case for p in compatible)
    if (predicate or vocative) and named_actor and compatible and all('Name' in p.tag and 'anim' in p.tag for p in compatible):
        return dict(LEXICON_BASIS, category='INFERENCE', evidence='dictionary_known_person_name', token=token)
    # Comma alone or token + date/infinitive gives no person evidence. A direct
    # vocative + person-movement imperative supports audience, never identity.
    modifiers = r'(?:(?:сегодня|завтра|послезавтра|пожалуйста|в\s+\d{1,2}:\d{2})\s+)*'
    if not preposition and not analyses and re.match(r'^\s*,\s*'+modifiers+r'(?:'+PERSON_MOVEMENT_IMPERATIVE+r')(?!\w)(?:\s|$)', rest):
        return dict(category='INFERENCE', evidence='explicit_vocative_person_movement_instruction', token=token,
                    rule='vocative_person_movement_v1', source='SOURCE DISCOURSE')
    return None
