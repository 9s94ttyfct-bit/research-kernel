"""Calibration (synthesis across studies, 2026-11-13; docs/eval/synthesis-2026-11-13.md): in srrepro9 every project
that reached argue narrowed its main claim to one or a few studies. The requests a role or judge receives, the
artifacts the kernel computes from the extraction rows and the claims it writes from them."""

from __future__ import annotations

import pytest

from rk.production.synthesis import RANDOM_EFFECTS_RULE, SynthesizeWorker
from tests.production_fakes import make_frozen_project
from tests.test_protocol import TOPIC_WORDS
from tests.test_synthesis import (
    BCG,
    BINARY,
    META,
    _ctx,
    _enable_stats,
    _exec,
    _group,
    _setup,
    _votes_pool,
)


@pytest.fixture
def env(tmp_home, replay_http, request):
    k, ps, lib, _ = make_frozen_project(tmp_home, replay_http, **getattr(request, "param", {}))
    yield k, ps, lib
    k.close()


def _topic_free(text: str) -> None:
    """Its examples come from none of the six evaluation topics."""
    assert not [w for w in TOPIC_WORDS if w in text.lower()], text


# ---- Task 1: pooling: clinical diversity is the random-effects model's job ----
@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
@pytest.mark.parametrize("model", ["DL", "REML", "FE"])
def test_the_random_effects_rule_reaches_the_planner_and_the_pool_vote(env, model):
    _enable_stats()
    _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META.model_copy(update={"pool_model": model}))
    ex = _exec(plans=[lambda inputs: _group(s["item_id"] for s in inputs["studies"])], votes=_votes_pool())
    assert SynthesizeWorker().run(_ctx(env, ex)).status == "done"
    (planned,) = ex.of("arguer", "PoolingPlan")
    (voted,) = [c for c in ex.calls if c["schema"] == "VoteOut" and c["role"] == "judge"]
    random_effects = model != "FE"
    assert (RANDOM_EFFECTS_RULE in planned["instructions"]) is random_effects
    assert (RANDOM_EFFECTS_RULE in voted["inputs"]["question"]) is random_effects
    _topic_free(RANDOM_EFFECTS_RULE)


@pytest.mark.parametrize("env", [{"paper_type": "systematic_review"}], indirect=True)
def test_the_note_on_studies_the_kernel_added_does_not_read_as_unassessed(env):
    """Both srrepro9 no_pool votes on the 45 computable studies rested on the kernel's note that the studies it added
    "were not assessed for comparability by the planner"."""
    _enable_stats()
    ids = _setup(env, dict(list(BCG.items())[:3]), schema=BINARY, plan=META)
    ex = _exec(plans=[_group([ids["Aronson"]])], votes=_votes_pool())
    assert SynthesizeWorker().run(_ctx(env, ex)).status == "done"
    subject = next(c for c in ex.calls if c["schema"] == "VoteOut")["inputs"]["subject"]
    assert subject["added_by_kernel"] == ["Ferguson 1951", "Rosenthal 1952"]
    assert "not assessed" not in subject["note"]
    assert "same outcome and comparison" in subject["note"]
    _topic_free(subject["note"])
