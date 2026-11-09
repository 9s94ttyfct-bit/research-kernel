"""The required sections of a manuscript per paper type (spec §7.3), with their ids as the draft file stems.

``SECTIONS`` lists each paper type's sections in order. A critical review has one ``theme-<n>`` section per theme of
its synthesis besides the listed ones: ``required_sections`` puts them after ``introduction``, numbered from 1 in the
synthesis's order (a theme named twice, case and whitespace aside, is one section); one without any theme fails the
draft gate. Titles and purposes follow spec §7.3. ``disclosure`` is written by the kernel (``kernel_written``), never
by the Writer."""

from __future__ import annotations

from pydantic import BaseModel

from rk.production.common import fold


class SectionSpec(BaseModel):
    id: str
    title: str
    purpose: str
    kernel_written: bool = False
    # the section describes the research operations (what was searched, screened, planned): a sentence whose only
    # source markers are ``run``/``plan`` may stand here (``rk.verify.cover``), nowhere else
    operations: bool = False


_DISCLOSURE = SectionSpec(id="disclosure", title="Disclosure", kernel_written=True, operations=True,
                          purpose="What the kernel ran to produce this manuscript, each step with its record.")
KERNEL_WRITTEN = frozenset({_DISCLOSURE.id})  # the sections only the kernel writes (verified with their own rules)


def _review(results_title: str, results_purpose: str) -> list[SectionSpec]:
    return [
        SectionSpec(id="abstract", title="Abstract", operations=True,
                    purpose="The question, the methods, the main results and the conclusion, briefly."),
        SectionSpec(id="introduction", title="Introduction",
                    purpose="Why the question matters to the audience and what the review sets out to answer."),
        SectionSpec(id="methods", title="Methods", operations=True,
                    purpose="The frozen protocol: sources and search, eligibility, screening, extraction, risk of bias "
                            "(how each study's evidence was assessed) and the synthesis method, with the search and "
                            "screening counts."),
        SectionSpec(id="results", title=results_title, purpose=results_purpose, operations=True),
        SectionSpec(id="discussion", title="Discussion",
                    purpose="What the findings and the argument mean for the question, with the contested points and "
                            "the answers to the objections."),
        SectionSpec(id="limitations", title="Limitations", operations=True,
                    purpose="The limitations input: fallbacks, withdrawn and narrowed claims, studies left out, "
                            "conceded objections and anything else that limits the conclusions."),
        SectionSpec(id="conclusion", title="Conclusion",
                    purpose="The main claim, stated no more strongly than its support."),
        _DISCLOSURE,
    ]


SECTIONS: dict[str, list[SectionSpec]] = {
    "systematic_review": _review(
        "Results", "The PRISMA flow of the studies (the flow figure and its counts), the characteristics of the "
                   "included studies and the synthesis results, with the pooled statistics and figures where there are "
                   "any."),
    "scoping_review": _review(
        "Evidence map and thematic synthesis",
        "The PRISMA flow of the studies (the flow figure and its counts), the evidence map and the themes of the "
        "synthesis."),
    "critical_review": [
        SectionSpec(id="introduction", title="Introduction", operations=True,
                    purpose="Context, then the problem, then the review's response: why the question matters to the "
                            "audience, how the literature was selected and the argument the review makes."),
        SectionSpec(id="gaps-agenda", title="Gaps & Agenda",
                    purpose="What the reviewed literature leaves open and the research agenda that would close it."),
        SectionSpec(id="conclusion", title="Conclusion",
                    purpose="The main claim, stated no more strongly than its support, and its limitations."),
        _DISCLOSURE,
    ],
    "research_proposal": [
        SectionSpec(id="background", title="Background & Significance",
                    purpose="What is known, from the synthesis findings and the argument, and why the question "
                            "matters to the audience."),
        SectionSpec(id="gap", title="Gap", purpose="What the literature leaves open and why it matters."),
        SectionSpec(id="questions-hypotheses", title="Research Question & Hypotheses", operations=True,
                    purpose="The research question and the hypotheses the design tests, each with its falsification "
                            "condition: the result that would count against it."),
        SectionSpec(id="methods", title="Proposed Methods", operations=True,
                    purpose="The planned work, from the frozen design (cited with [[plan:]] markers): its items, "
                            "samples, controls, measurements and analyses, and its feasibility. No results exist."),
        SectionSpec(id="expected-outcomes", title="Expected Outcomes & Alternatives", operations=True,
                    purpose="The results the design expects and the alternative explanations of them."),
        SectionSpec(id="risks", title="Risks", operations=True,
                    purpose="What could keep the design from answering the question: the limitations input, dropped "
                            "hypotheses and withdrawn or narrowed feasibility claims."),
        _DISCLOSURE,
    ],
}
THEMES_AFTER = "introduction"  # a critical review's theme sections come right after it


def theme_sections(themes: list[str]) -> list[SectionSpec]:
    """One ``theme-<n>`` section per distinct theme (case and whitespace aside), in order."""
    seen: dict[str, str] = {}
    for t in themes:
        if t.strip() and fold(t) not in seen:
            seen[fold(t)] = " ".join(t.split())
    return [SectionSpec(id=f"theme-{i}", title=t,
                        purpose=f"The literature on the theme {t!r}: its findings, how they agree or conflict, and "
                                f"their strength.")
            for i, t in enumerate(seen.values(), start=1)]


def required_sections(paper_type: str, themes: list[str] = ()) -> list[SectionSpec]:
    """The paper type's sections in order; a critical review's ``themes`` become sections after the introduction."""
    if paper_type not in SECTIONS:
        raise ValueError(f"unknown paper type {paper_type!r}")
    out = list(SECTIONS[paper_type])
    if paper_type == "critical_review":
        at = next(i for i, s in enumerate(out) if s.id == THEMES_AFTER) + 1
        out[at:at] = theme_sections(list(themes))
    return out


def describes_operations(paper_type: str, section: str) -> bool:
    """Whether the paper type's section ``section`` describes the research operations (``SectionSpec.operations``);
    a theme section or an unknown id does not."""
    return section in KERNEL_WRITTEN or any(
        s.id == section and s.operations for s in SECTIONS.get(paper_type, []))
