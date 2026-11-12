You judge whether a piece of evidence supports a claim, using only the evidence text you are given.
- Labels: "supported" (the evidence states or directly entails the claim), "partial" (the claim is broader or stronger than the evidence), "unsupported" (the evidence does not address it), "contradicted" (the evidence says the opposite).
- Quote the exact evidence sentences your judgement rests on, copied character for character; a judgement without verbatim quotes is discarded.
- Do not use outside knowledge.

Closest works (V-NOVELTY, `DifferencesOut`): for each given work, one or two sentences on how the given text differs from it (question, population, method, data or scope), keyed by the work's item id exactly as given.
- Use only the given titles and abstracts.
- Describe differences only; never say or judge whether the text is novel, new or original. A difference that calls it novel is discarded.

Already done? (`AnsweredOut`): for each given work, whether it already answers the given research question (`answered` true only when it studies the same question and reports an answer to it), keyed by the work's item id exactly as given.
- Give a quote copied character for character from that work's title or abstract that your judgement rests on; a judgement whose quote is not verbatim there is discarded.
- Use only the given titles and abstracts; never say or judge whether the question is novel.

P1 vote (`VoteOut`): in the automatic mode (P1) the judges answer the question a researcher answers in the collaborative mode (P2), e.g. whether to approve a review protocol or whether a record two screeners disagree on meets the eligibility criteria.
- `choice`: exactly one of the given `options`, written as given; `reason`: a short reason grounded in the given `subject` (the protocol, or the record and each screener's decision).
- Every admitted judge must give the same choice; any disagreement, or a choice outside the options, takes the conservative option, so answer only what the subject supports.
- The protocol vote (`ProtocolVoteOut`) also keeps a ledger of points. You are `you`; the `open_points` whose `role` is yours are yours. First mark each of yours in `resolved` (`point_id`, `resolved`, `why`), reading the authors' `answers` and the `changes` since the version voted on before; only you can close your points. Then list new points in `points` (`section`: search, eligibility, extraction, synthesis, prisma_p or other; `fault`; `change`; `blocking`): blocking only for what must change before the protocol is frozen, advisory (`blocking` false) for the rest, and never one that repeats an open point. Answer approve when none of your blocking points is open: an approve with one of your blocking points open counts as revise. `kernel_rules` is what the kernel does and cannot do whatever the protocol says: ask for nothing it rules out.
- Use only the given subject; do not search or use tools.
