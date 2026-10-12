"""Pinned, bounded lexical evidence is separate from business identity."""
from importlib.metadata import version
from pathlib import Path
import tomllib

import pytest

from greenhouse_owner_os.services.task_audience_language import (
    LEXICON_BASIS, _analyses, _dictionary, task_recipient_evidence,
)


@pytest.mark.parametrize('token', ['Рустам', 'рустам', 'Рахим', 'Закир', 'Умар'])
def test_known_unambiguous_name_has_auditable_linguistic_basis(token):
    basis = task_recipient_evidence(f'{token} завтра проверить насос')
    assert basis['evidence'] == 'dictionary_known_person_name'
    assert basis['category'] == 'INFERENCE'
    assert basis['source_revision'] == '417150'
    assert not any(key in basis for key in ('worker_id', 'greenhouse_id', 'language'))


def test_time_prefix_does_not_replace_lexical_evidence():
    assert task_recipient_evidence('Завтра Рустам Анталия починит пленку')['token'] == 'рустам'
    assert task_recipient_evidence('Завтра Дверь закроет окна') is None


def test_rustam_common_noun_reading_is_incompatible_with_leading_actor_frame():
    # Actual pinned dictionary also has inanimate руст, dative plural. That
    # reading cannot be a leading actor/direct object; it is not discarded by
    # probability or capitalization.
    parses = _analyses('рустам')
    assert any('Name' in p.tag and p.tag.case == 'nomn' for p in parses)
    assert any('inan' in p.tag and p.tag.case == 'datv' for p in parses)
    assert task_recipient_evidence('Рустам завтра проверить насос')


@pytest.mark.parametrize('token', ['Роман', 'Вера', 'Любовь', 'Надежда', 'Хасан'])
def test_common_noun_or_geographic_reading_prevents_lexical_choice(token):
    assert any('Name' in p.tag for p in _analyses(token.casefold()))
    assert task_recipient_evidence(f'{token} завтра проверить насос') is None


@pytest.mark.parametrize('raw', [
    'Фитингг, проверь завтра', 'Фитинги, проверь завтра', 'Дверь, проверь насос',
    'Зорпак завтра проверить насос', 'Зорпак, проверить насос', 'Зорпак, полей завтра',
])
def test_unknown_word_or_generic_object_vocative_is_not_guessed_as_name(raw):
    assert task_recipient_evidence(raw) is None


@pytest.mark.parametrize('raw', ['Шербек, в 4:30 заедь на Озвади и закрой, пожалуйста, вентиляцию.', 'Шербек, приходи завтра'])
def test_oov_movement_vocative_is_source_discourse_not_lexical_name(raw):
    basis = task_recipient_evidence(raw)
    assert basis['evidence'] == 'explicit_vocative_person_movement_instruction'
    assert basis['category'] == 'INFERENCE' and 'analyzer' not in basis


def test_explicit_area_reference_vetoes_lexical_name_even_for_multiword_alias():
    area = dict(name='Рустам теплица', aliases=['Рахим'], active=True)
    assert task_recipient_evidence('Рустам теплица завтра проверить насос', areas=[area]) is None
    assert task_recipient_evidence('Рахим завтра проверить насос', areas=[area]) is None


def test_lookup_cannot_predict_unknown_names_and_is_bounded():
    analyzer = _dictionary()
    assert len(analyzer._units) == 1
    assert type(analyzer._units[0].analyzer).__name__ == 'DictionaryAnalyzer'
    assert analyzer.prob_estimator is None
    assert _analyses('зорпак') == () and _analyses('я' * 61) == ()
    assert _analyses('Rustam') == ()
    assert all(p.is_known for p in _analyses('рустам'))
    assert _analyses.cache_info().maxsize == 512


def test_runtime_and_project_dependencies_are_exactly_pinned():
    pins = {'pymorphy3': '2.0.6', 'pymorphy3-dicts-ru': '2.4.417150.4580142',
            'dawg2-python': '0.9.0', 'setuptools': '84.0.0'}
    project = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())
    for package, pinned in pins.items():
        assert version(package) == pinned
        assert f'{package}=={pinned}' in project['project']['dependencies']
    assert LEXICON_BASIS['dictionary_version'] == pins['pymorphy3-dicts-ru']


def test_environment_dictionary_override_cannot_change_pinned_lexicon(monkeypatch, tmp_path):
    from pymorphy3 import MorphAnalyzer
    from pymorphy3_dicts_ru import get_path
    monkeypatch.setenv(MorphAnalyzer.DICT_PATH_ENV_VARIABLE, str(tmp_path / 'foreign-dictionary'))
    _dictionary.cache_clear()
    try:
        analyzer = _dictionary()
        assert Path(analyzer.dictionary.path).resolve() == Path(get_path()).resolve()
        assert any('Name' in p.tag for p in analyzer.parse('умар'))
    finally:
        _dictionary.cache_clear()
