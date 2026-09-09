"""Public configuration boundaries; these checks do not simulate research success."""
from copy import deepcopy
import hashlib
from typing import Any

from pydantic import ValidationError
import pytest
import yaml

from app.agents.idea.runtime_profile import (
    PROFILE_FILE, _LeadResearch, public_agent_configuration, resolve_idea_profile,
)
from app.settings import Settings, repo_root

V5 = 'experimental_research_pro_per_insight_v5'
V6 = 'experimental_research_pro_per_insight_v6'


def test_v6_adds_one_publisher_and_explicit_single_paper_units_only() -> None:
    data = (repo_root()/PROFILE_FILE).read_bytes()
    # Entire catalog at ef709a6, before appending v6.
    assert hashlib.sha256(data[:10992]).hexdigest() == '879798eaf46b5e1d2a67c49fedd9f2d19ee0c0c958c3f9e4f65e43b8e19bb249'
    profiles = yaml.safe_load(data)['profiles']
    expected = deepcopy(profiles[V5])
    expected['lead']['research']['per_delegation_min_sources'] = 1
    expected['child']['tools'].append('search.neurips_search')
    assert profiles[V6] == expected
    old, new = resolve_idea_profile(V5), resolve_idea_profile(V6)
    assert old is not None and new is not None
    assert old.snapshot()['configuration_sha256'] == '2140c3bdd73a6e9626fbb34a3be00e8d85a53c84393e6146d48eb37331403b9c'
    assert new.lead.tools == old.lead.tools
    assert new.child.tools == (*old.child.tools, 'search.neurips_search')
    assert 'per_delegation_min_sources' not in old.lead.raw['research']
    assert new.lead.raw['research']['per_delegation_min_sources'] == 1
    before, after = public_agent_configuration(old.lead), public_agent_configuration(new.lead)
    before['research']['per_delegation_min_sources'] = 1
    assert before == after
    assert new.snapshot()['status'] == 'experimental' and new.snapshot()['validated'] is False
    assert Settings.model_fields['mars_idea_runtime_profile'].default == 'baseline'
    assert Settings(_env_file=None, mars_idea_runtime_profile=V6).mars_idea_runtime_profile == V6  # type: ignore[call-arg,arg-type]


@pytest.mark.parametrize('value', [True, False, 0, 2, '1', 1.0])
def test_single_paper_policy_is_strict_integer_one(value: Any) -> None:
    with pytest.raises(ValidationError):
        _LeadResearch.model_validate({'max_delegations': 3, 'per_delegation_min_sources': value})


def test_omitted_policy_does_not_change_legacy_config_and_explicit_policy_is_bound() -> None:
    assert _LeadResearch.model_validate({'max_delegations': 3}).model_dump(exclude_none=True) == {'max_delegations': 3}
    assert _LeadResearch.model_validate({'max_delegations': 3, 'per_delegation_min_sources': 1}).model_dump(exclude_none=True) == {'max_delegations': 3, 'per_delegation_min_sources': 1}


def test_new_scenario_keeps_two_global_read_papers_and_the_original_public_question() -> None:
    root = repo_root()/'configs/evaluation'
    old = yaml.safe_load((root/'idea_research_publisher_real.yaml').read_text())
    new = yaml.safe_load((root/'idea_research_single_publication_real.yaml').read_text())
    assert new['question'] == old['question']
    assert new['requirements'] == old['requirements']
    assert new['requirements']['min_sources'] == new['requirements']['min_pdfs'] == 2
    assert new['research'] == {**old['research'], 'per_delegation_min_sources': 1}
    assert new['domains'] == [*old['domains'], 'papers.nips.cc', 'proceedings.neurips.cc']
    assert set(new['child_tools']) == {*old['child_tools'], 'search.neurips_search'}
    for key in ('project', 'scope', 'data_scope', 'model', 'loop', 'tools', 'child_model', 'child_loop', 'source_max_mib'):
        assert new[key] == old[key]
    assert not any(key in new for key in ('seed_artifact', 'context_refs', 'data_source'))
