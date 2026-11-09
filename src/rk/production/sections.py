"""The required sections of a manuscript per paper type (spec §7.3), with their ids as the draft file stems.

``SECTIONS`` lists each paper type's sections in order. A critical review has one ``theme-<n>`` section per theme of
its synthesis besides the listed ones: ``required_sections`` puts them after ``introduction``, numbered from 1 in the
synthesis's order (a theme named twice, case and whitespace aside, is one section). ``disclosure`` is written by the
kernel (``kernel_written``), never by the Writer."""

from __future__ import annotations

from pydantic import BaseModel

from rk.production.common import fold


class SectionSpec(BaseModel):
    id: str
    title: str
    purpose: str
    kernel_written: bool = False


_DISCLOSURE = SectionSpec(id="disclosure", title="Disclosure", kernel_written=True,
                          purpose="What the kernel ran to produce this manuscript, each step with its record.")


def _review(results_title: str, results_purpose: str) -> list[SectionSpec]:
    return [
        SectionSpec(id="abstract", title="Abstract",
                    purpose="The question, the methods, the main results and the conclusion, briefly."),
        SectionSpec(id="introduction", title="Introduction",
                    purpose="Why the question matters to the audience and what the review sets out to answer."),
        SectionSpec(id="methods", title="Methods",
                    purpose="The frozen protocol: eligibility, sources and search, screening, extraction and synthesis, "
                            "with the search and screening counts."),
        SectionSpec(id="results", title=results_title, purpose=results_purpose),
        SectionSpec(id="discussion", title="Discussion",
                    purpose="What the findings and the argument mean for the question, with the contested points and "
                            "the answers to the objections."),
        SectionSpec(id="limitations", title="Limitations",
                    purpose="The limitations input: fallbacks, withdrawn and narrowed claims, studies left out, "
                            "conceded objections and anything else that limits the conclusions."),
        SectionSpec(id="conclusion", title="Conclusion",
                    purpose="The main claim, stated no more strongly than its support."),
        _DISCLOSURE,
    ]


SECTIONS: dict[str, list[SectionSpec]] = {
    "systematic_review": _review(
        "Results", "The studies included (the PRISMA flow), their characteristics and the synthesis findings, with the "
                   "pooled statistics and figures where there are any."),
    "scoping_review": _review(
        "Evidence map and thematic synthesis",
        "The studies included (the PRISMA flow), the evidence map and the themes of the synthesis."),
    "critical_review": [
        SectionSpec(id="introduction", title="Introduction",
                    purpose="Why the question matters to the audience, how the literature was selected and the "
                            "argument the review makes."),
        SectionSpec(id="gaps-agenda", title="Gaps and research agenda",
                    purpose="What the reviewed literature leaves open and what research would close it."),
        SectionSpec(id="conclusion", title="Conclusion",
                    purpose="The main claim, stated no more strongly than its support, and its limitations."),
        _DISCLOSURE,
    ],
    "research_proposal": [
        SectionSpec(id="background", title="Background",
                    purpose="What is known, from the synthesis findings and the argument."),
        SectionSpec(id="gap", title="Gap", purpose="What the literature leaves open and why it matters."),
        SectionSpec(id="questions-hypotheses", title="Research questions and hypotheses",
                    purpose="The research question and the hypotheses the design tests, each with what would count "
                            "against it."),
        SectionSpec(id="methods", title="Methods",
                    purpose="The frozen design: its items, samples, controls, measurements and analyses, and its "
                            "feasibility."),
        SectionSpec(id="expected-outcomes", title="Expected outcomes",
                    purpose="The results the design expects and the alternative explanations of them."),
        SectionSpec(id="risks", title="Risks",
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
