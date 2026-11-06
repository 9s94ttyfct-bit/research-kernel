# *The Craft of Research*, 5th ed.: The Book as a Process Model for an Autonomous Research Agent

Source: `/tmp/craft.txt` (pdftotext -layout of Booth, Colomb, Williams, Bizup, FitzGerald, *The Craft of Research*, 5th ed.). I read the whole main text, L317–10606: Preface, Introduction, Parts I–V with their prologues, chapters 1–18, and every Quick Tip. I only skimmed the Appendix (L10607+).

Conventions:
- `(L1234)` is a line number in `/tmp/craft.txt`. Text in quotation marks is the book's wording, sometimes lightly trimmed. Text without quotation marks is a close paraphrase.
- **[agent-mapping inference]** marks my own design suggestions for an autonomous AI agent. The book says none of this.
- "QT" means a chapter-end Quick Tip.

---

## 1. Overall process model

### 1.1 The book's own framing of the lifecycle

The book defines research as "a particular kind of directed inquiry: one that begins with a question on the part of the researcher and ends with sharing the answer with an interested audience" (L606–609). The 5th edition separates three processes:
- research (Parts I–II)
- argumentation (Part III)
- communication (Part IV)

It does this "so that parts I and II focus more deliberately on research, part III on argumentation, and part IV on communicating research arguments" (L446–450). It also says research "need not culminate in an academic paper" (L441–445).

The edition takes a "discourse-based" model of knowledge. It treats research "not as the discovery and 'reporting' of 'truths' but as a collective pursuit of better understanding through argumentation" (L451–478). It also states that "truth is always partial—both incomplete and partisan" (L469).

### 1.2 Ordered stages (the canonical, idealized order)

| # | Stage | Chapter(s) | Core output |
|---|---|---|---|
| S0 | Audience and role framing | Intro, I.3–I.4, QT checklist | Audience profile, writer role, audience role |
| S1 | Interest → focused topic | 1.1 | Focused topic phrased with action nouns |
| S2 | Topic → research questions | 1.2 | Question inventory, then evaluated and combined questions |
| S3 | So what? (3-step formula) | 1.3 | "I am studying X because I want to find out Y in order to help my audience understand Z" |
| S4 | Question → research problem | ch. 2 | Problem statement: condition + cost/consequence; pure vs. applied |
| S5 | Working hypothesis / provisional claim | Part I prologue, 5.5, 6 intro, QT ch. 5 | Working claim plus alternatives, all revisable |
| S6 | Finding and evaluating sources | Part II prologue, ch. 3 | Source set, each tagged primary/secondary/tertiary and screened for relevance and reliability |
| S7 | Engaging sources and taking notes | ch. 4 | Complete bibliographic records, notes, annotations, keyword tags, annotated bibliography |
| S8 | Assembling the argument (overview) | Part III prologue, ch. 5 | Storyboard: claim, reasons, evidence, warrants, acknowledgments/responses |
| S9 | Making claims | ch. 6 | Main claim: right kind, specific, significant, contestable, qualified |
| S10 | Reasons and evidence | ch. 7 | Ordered reasons; evidence screened on 5 criteria (+ relevance) |
| S11 | Warrants | ch. 8 | Warrants stated as "When X, then Y" and passed through 5 tests |
| S12 | Acknowledgment and response | ch. 9 | Objection inventory and response sub-arguments |
| S13 | Planning and drafting | Part IV prologue, ch. 10 | Working intro, key terms, section plan, draft |
| S14 | Revising and organizing | ch. 11 | Top-down revision: frame → argument → organization → paragraphs; abstract |
| S15 | Incorporating sources and citation | ch. 12 | Fair summaries, paraphrases and quotations; citations; plagiarism guards |
| S16 | Visual evidence | ch. 13 | Honest, framed tables and figures |
| S17 | Introduction and conclusion | ch. 14 | Context → problem → response; conclusion with point, new significance, more research; title |
| S18 | Style | ch. 15 | Sentence-level revision: characters as subjects, actions as verbs, old before new, complexity last |
| S19 | Presentation | ch. 16 | Preliminary and final talk, notes, Q&A preparation, handouts |
| S20 | Ethics (applies to every stage) | ch. 17 (and 18) | Integrity, community obligations, social responsibility |

### 1.3 Non-linearity: the book's explicit statements and loop-backs

The book repeatedly says that this order is a teaching simplification.

- **Preface:** "Experienced researchers loop back and forth, move forward a step or two before going back in order to move ahead again, change directions, all the while anticipating stages not yet begun" (L350–352).
- **Introduction:** "research rarely moves in a straight line. It zigzags and sometimes doubles back on itself. We lay out the research process in a more linear fashion than is true to life" (L585–588).
- **Part I prologue:**
  - "you cannot march through these steps in the neat order we present them. You will think of a tentative answer to your research question before you have all the evidence you need to support it. And when you think you have an argument worth making, you may discover you need more and maybe different evidence from new sources. You may even modify your topic" (L1069–1074).
  - The book compares this to "zigzagging up and down a rocky hill through overgrown woods, sometimes in a fog" and advises: "plan for predictable detours" (L1074–1079).
- **Part II prologue:** "you don't first find all your sources, then read and take notes on them … This cyclic approach to locating, evaluating, and engaging sources is characteristic of all authentic research" (L2232–2238). Searching too is "not strictly linear. A single source can lead to others and return you to catalogs and databases you have already visited" (L2496–2499).
- **Part III prologue:**
  - Plan the argument early: "as soon as you have a sense of your problem and its likely solution, you should begin planning your argument. Your plan will change as your research progresses—if it doesn't, you probably aren't doing your best thinking" (L3808–3812).
  - "Only when you try to make a research argument that answers your audience's predictable questions can you see what research you have yet to do" (L3813–3815). Argument assembly therefore loops back to source finding (S8→S6).
- **Storyboard (5.5):** The storyboard "can and should change." When a page lacks evidence, the researchers "may have to abandon that reason and even change the claim" (L4184–4194). This is a loop from S10 to S9/S5.
- **Significance:** Many researchers "can't flesh out the last step in that three-part sentence until they finish a first draft. So it's not a mistake to begin … without a good answer … but you face a problem when you finish your research without having thought through each of those three steps" (L1580–1586). This loop runs S13 → S3.
- **Problem from your own conclusion (2.4.3):** "If in an early draft you arrive at an unanticipated claim, ask yourself what question it might answer" (L2144–2149). Drafting can create the problem (S13 → S4).
- **Unfixable weakness (9.4.2):** "try to redefine your problem or rebuild your argument to avoid it" (L5906–5907). This loops S12 → S4/S8.
- **Drafting as discovery:**
  - Part IV prologue: writers "know they'll make some wrong turns but also some new discoveries and maybe even rethink their whole project" (L6229–6234).
  - Drafting "can also be an act of discovery" (L6766–6768).
  - "They write a first draft not to show to readers, but to discover what case they can actually make" (L6925–6927).
- **Introductions are written twice:** once as a working sketch and once as a final version (L6407–6410). Chapter 14 is done "once you think you have a draft that works" (L8400).
- **Preliminary presentations** test ideas mid-project (L9926–9946).
- **Advice for Teachers:** "Research is messy, so it does no good to march students through it lockstep: (1) select topic, (2) state thesis, (3) write outline, (4) collect bibliography, (5) read and take notes, (6) write paper. That caricatures authentic research … They need time for false starts and dead ends" (L10524–10529).
- **Write throughout:** "Resolve to do lots of writing along the way" (L1091). There are three reasons to "write as we research": to remember, to understand, and to test your thinking (L6253–6315). "Writing is thinking" (L6316–6317).

**[agent-mapping inference]** Model the process as a DAG of stage artifacts with permitted back-edges, not as a pipeline:

| Back-edge | Trigger |
|---|---|
| S8/S10 → S6 | Evidence gap on a storyboard page |
| S10 → S9/S5 | A reason cannot be evidenced, so abandon the reason or revise the claim |
| S12 → S4 | Unfixable weakness |
| S13 → S3/S4 | Unanticipated claim in a draft |
| S14 → S10 | Evidence fails the re-check |

Every back-edge should log its trigger. The stage controller should also limit "one more source" loops; the book warns against endless note-taking as procrastination (L5079–5081) and against "read one more article" (L6788–6791).

---

## 2. Per-stage analysis

### S0. Audience and role framing (Introduction, I.1–I.5; QT "A Checklist for Understanding Your Audience")

**(a) Goal.** Decide whom you are writing for, which role you play for them, and which role you cast them in. Do this before writing a word, and revisit it later.

**(b) Inputs → outputs.** Inputs: the assignment or context and the likely audiences. Output: an audience profile plus a writer/audience role contract.

**(c) Heuristics.**
- Writing is "a kind of imagined conversation conducted through text, where any misunderstanding cannot be repaired in real time" (L669–672).
- Three writer roles:
  1. "I Have Found Some New and Interesting Information" (L735)
  2. "I Have Found a Solution to a Practical Problem" (L751)
  3. "I Have Found an Answer to an Important Question" (L771)
- Complementary audience roles:
  1. "Entertain Me" (L813)
  2. "Help Me Solve My Practical Problem": only "those particular facts that are relevant to the problem … and whose sources you can show are reliable" (L849–852)
  3. "Help Me Understand Something Better": they "expect you to verify their authenticity and to substantiate any claims" (L884–886)
- Academic audiences "will almost always adopt this third role" (L910).
- Reverse the bad-classroom relationship: "you must think of your audience as made up of people who don't know what you know but need to" (L726–728).
- Audience checklist (L986–1022):
  1. Who is the audience: professionals, a well-informed general audience, or novices?
  2. What do they expect: entertainment, new facts, understanding, or a practical solution?
  3. How much do they know? Do they already recognize the problem, have it without recognizing it, or is it "not theirs but only mine"?
  4. How will they respond to the claim: contradiction, standard counterarguments, wanting to see the steps?
- Answer these "early on, then revisit them when you start planning and again when you revise" (L988–989).

**(d) Checks.** Run the checklist at three points: start, planning, and revision (L988–989).

**(e) Failure modes.**
- Assuming the audience is a "Waffle Lovers Society," already fascinated (L832–837).
- Misjudging the register, which produces indifference or talking down (L695–700).
- The devastating response "I don't care" (L889–890).
- Posing a practical problem when the context expects a conceptual one: "pose and address a practical problem only if your teacher creates one" (L857–860).

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `audience.yaml` with fields `audience_type`, `expected_role ∈ {entertain, inform, solve_practical, understand}`, `prior_knowledge`, `problem_recognized ∈ {yes, latent, mine_only}`, `anticipated_resistance[]`, `required_conventions`.
- Gate: every field is non-empty, and the role pairing is consistent with the deliverable type. Re-validate at S13 and S14.

### S1. Interest → focused topic (1.1; QT "Finding Topics")

**(a) Goal.** Move from a broad subject to a specific topic that can grow into a question.

**(b) Inputs → outputs.** Inputs: interests and reference skimming. Output: a focused topic phrase.

**(c) Heuristics.**
- A subject is "a broad area of knowledge" and a topic is "a specific interest within that area" (L1165–1173).
- "The best topic will raise a question about a subject whose answer solves a problem that you and your audience care about" (L1171–1173).
- List "as many interests as you can (ten or more)" (L1202).
- Wikipedia is fine for exploring but not "as a reliable source of evidence … without vetting reference materials on which Wikipedia articles rely" (L1213–1215).
- In a field, skim recent journals, calls for papers, librarians and special collections (L1227–1234).
- **Focusing test:** a topic stated "in a few words" is too diffuse. Add action nouns derived from verbs (conflict, description, contribution, developing, evolution); "Lacking 'action' words, your topic is static" (L1238–1256).
- **Topic → claim test:** restate the topic as a sentence. Static topics become empty claims ("Commercial aviation has a history"); focused ones become claims "an audience might find interesting" (L1257–1290).
- QT sources for topics:
  - things that surprise you
  - claims you disagree with, in podcasts and videos
  - "Ask a generative AI tool to suggest questions. Then evaluate those questions" (L1639–1641)
  - common beliefs you suspect are "simplistic or wrong" (L1645–1647)
  - in a field: social media archives "for matters of controversy," your teacher's most contested issues, next-level textbook study questions (L1652–1668)

**(d) Checks.** Can the topic be stated only in more than a few words? Does it convert into a non-trivial claim sentence?

**(e) Failure modes.** A static topic. Equating "narrow enough to find enough information" with a good topic (L1169–1171).

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `topic_candidates[]` with ≥10 entries, plus the chosen `focused_topic` and its `claim_restatement`.
- Gate:
  - the focused topic contains at least one nominalized action/relational term
  - its claim restatement is not tautological or existential
  - an LLM judge rates the restatement "an audience might contest or find interesting"

### S2. Focused topic → research questions (1.2)

**(a) Goal.** Generate and select questions that will direct the search and give the research a purpose.

**(b) Inputs → outputs.** Input: the focused topic. Outputs: a question inventory, then a ranked and combined question.

**(c) Heuristics.**
- The beginner's mistake: "they seek out all the information they can find on it." Without a question there is "no way to select what's relevant" (L1295–1313).
- "Start with the standard journalistic prompts … but focus on how and why … Record all the questions, but don't stop to answer them" (L1318–1323).
- Question families:
  - history: larger developmental context and internal history (L1333–1343)
  - structure and composition: place in a larger system, how the parts fit (L1346–1373)
  - categories: kinds; compare and contrast (L1376–1384)
  - what-if / speculative (L1387–1391)
  - questions suggested by sources, building on agreement (extend a claim, new evidence for it, analogous questions) or reflecting disagreement (new evidence contradicting it; where two sources disagree) (L1394–1423)
  - experienced researchers: "questions that other researchers ask but don't answer," often at article ends; "lurk" in online discussions (L1424–1431)
- Evaluate questions and avoid three kinds (L1434–1454):
  1. settled fact you could look up
  2. answers unsupportable by any obtainable data
  3. dead ends that don't bear on a larger issue
- Caveat: "it's always possible that a question that seems unproductive might not be" (L1456–1465).
- Combine promising questions into a larger one (L1466–1480).
- "Trust Your Gut" sidebar: persevere on a question you can't yet justify (McClintock) (L1354–1367).

**(d) Checks.** The 3-way avoid test. Prefer how/why over who/what/when/where (L1441–1443).

**(e) Failure modes.** Information-dumping on a topic. Answering questions prematurely. Questions answerable by lookup.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `questions.jsonl`. Each row carries `family`, `text`, `lookupable: bool`, `data_obtainable: bool`, `larger_issue`, and a score.
- Gate:
  - ≥1 selected question is how/why
  - `lookupable == false`, verified by attempting a quick lookup; if a single authoritative fact answers it, reject it
  - a plausible data source is named
  - the combined question is recorded

### S3. So what? The three-step formula (1.3)

**(a) Goal.** Make the question matter to others, not just to you.

**(b) Inputs → outputs.** Input: the selected question. Output: the 3-step (optionally 4-step) project sentence.

**(c) Heuristics.**
- "What will be lost if you don't answer your question?" Start by asking So what? "at first of yourself" (L1488–1490).
- "Nothing. I just want to know" is "Good enough to start but not to finish" (L1498–1500).
- The formula:
  1. "I am trying to learn about/working on/studying ______" (L1511–1512)
  2. "because I want to find out who/what/when/where/whether/why/how ______" (L1526–1529)
  3. "in order to help my audience understand how, why, or whether" (L1556–1557)
- Summary form (L1596–1605):
  1. what you are studying
  2. what you don't know about it
  3. why you want your audience to know and care about it
- Step 3 is "your ticket into the conversation of your research community" (L1586–1587).
- Group version: "I'm working on X because I want to find out Y, so that I (and you) can better understand Z" (L1118–1120).
- Elevator pitch: "your research question, your best guess at an answer, and the kind of evidence you expect to use" (L1121–1124).

**(d) Checks.**
- "Test your progress by talking about your project with others … explain your project also to people who know little about it … Their questions and responses will force you to fill in those three steps" (L1588–1595).
- In QT ch. 4: "Keep rehearsing that formula" (L3787–3788).

**(e) Failure modes.** Finishing research without having filled in step 3 (L1584–1586).

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `project_sentence = {topic, question, significance, application?}`.
- Gate:
  - all three slots filled
  - step 2 is an indirect question
  - step 3 names a different and broader understanding than step 2, checked with a judge prompt: "Does answering Q2 require more than answering Q1?"
- Re-run the gate at the end of S13. A missing step 3 at that point blocks finalization.

### S4. Question → research problem (ch. 2)

**(a) Goal.** Turn the question into a problem that the audience recognizes needs solving.

**(b) Inputs → outputs.** Input: the 3-step sentence. Output: a problem statement (condition + cost/consequence), classified as practical/conceptual and pure/applied.

**(c) Heuristics.**
- "to make your research matter, you must address a problem that others in your community … also want to solve" (L1709–1712). In research, "a problem is something we seek out, even invent" (L1715–1716).
- **Practical problem** = condition + cost. A condition "costs us time, money, respect, security, opportunity, or even our lives" (L1749–1753). State both parts (L1762–1777).
- Frame cost from the audience's point of view. Chain So what? answers until the audience says "What do we do about it?" (L1779–1802).
- **Conceptual problem.** The condition is "always some version of not knowing or not understanding something" (L1838–1839). The consequence is "a lack of understanding that keeps us from understanding something else even more significant" (L1869–1873).
- Q1/Q2 relation: "The answer to the first question (Q1) helps you answer the second (Q2)" and "The answer to the second question (Q2) is more important" (L1895–1898). The So-what ladder (romantic movies → depictions of love → expectations about marriage) is at L1904–1927. If the audience still asks So what?, "Wrong audience" (L1929–1931).
- Bias warning: "the most difficult biases to resist are those embedded in our questions and problems themselves" (L1851–1860).
- **Pure vs. applied:** step 3 refers to knowing (pure) or doing (applied) (L1944–1982).
- Applied plausibility test: "(a) If my audience wants to achieve the goal of ___, (b) would they think that they could do it if they found out ___?" (L2005–2031).
- If the practical link is a stretch, formulate the project as pure research with a 4th step, "Potential Practical Application," and save it for the conclusion (L2032–2055).
- Finding problems:
  - ask others (L2090–2094)
  - take a small piece of a bigger problem (L2095–2097)
  - look in sources for "contradictions, inconsistencies, incomplete explanations"
  - "be sure it's real, not just your own misreading. Countless research papers have refuted a point that no one ever made" (L2108–2116)
  - "If a source says X and you think Y, you may have a research problem, but only if you can show that those who think X misunderstand some larger issue as well" (L2117–2120)
  - read the last pages of sources (L2121–2138)
  - read your own conclusion (L2141–2149)
- The first question about a problem is "not Can I solve it? but Will others think it should be solved?" (L2157–2159).

**(d) Checks.**
- The So-what chain.
- The applied (a)/(b) test.
- The misreading check before claiming a gap.
- The 5 problem questions in ch. 9 (L5741–5756):
  1. Why think there's a problem at all?
  2. Is it properly defined, with manageable scope?
  3. Is it practical or conceptual?
  4. Why is it significant?
  5. Will the audience believe the project can solve it?

**(e) Failure modes.**
- Interest to yourself alone.
- Cobbling a practical consequence onto a conceptual question (L1988–2004).
- Mistaking a topic for a problem (L1822–1828).
- Profit-driven corruption: "Tell us what to look for, and we'll provide it!" (L2063–2070).

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `problem.json = {type: practical|conceptual, purity: pure|applied, condition, consequence_chain[≥2], audience_cost_framing, gap_evidence[] (citations showing the gap is real), bias_review}`.
- Gates:
  - (i) the consequence chain has ≥2 levels, each judged "more important" than the previous
  - (ii) the gap claim is backed by ≥2 sources quoted *in context*, which guards against "refuting a point no one made"
  - (iii) if applied, the (a)/(b) test passes, judged by an independent model
  - (iv) a bias prompt lists perspectives the framing might exclude

### S5. Working hypothesis / provisional claim (Part I prologue; 5.5; ch. 6 opening; QT ch. 5)

**(a) Goal.** Hold a tentative answer that focuses the search without becoming dogma.

**(b) Inputs → outputs.** Input: the problem. Outputs: a working claim and alternatives, including rejected ones.

**(c) Heuristics.**
- "A genuine research question is one that can only be answered by gathering and analyzing evidence in an effort to arrive at an arguable claim" (L1039–1041).
- "You need a provisional answer to your research question—that is, a provisional claim—to focus your search for data" (L4298–4300).
- The storyboard's first page holds "question and working claim—that is, a provisional claim that will guide research but probably change … along with some alternatives, including one the researchers have rejected" (L4185–4188).
- QT "Falling Back on What You Know": don't "embrace a claim too early … because you 'know' you can prove it … begin not with a claim you know you can prove but with a problem you want to explore and solve" (L4259–4266).
- Some researchers "have even won fame for disproving a plausible hypothesis that they had set out to prove" (L2083–2084).
- A failed claim can be turned into a refuted hypothesis (L5922–5931).

**(d) Checks.** Can you state "your best guess at an answer" in the elevator pitch (L1123)? Are alternatives recorded?

**(e) Failure modes.** Premature certainty. Confirmation-seeking search: the wrong view of research is "finding information … especially information they believe backs up their arguments" (L2248–2251).

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `hypotheses.json` with a working claim, ≥2 rival hypotheses, and for each one the evidence that would disconfirm it.
- Gate:
  - the search plan in S6 includes queries for disconfirming evidence for each hypothesis
  - claim revisions are versioned with reasons

### S6. Finding and evaluating sources (Part II prologue; ch. 3)

**(a) Goal.** Build a relevant, reliable, and diverse source network, including sources that challenge you.

**(b) Inputs → outputs.** Inputs: the problem and keywords. Outputs: a source list classified by type and screened for relevance and reliability, plus search logs.

**(c) Heuristics.**

*Source types.* Classification is by use and "relative to a researcher's project" (L2262–2264, L2376–2389).
- **Primary:** "'original' materials that provide you with the 'raw data' or evidence" (L2267–2282).
- **Secondary:** peer-reviewed and university-press work, i.e. "that field's literature" (L2285–2299). Three uses:
  1. learn what others have written and which questions they think important (L2302–2307)
  2. find other points of view: "look not only for those that support your views but also for those that challenge them" (L2308–2322)
  3. find models of argument and form (L2323–2340)
- **Tertiary:** good for overviews, but "If you cite tertiary sources in a scholarly argument, you will mark yourself as either a novice or an outsider." They "oversimplify" and are "susceptible to becoming outdated" (L2343–2362).

*Library strategy.*
- Ask a librarian, describing the project with the 3-step rubric (L2429–2452).
- "resist the temptation to go straight to primary or secondary sources … allow reference works to shape your search" (L2455–2466).
- Use annotated bibliographies and annual reviews (L2467–2471) and subscription databases (L2474–2491).
- Search terms: "Successful researchers know they have to be flexible … trial and error" (L2499–2503).
- Use keyword search plus subject-heading browsing (L2506–2528).
- If there are too many hits, restrict to "the last fifteen years … the last ten" (L2536–2538). Finding nothing may mean too narrow, or a neglected topic (L2545–2553).
- Browse the stacks, physically or virtually, for serendipity (L2556–2585).
- **Bibliographic trails (backward):** "a retrospective exercise … can also perpetuate a particular kind of bias" (L2588–2601).
- **Citation indexing (forward):** "occasionally, sources are cited frequently because they are so bad or … debunked" (L2604–2615).
- "A source's credibility can thus be gauged both by the sources it cites and by the sources that cite it" (L2616–2619).

*Online.*
- "maintain a healthy skepticism: much of what we find through Google, other search engines, or generative AI is reliable, but not everything is … the internet is essentially unmonitored." Search companies and webmasters "have an interest in where you go" (L2632–2643).
- Legitimate internet uses (L2648–2658):
  - getting bearings ("regarding everything we learn at this stage as provisional")
  - finding keywords
  - checking dates and facts (but "check these against more reliable sources")
  - locating authors
  - a ballpark search via Google Scholar
- "don't treat online articles (aside from those in scholarly journals) as secondary sources … You can, however, use the internet freely as a primary source" (L2664–2670).
- Avoid unauthorized copies: they are "often inaccurately reproduced" (L2673–2679).

*Relevance test (3.4.1).*
- Books: index → keyword pages; first and last paragraphs of keyword-heavy chapters; intros; the last chapter; the editor's introduction; the bibliography.
- Articles: abstract, keyword search, intro and conclusion (or the first 6–7 and last 4–5 paragraphs), section heads, bibliography.
- Websites: about, overview, summary, site map, site search (L2690–2726).

*Reliability test (3.4.2), 11 signs (L2746–2844):*
1. Reputable press; be skeptical of sensational self-published books and of lobbying groups with "the trappings of academic institutions."
2. Peer-reviewed?
3. Reputable author? Check funders: "check out who authors thank."
4. Reputable website sponsor? Beware objective-sounding fronts (the "Tobacco Institute").
5. Current, by field norms. Rule of thumb: you "can cite works as old as the older ones" in recent works-cited lists; use the most recent edition of secondary sources; an un-updated site "may have been abandoned."
6. Notes and bibliography? "If not, be suspicious."
7. Website bibliographic data: sponsor, author, date.
8. Judicious? Be wary of "wild claims … abusive language."
9. Editorial care: "if a source includes obvious factual errors, distrust it."
10. Well reviewed?
11. Frequently cited? But marginalized scholars may be under-cited.

"These indicators do not guarantee reliability … don't assume that you can read uncritically" (L2846–2850).

*Beyond predictable sources.* Search outside the field's standard source types; "you will get credit for originality if you turn up a good source that others have missed" (L2854–2869).

*"When Someone Gets There First."* Don't panic. If the source truly solves your exact problem, reformulate. Otherwise "acknowledg[e] the strength of your source's claim and not[e] where and how your claim differs" (L2878–2888).

*People as sources.*
- Plan interviews: "you can't keep going back to people because you didn't prepare well enough" (L2891–2905).
- Ask experts about "important open questions," your provisional thesis, and readings (L2906–2915).
- Get ethics review for human subjects (L2924–2936).

**(d) Checks.** The relevance skim protocol and the 11-point reliability screen. Classify each source relative to the project.

**(e) Failure modes.**
- Gathering only confirming sources.
- Citing tertiary sources.
- Treating the internet as comprehensive and reliable.
- Citation-count worship.
- Bias from backward trails.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `sources.jsonl`. Each row holds `type (relative to project)`, `relevance_evidence` (which skim steps were hit), an 11-field `reliability` vector with evidence URLs, `funding_check`, `currency_rule_check`, `stance (supports/challenges/neutral)`, and `discovered_via (keyword | subject | backward | forward | browse | person | AI-suggested)`.
- Gates:
  - ≥1 challenging source per main reason
  - no tertiary source in the evidence role
  - every AI-suggested source is resolved to an existing record (DOI or catalog) before acceptance, since the book warns AI can "make up references" (L2978)
  - both forward and backward citation expansion attempted
  - the currency check uses works-cited dates from recent field papers

### S7. Engaging sources and note-taking (ch. 4)

**(a) Goal.** Record accurately, understand deeply, and read for problems, arguments and data without distorting the source.

**(b) Inputs → outputs.** Input: accepted sources. Outputs: complete bibliographic records, structured notes, annotations, keyword tags, and an annotated bibliography.

**(c) Heuristics.**

*Bibliographic record first.* "record all of its bibliographic information. Do this before you do anything else" (L3023–3026). There are field lists for books, ebooks, articles and online sources: DOI, database, sponsor, access date (L3034–3074). If you access a printed text online, record both the original and the access route (L3073–3074). Williams lost a publication for over a year to an incomplete record (L3095–3105).

*Active engagement.*
- "If you can, read important sources twice. First, read generously … Then … read it a second time slowly and more critically" (L3113–3121).
- "Test your understanding by summarizing: if you can't sum up a passage in your mind, you don't understand it well enough to disagree" (L3123–3125).
- "Don't accept a claim just because an authority asserts it. That assertion may be wrong or … obsolete." Experts disagree; that is "the mark of an active field," not "mere opinion" (L3126–3135).
- "Check—and Check Again": "check the accuracy of everything important to your argument" (L3138–3158).

*Reading for a problem.* Look for "claims that seem puzzling, inaccurate, or simplistic" (L3166–3169).
- **Creative agreement** (L3172–3218):
  1. offer additional (newer or stronger) support
  2. confirm unsupported claims
  3. apply a claim more widely
- **Creative disagreement** (L3220–3313):
  1. classification or definition
  2. parts and wholes
  3. history or development
  4. cause and effect: "Be especially alert to confusions of causation … with correlation" (L3277–3279)
  5. perspective
  
  Each comes with fill-in templates ("Source claims that ___ causes ___, but it doesn't/they are both caused by ___").

*Reading for arguments.*
- "No argument is complete until it acknowledges and responds to its audience's predictable questions and disagreements … You can't really understand what you think until you understand why a rational person might think differently" (L3324–3337).
- Borrowing a source's way of arguing is not plagiarism; "Readers will look for originality in your problem, claim, and evidence" (L3341–3350).

*Reading for data.*
- "if you can, check the primary source … use statistical data only if you can judge for yourself whether they were collected and analyzed appropriately" (L3374–3380).
- **Cite both** the primary and the secondary source ("Wong, 1989 … quoted in Anderson, 2015") (L3381–3390).
- Citations let "readers … retrace your steps" (L3391–3392).
- When a claim is used as support, "report not only the conclusion of the source but its reasoning and supporting evidence" (L3400–3409).

*Note system* (index-card template, L3429–3465):
- author, short title and page at top left; keywords at top right
- separate topics into separate notes
- accuracy: beware transcription errors
- record claims and interesting data
- note your agreements, disagreements, questions
- "Clearly distinguish (1) what you quote … (2) what you paraphrase or summarize … and (3) your own thoughts" using fonts or colors (L3458–3465)

Electronic options: documents, note apps, citation managers. Questions to settle: organization, use, availability, **backup** ("store your notes in, or at least back them up to, the cloud"), and fit (L3485–3546).

*Summarize / paraphrase / quote decisions* (L3566–3591):
- "A summary of a source never serves as good evidence."
- "A paraphrase is never as good evidence as a direct quotation."
- Quote when the words are evidence, an authority, strikingly original, or a claim you dispute.
- "Never abbreviate a quotation thinking you can accurately reconstruct it later."

*Context rules (4.6.4)* (L3599–3692):
1. Record the line of reasoning, not just the conclusion (Bartolli example).
2. Note the claim's role in the original: main point, concession or qualification. The Jones lung-cancer concession was misreported as her point.
3. Record scope and confidence; "Don't make a claim seem more certain or far-reaching than it is" ("suggests a link" ≠ "causes").
4. Don't mistake an author's summary of others for the author's own view.
5. Note *why* sources agree or disagree. "It is not 'research' when you uncritically summarize another's work … If you rely on at least two sources, you'll usually find that they do not agree entirely … Whom should we believe?"

*Annotation, annotated bibliography, keywords.*
- Annotate rather than just highlight (L3696–3729).
- In an annotated bibliography each entry should "evaluate the credibility … summarize its argument, and explain its relevance" (L3748–3761).
- Tag notes with ≥2 keywords "by what it implies for your question … Use the same tags … for related notes" (L3764–3776).
- "everything you read is relevant" to building knowledge (L3732–3744).

QT "Managing Moments of Uncertainty": a muddle comes "when you accumulate notes faster than you can sort them." Return to "What question am I asking? What problem am I posing?", explain the work to non-specialists, and ask "Am I missing anything important?" (L3778–3796).

**(d) Checks.** The summary-comprehension test, the context five, the two-source rule, and verifying quotes against originals.

**(e) Failure modes.**
- Screenshot or tab hoarding: "don't just snap a photo … That's easy, but it doesn't advance your thinking" (L2191–2195).
- Misquotation and misattribution.
- Reporting concessions as main points.
- Inflating certainty.
- Losing track of which words are yours.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: a `notes` table with fields `source_id`, `locator (page/para)`, `kind ∈ {quote, paraphrase, summary, own}`, `verbatim_text` (for quotes, as a hash-verified exact span from the source text), `role_in_source ∈ {main, sub, concession, qualification, summary_of_other}`, `hedge_level` (copied from the original modal verbs), `reasoning_chain`, `keywords[≥2]`, `my_response`.
- Gates:
  - every `quote` matches the source text exactly (string-match verification)
  - every `paraphrase` passes an n-gram overlap ceiling
  - a `role_in_source` field is required
  - a claim's hedge level may not be strengthened downstream (an automated modal-strength diff)
  - secondhand data carries both citations
  - every evidential claim has ≥2 independent sources, or an explicit "single-source" flag

### S8. Assembling the argument: overview (Part III prologue; ch. 5)

**(a) Goal.** Construct the argument as a cooperative answer to the audience's predictable questions.

**(b) Inputs → outputs.** Inputs: notes, the working claim, the problem. Output: a storyboard of claim → reasons → evidence (with warrants and acknowledgments/responses).

**(c) Heuristics.**
- Argument as "a lively conversation with amiable and sometimes skeptical colleagues … the goal is not for one side to coerce the other … the best answers are often not those that end the conversation but those that generate new questions" (L3824–3831).
- You answer "Why should I believe that?" (L3836–3837). You must "imagine them on your audience's behalf" (L3846–3849).
- **Five questions = five elements** (L3950–3956):
  1. **Claim:** What do you want me to believe?
  2. **Reasons:** Why do you say that?
  3. **Evidence:** How do you know? Can you back it up?
  4. **Warrant:** How does that follow?
  5. **Acknowledgment and Response:** But what about …?
- "Think of your research as the process of figuring out answers to these questions" (L3956–3957).
- The core is claim + reasons + evidence (L3961–3967). Reasons chain into sub-reasons (L3979–3993).
- "we base reasons on evidence … Reasons need the support of evidence; evidence should need no support beyond careful demonstration or a reference to a reliable source" (L4013–4020).
- Definitions (L4030–4053):
  - claim = an assertion demanding support
  - reason = an assertion supporting a claim
  - evidence = information deployed to support a reason, "not always framed as an assertion"
- Answer, solution, claim and point are the same thing viewed differently (L4023–4027; L6283–6293).
- **Storyboard** (L4161–4194): claim and each reason at the top of separate pages; evidence listed below each one, or "note the kind of evidence you'll need"; a warrant page where needed; acknowledgment pages. Pages stay unfinished, get moved, and reveal gaps.
- **Ethos**, the implicit sixth element: "Can I trust you? That answer must be Yes" (L4195–4210, L4247–4255).
- "Getting to Know You" (L3853–3896):
  - read journals that publish work like yours
  - rehearse the argument with an experienced researcher
  - get drafts read by people "as much like your intended audience as possible"
  - the physicists who learned how biologists think

QT "Falling Back on What You Know" (L4257–4288):
- don't import one field's evidence norms into another
- don't repeat one mastered argument type
- "seek out alternative methods, formulate not only multiple solutions but multiple ways of supporting them"

**(d) Checks.** Does every reason page have evidence or a named evidence need? Is there a warrant page where relevance is doubtful? Are there acknowledgment pages?

**(e) Failure modes.**
- A "data dump" (L3909–3911).
- Waiting to gather "every last bit of data" before planning (L3803–3807).
- Field-norm transfer errors.
- The one-trick argument.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `argument_graph.json` with node types `{claim, reason, evidence_report, warrant, acknowledgment, response}` and typed edges.
- Gates:
  - (i) every reason has ≥1 evidence child, or an open `evidence_needed` ticket that triggers a back-edge to S6
  - (ii) every leaf is an `evidence_report` linked to a verified note
  - (iii) ≥1 acknowledgment/response pair
  - (iv) the graph is acyclic and the root is the main claim

### S9. Making claims (ch. 6)

**(a) Goal.** A main claim of the right kind that is specific, significant, contestable, and qualified.

**(b) Inputs → outputs.** Input: the working claim. Output: the final main claim with qualifications and hedges.

**(c) Heuristics.**
- Three questions (L4302–4307):
  1. What kind of claim?
  2. Specific enough to suggest an argument?
  3. Significant enough to need one?
- **Claim kinds** (L4320–4366):
  - Conceptual: fact/existence; definition/classification; cause/consequence; evaluation/appraisal (which needs criteria of judgment)
  - Practical: action/policy, which "usually rests on prior conceptual claims"
- Practical claims need sub-arguments (L4373–4380):
  - feasibility
  - costs less than the problem
  - doesn't create a bigger problem
  - better than the alternatives
  
  "If your audience expects these sub-arguments and you don't make them, your audience may reject your whole argument."
- Don't tack practical action onto a conceptual claim; put it in the conclusion (L4380–4386).
- **Specificity:** "Vague claims lead to vague arguments … The claim's specificity signals how the argument is likely to proceed" (L4397–4410).
- **Significance:** "If an audience accepts a claim, how many beliefs must they change? And how foundational are those beliefs?" (L4416–4420). The ladder runs: new information < settling something puzzling < upsetting what seems long settled (L4421–4449).
- "Thicken" the claim with an *although* clause acknowledging one of three things (L4450–4484):
  1. a belief your claim challenges
  2. a conflicting view
  3. a condition limiting scope or confidence
- "Even small findings can be significant if they challenge current knowledge or raise new questions" (L4515–4517).
- Novices: "Imagine an audience made up of people like yourself … How much has your claim changed what you now think?" The key question is "not Why should I believe this? but Why should I care?" (L4525–4531).
- **Qualification:** "nothing damages your ethos more than arrogant certainty … Limit your claims to what your argument can actually support by qualifying their scope and certainty" (L4537–4545).
- Limiting conditions: mention only those "your audience might plausibly think of" (L4562).
- **Hedges:** Watson & Crick "wish to suggest," "In our opinion," "We believe" (L4598–4617). Audiences "distrust pat certainty expressed in words like all, no one, every, always, never … but if you hedge too much, you will seem timid … notice how experts in your field hedge" (L4618–4622).
- QT "Make Your Claim Contestable": state the opposite. If the opposite is nonsensical, demonstrably false, or so obvious that it "goes without saying," the claim is not worth arguing, though the test "isn't foolproof" (Copernicus) (L4624–4659).

**(d) Checks.**
- The negation test.
- The although-clause test.
- The belief-change estimate.
- The hedge audit.
- The claim checklist in ch. 9 (L5767–5774):
  1. Practical/conceptual, and matches the problem?
  2. Solves it?
  3. Specific?
  4. Contestable?
  5. Appropriately hedged?

**(e) Failure modes.** A topic disguised as a claim ("This report summarizes…"); overstatement; vague claims; missing practical sub-arguments.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `claim.json = {text, kind, scope_limits[], hedges[], although_clause, negation, significance_estimate}`.
- Gates:
  - the negation is judged neither absurd nor trivially false
  - the claim kind matches the problem type
  - every universal quantifier (all, never, always) is justified by evidence coverage or removed
  - the hedge strength is ≤ the strongest evidence strength in the argument graph
  - practical claims carry the 4 sub-argument nodes

### S10. Reasons and evidence (ch. 7)

**(a) Goal.** A logically ordered set of reasons, each grounded in evidence the audience will accept.

**(b) Inputs → outputs.** Inputs: the storyboard and notes. Outputs: ordered reasons and screened evidence reports.

**(c) Heuristics.**
- Audiences judge reasons for plausibility and order for logic; "If they don't believe the evidence, they'll reject the reasons and, with them, your claim" (L4670–4674).
- Test the order by reading "the reasons, not the details" on the storyboard (L4684–4687).
- "you don't get to decide whether your evidence is sufficient; your audience does … if they do question it, what you think is hard evidence becomes for them only another reason" (L4695–4699).
- Regress: "Such questions can, in principle, be asked forever, but at some point, we expect reasonable audiences to stop … Your responsibility is to offer evidence up to that point" (L4726–4730). See the tuition example, which moves from "crushing debt burden" to percentages and dollar amounts (L4702–4719).
- "data are always constructed and shaped by those who collect and use them" (L4740–4744).
- The kind of evidence follows the claim and the field (L4750–4775).
- **Evidence vs. reports of evidence:** "researchers cannot share with their audiences 'the evidence itself'" (L4792–4793). "facts are shaped by those who collect them and again by the intentions of those who use them … your audience wants to know they can trust the complete chain of reports" (L4819–4831).
- Follow standardized field methods: "When in sociology, do as sociologists do" (L4843–4850).
- "We live in an age awash in information … increasingly not even by other people but by computerized 'bots' … The last link in that chain of credibility is you" (L4854–4858).
- **Evidence criteria** (L4861–4864; detail L4900–5059):
  1. **Accurate:** the report matches the evidence; would a redo get the same numbers? Double-check. Skeptical audiences treat mistakes as signs of "broader unreliability." Questionable evidence can be used if you flag it; rejecting dubious support "show[s] yourself to be cautious, self-critical."
  2. **Appropriately precise:** watch "some, most, many, almost, often" used to "fudge." Precision is field-relative; evidence can also be "too precise" (the 2:13 p.m. Soviet-collapse example).
  3. **Sufficient and representative:** "Beginners typically offer too little evidence … one quotation, one number." Evidence must represent "the full range of variation." With sampled data "you must show that it is" representative. Anecdotal evidence is persuasive but risky. **Cherry-picking** "implies not just carelessness but dishonesty. To avoid it, you must show that you have considered all of the evidence available."
  4. **Authoritative:** "judgments about the authority of sources are typically made by research communities, not individuals" (CDC vs. Wikipedia).
  5. **Clear and understandable:** "Quotations … rarely 'speak for themselves'." Add the interpretive reason that links the quote to the claim (the Hamlet example). Redesign charts so the comparison shows. Image "enhancements" must not become "fabricating evidence."
  6. **Relevant:** the sixth criterion, deferred to ch. 8.
- QT "Assess Your Evidence as You Gather It": evidence is the only core element you don't formulate yourself. Note "your sense of its reliability, any concerns." This protects against "mechanically recording more and more … a kind of procrastination" (L5061–5084).

**(d) Checks.**
- The five-plus-one criteria applied before adding anything to the storyboard (L4896–4897).
- Re-check data and quotes against notes (L4913–4915; L7064–7065).
- Reasons questions (L5788–5790):
  1. Enough?
  2. Consistent?
  3. Relevant?
- Evidence objection list (L5795–5810):
  - wrong kind
  - out of date
  - inaccurate
  - imprecise
  - insufficient or unrepresentative
  - not authoritative
  - not understandable
  - not relevant

**(e) Failure modes.** Treating assertions as evidence, single-instance proof, cherry-picking, uninterpreted quotations, vague quantifiers, and fabrication by enhancement.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `evidence_reports[]`. Each record has `provenance_chain[]` (original → each intermediary), `method_note`, `criteria` (six graded fields, each with a rationale), `counter_evidence_considered[]`, and `interpretation_sentence` (how it supports the reason).
- Gates:
  - each reason has evidence passing all six criteria, or an explicit limitation disclosure
  - a numeric re-extraction check (an independent re-read of the source reproduces the number)
  - a representativeness statement for any sampled or selected data
  - a cherry-picking audit: a search for contrary evidence is logged and its results addressed
  - no `evidence_report` without a provenance chain back to an accessible source

### S11. Warrants (ch. 8)

**(a) Goal.** Make sure each reason is *relevant* to its claim, and state the connecting principle where the audience needs it.

**(b) Inputs → outputs.** Input: reason–claim pairs. Outputs: a warrant for each (implicit or stated) and the test results.

**(c) Heuristics.**
- "Warrants are general principles that connect reasons to claims" (L5113). A question about relevance is answered by a warrant, not by more evidence (L5109–5119).
- Every warrant question is "ultimately, a question about the basis of your beliefs" (L5119–5124).
- Most warrants are given by communities (L5125–5129).
- Rule: "state your warrants only if your audience holds different ones, will not be able to understand your reasoning unless you do, or may challenge your reasoning" (L5130–5134).
- Proverbs are cultural warrants, structured circumstance → consequence (L5138–5169).
- Research warrants (L5174–5236):
  - are specialized
  - are usually unstated among experts (stating them "could seem condescending")
  - are often compressed ("Shared DNA is the measure…")
  
  Restate them as **"When X, then Y,"** with "Both the circumstance and consequence … more general than the specific reason and claim."
- **Five warrant tests** (L5265–5273):
  1. **Reasonable?** If not, argue for it as a claim (L5275–5287).
  2. **Sufficiently limited?** Qualify it ("usually," "probably"), then show the exceptions don't exclude your case (L5289–5308).
  3. **Superior to competing warrants?** For example, bodily autonomy vs. public health; "culture wars" are about warrants (L5311–5342).
  4. **Appropriate to the field?** Legal warrants trump commonsense justice (L5343–5365).
  5. **Covers the reason and the claim?** Ahmed and Beth: the reason may not instantiate the circumstance, or the claim may not instantiate the consequence (L5368–5406).
- **When to state a warrant** (L5411–5441):
  1. the audience is outside your field
  2. the principle is new or controversial ("Refer to respected figures … If you can't … make an argument of your own")
  3. the claim is one people "just don't want … to be true"; then state the warrant first (the climate-consensus example, L5458–5471)
- "What You Don't Say Says Who You Are": unstated warrants affect ethos (L5444–5454).
- **Test your argument by imagining its warrant.** In the social-media example, no reasonable warrant links "mental health problems rising" to "social media is to blame." The fix is to revise the reason so that it instantiates an acceptable warrant, which may require new evidence (L5472–5527).
- **Challenging warrants**, by type (L5528–5630):
  - experience: challenge reliability or find counterexamples
  - authority: the authority lacks the evidence, oversteps its expertise, or isn't an authority
  - systems of knowledge: challenge the system, or show the case falls outside it
  - cultural: offer competing warrants, or note cultural specificity
  - methodological (generalization, analogy, sign): challenge the application or the limiting conditions
  - articles of faith: "A claim assumed to be true … cannot be considered a product of research"
- QT "Reasons, Evidence, and Warrants": "Researchers generally trust the first kind [evidence-based] more"; "you can't support a claim of fact … with a warrant and reason alone"; "whenever you can, rely not on elaborate lines of reasoning based on warrants but on hard evidence" (L5632–5675).

**(d) Checks.**
- The 5 tests for each reason→claim link.
- The marginal-warrant revision check: "write in the margin its most important unstated warrant. Then ask whether readers will accept it" (L7075–7079).

**(e) Failure modes.**
- Plausible-sounding causal leaps (correlation → blame).
- Over-rigid warrants.
- Unstated controversial warrants.
- Supporting factual claims by reasoning alone.
- Faith-based warrants placing a claim beyond dispute.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: each reason→claim edge in `argument_graph` carries `warrant = {circumstance, consequence, type ∈ {experience, authority, system, cultural, methodological, faith}, tests: {reasonable, limited, superior, field_appropriate, covers}, stated: bool, state_reason}`.
- Gates:
  - all five tests pass, as judged by an adversarial model instructed to find a competing warrant or a non-covering instance
  - `type == faith` is blocked
  - claims of fact must have direct evidence, not warrant-only derivation
  - warrants are stated whenever the audience profile from S0 is non-expert or the warrant is flagged novel

### S12. Acknowledgment and response (ch. 9; QT "Three Predictable Disagreements")

**(a) Goal.** Anticipate and answer questions about the problem, the argument's soundness, and alternatives, honestly and in proportion.

**(b) Inputs → outputs.** Input: the argument graph. Outputs: an objection inventory, a selected subset, and response sub-arguments.

**(c) Heuristics.**
- "Arguments are not just logical constructions; they are also social interactions" (L5691–5692).
- Three kinds of questions (L5707–5717):
  1. about the problem ("why the argument is worth making at all")
  2. about soundness
  3. about alternatives
- "Don't go easy on yourself: the time to fix a problem with your argument is when you find it" (L5720–5721).
- Problem questions (L5741–5756) and soundness questions (L5767–5810) are listed above in S4 and S10.
- "test your claims, reasons, and evidence as you expect the most skeptical members of your audience will … put your argument through your own 'stress test' before they put it through theirs" (L5815–5819). Some will stay unpersuaded: "So be it" (L5820–5822).
- **Alternatives** come mainly from sources: note where they "advance claims different from yours … Also note where one source disagrees with another." If a source your audience takes seriously doesn't persuade you, "don't ignore it. Instead, explain why" (L5837–5859).
- **Goldilocks** (L5864–5868). Response priorities (L5872–5878):
  - plausible charges you can rebut
  - lines of argument important in the field
  - conclusions the audience *wants* to be true
  - evidence the audience knows
  - important counterexamples
- Use acknowledgments to reiterate scope or to contrast with nearby solutions (L5879–5902).
- **Weaknesses you can't fix** (L5905–5921). First redefine or rebuild. Otherwise do not hide them: "that's dishonest". Candidly acknowledge them and respond that:
  - the rest of the argument compensates
  - more research may find a way around them
  - the argument still offers insight and "suggests what attributes a better answer would have"
- Turn a failed claim into a refuted hypothesis (L5922–5931).
- **Questions you can't answer:** don't seek "the last word … the goal of research is usually to … keep its conversation going." Be "candid about questions that still remain" (L5935–5945).
- **Responses are sub-arguments:** acknowledgment → explanation of why the objection doesn't apply → further concession → report of evidence (the PSA example) (L5949–5978).
- **Vocabulary**, from most dismissive to most respectful (L5992–6092):
  1. despite / regardless of / notwithstanding; although / while / even though
  2. seem / appear / may / could; plausibly / reasonably …
  3. attribution to an unnamed source ("It is easy to think …")
  4. a generic interlocutor ("There are some who might say …")
  5. your own voice ("Granted," "Admittedly," "It must be acknowledged …")
  
  Don't denigrate: "Save criticism for the response, and direct it at the work rather than the person" (L6052–6064).
- **Response vocabulary**, from tactful to blunt (L6094–6147): "I find it difficult to see how …"; "there remains the problem of …"; "ignores/is irrelevant to …"; "evidence is unreliable/shaky/thin"; "That principle holds in many cases, but not in all."
- **Three predictable disagreements** (L6149–6200):
  1. **Other causes:** "no effect has a single cause and no cause has a single effect."
  2. **Counterexamples:** think of them first; beware statistical illiteracy about aberrant cases (cold July in Miami).
  3. **Definitions:** "define it to support your solution and offer a subordinate argument for your definition. Don't treat a dictionary definition as authoritative"; acknowledge technical vs. common meanings.

**(d) Checks.** The skeptic stress test, the Goldilocks selection, and a candor check on known weaknesses.

**(e) Failure modes.**
- Ignoring weaknesses.
- Strawmanning and ad hominem.
- Too many or too few acknowledgments.
- Bare counter-assertion with no explanation.
- Aiming for the last word.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `objections.jsonl`, generated by a separate red-team agent per question category plus the three predictable disagreements. Each row has `source` (a source that raised it, or the imagined audience), `priority_class`, `selected`, `response_subargument_id`, and `concession_level`.
- Gates:
  - (i) every known unfixable weakness appears in the output with one of the three candid responses; hidden weaknesses are a hard fail
  - (ii) alternative causes are enumerated for every causal claim
  - (iii) definitional terms have a definition sub-argument
  - (iv) the response language passes a "no person-directed criticism" lint
  - (v) open questions are listed

### S13. Planning and drafting (Part IV prologue; ch. 10; QT "Managing Anxiety")

**(a) Goal.** Turn the argument into a reader-oriented plan and a first draft.

**(b) Inputs → outputs.** Inputs: the storyboard and the audience profile. Outputs: a working introduction, key terms, section plan, working conclusion, and draft.

**(c) Heuristics.**
- **Ready-to-plan checklist** (L6216–6227):
  1. You know the audience, what they know, and why they should care.
  2. You know your ethos.
  3. You can sketch your question and answer "in two or three sentences."
  4. You can sketch reasons and evidence.
  5. You know where a warrant is needed.
  6. You know the likely objections and your responses.
- Why a formal paper: "when you write for others, you demand more of yourself." Anticipate: "How have you evaluated your evidence? Why do you think it's relevant? What ideas have you considered but rejected?" (L6355–6362). Genres "represent not just what it knows but also how it knows" (L6374–6377).
- Use the field's standard format where one exists (IMRD) (L6332–6343).
- **Working introduction** (L6406–6520):
  1. **Context:** what you used to think, what most people think, an event, or what researchers have found. "summarize … only those sources whose findings you intend to extend, modify, or correct."
  2. **Problem:** a motivating statement beginning "but / yet / however," plus the So-what answer.
  3. **Response:** the claim restated in the same terms, or a "launching point" describing how the paper will proceed.
- **Key terms:** pick "the four or five terms that seem most important" and use them consistently; "thoughtful repetition can be an effective strategy" (L6521–6544).
- **Body:**
  - a background/definitions page
  - one storyboard page per section with its point (usually a reason)
- **Order options** (L6558–6603):
  - part-by-part
  - chronological
  - short → long
  - familiar → unfamiliar
  - less → more contestable
  - more → less important (or the reverse)
  - earlier understanding → later
  - general analysis → specific applications
  
  Whatever the order, "be sure it addresses your readers' needs and doesn't just reflect how your ideas occurred to you." Signal the order with transitions (L6604–6608).
- **Sections:** give each section its own distinguishing key terms ("If you cannot find such terms … It may offer little or nothing") and use them for headings. Mark where evidence, explanations of its reliability, acknowledgments and warrants go. "if you need to state a warrant, state it before you offer its claim and supporting reason" (the Oxford signatures example). In fact-heavy sections, add end-of-section summaries (L6611–6673).
- **Working conclusion:** the point plus its significance (L6676–6683).
- **Three flawed patterns** (L6691–6759):
  1. A narrative of your thinking. Test: sentences like "The first issue was … Then I compared …"
  2. A patchwork of sources ("patch writing": "This is all summary, no analysis"), which risks plagiarism.
  3. Mapping the assignment's language or structure; for compare/contrast, organize by conceptual parts, not one source after another.
- **Drafting:**
  - draft quickly with "[?]" placeholders, or slowly with a detailed plan
  - "Most writers work best when they draft quickly, revise carefully, and toss what's irrelevant" (L6778–6809)
  - "Deadlines and Drafting": "if you don't start, you'll never finish … that perfect paper … has never been written" (L6783–6801)
  - follow productive digressions (L6815–6818)
- QT Anxiety (L6831–6912):
  - double your time estimate
  - divide tasks
  - build on strengths
  - don't go it alone
  - "Don't let the perfect be the enemy of the good"
  - writer's block and "anchoring": "step back and purposefully open your mind to alternatives … You can even leverage generative AI … but … acknowledge this practice and clear it with your teacher"
  - "you are not your paper"
  
  Anxiety also drives plagiarism, because people "rationalize their choices under pressure" (L6846–6850).

**(d) Checks.** The ready-to-plan checklist, the narrative-language test, the patchwork test, and the section key-term test.

**(e) Failure modes.** The three flawed patterns, perfectionism and procrastination, and an order that mirrors your discovery process.

**(f) Agent mapping [agent-mapping inference].**
- Artifacts: `outline.json` (sections, each with point, key_terms, evidence slots, warrant-before-claim flags) and `draft_v1.md`.
- Gates:
  - all six ready-to-plan conditions hold
  - <N process-narrative sentences, found with a regex/LLM classifier for first-person process verbs
  - an own-analysis-to-quoted/paraphrased-text ratio above a threshold per section
  - the outline order differs from the order notes were collected, or is justified
  - a hard deadline policy: drafting starts after a bounded research budget

### S14. Revising and organizing (ch. 11; QT "Abstracts")

**(a) Goal.** Make the draft deliver to readers what you think you put in.

**(b) Inputs → outputs.** Input: the draft. Outputs: a revised draft and an abstract.

**(c) Heuristics.**
- Writers "write a first draft not to show to readers, but to discover what case they can actually make … analyze your draft objectively; otherwise, you'll just read into it what you want your readers to get out of it" (L6925–6933). Revising "doesn't mean pandering" (L6937–6940).
- Revise **top-down**: organization → sections → paragraphs → sentences → spelling. This reads as readers will and avoids polishing text you later cut (L6944–6959).
- **Frame** (L6963–6983). Readers must see instantly where the introduction ends, where the conclusion begins, and the point sentence.
  - Put the point at or near the end of the introduction.
  - The conclusion's version should not contradict it and should be "more specific and definitive."
  - The point sentence contains the key terms (the Crusades example, L6984–7031).
- **Argument** (L7041–7088):
  - each reason is a section's point
  - reason/evidence balance: too little evidence means "thin"; few reasons means a "data dump"
  - check data and quotations against notes
  - show how evidence relates
  - don't skip sub-reasons
  - add hedges
  - write the unstated warrant in the margin and test it
  - make it read "like a conversation with peers … asking hard but friendly questions"
- **Organization**, six checks (L7097–7160):
  1. Key terms run through the paper and appear in most paragraphs; otherwise you have wandered.
  2. Section beginnings are clearly signaled: could you insert headings?
  3. Transitions show the logic of the order.
  4. Ask of each section, "What question does this section answer?" If none of the five argument questions, nor context or background, "consider cutting it."
  5. State the point of each section at the end of its introduction; "Never bury it in the middle."
  6. Each section has distinct key terms; if two share terms, combine them.
- **Paragraphs:** open with key concepts; put the point first or last, never buried; keep length "just right" (L7164–7189).
- **Cool the draft,** then skim the top-level parts. "ask someone you trust to skim your paper and summarize it: if they get something wrong, your final readers likely will too" (L7193–7203).
- **Abstracts** do three things: state the problem, announce key themes, and state the main point or a launching point. Three patterns (L7205–7285):
  1. context + problem + point
  2. context + problem + launching point
  3. summary
  
  Put search keywords in the title and abstract (L7286–7289).

**(d) Checks.** All of the above. The cold-read summary test is the strongest.

**(e) Failure modes.** Bottom-up polishing, a buried point, intro and conclusion points that contradict each other, a wandering text with no key terms, and redundant sections.

**(f) Agent mapping [agent-mapping inference].**
- Artifacts: `revision_report.json` and `abstract.md`.
- Gates:
  - (i) a fresh-context reader model, given only the draft, summarizes the problem, the claim and the reasons, and the summary matches `claim.json` and `argument_graph` within a semantic-similarity threshold. This is the "someone you trust" test.
  - (ii) the intro point and conclusion point don't contradict, and the conclusion is more specific
  - (iii) key-term coverage ≥ X% of paragraphs
  - (iv) every section maps to an argument node or a background role
  - (v) every number and quote in the draft re-matches its note

### S15. Incorporating sources and citation (ch. 12; QT "Indicating Citations")

**(a) Goal.** Use sources fairly, show their relevance, cite completely, and avoid even inadvertent plagiarism.

**(b) Inputs → outputs.** Inputs: the draft and notes. Outputs: the integrated text, citations, and bibliography.

**(c) Heuristics.**
- "Most of your paper … should be in your own words … How much they trust you will be determined, largely, by how you use your sources" (L7299–7309).
- **Summarize** when only the overall ideas matter; **paraphrase** when you depend on specific points but not specific words; **quote** when (L7323–7341):
  - the words are evidence
  - they are strikingly original
  - they frame a discussion
  - they come from a backing authority
  - they state a view you dispute and "want to demonstrate your fairness"
- Cite every one. No "stitch[ing] together" (L7343–7349).
- **Fair summary:** may focus selectively but "cannot slant your summary so much that you misrepresent your source" (L7353–7360).
- **Fair paraphrase:**
  - Method: "read the passage until you think you fully understand it. Then, without looking back at the source, state your understanding out loud." 
  - Finger test: "If you can run your finger along your sentences and find similar words expressing the same ideas in the same order … your paraphrase is too close" (L7376–7406).
- **Quotations:**
  - run-in under about 100 words, block otherwise
  - insert by dropping in, by an interpretive lead-in sentence, or by weaving into your grammar
  - ellipses and brackets allowed only without changing meaning (L7410–7477)
  - mix with paraphrase and summary (L7481–7498)
- **Relevance:** "evidence rarely speaks for itself … The length of your explanation should be proportional to the length of your quotation" (L7502–7513).
- **Why cite** (L7517–7562):
  - community participation
  - your ethos ("readers don't trust sources they can't find … when writers can't get the little things right, they can't be trusted on the big ones")
  - readers' needs: they check the "reliability, currency, and completeness" of evidence; "range and depth" reassure them
  - honoring your sources
- **Styles:** Chicago author-title and MLA; Chicago author-date and APA. "automatically generated citations aren't perfect" (L7580–7587). "follow the prescriptions … down to the last comma" (L7643–7644).
- **Plagiarism risks** (L7655–7663):
  - an uncited quotation, paraphrase or summary
  - exact words cited but not in quotation marks
  - a paraphrase too close to the original
  - ideas or methods uncited
  
  "passages … created wholesale through generative AI" are also cheating (L7652–7654).
- **Guards:**
  - cite each use, and cite different pages individually; "include the citation as soon as you add a quotation" (L7668–7682)
  - writers slip "because they lose track of which words are theirs" (L7674–7678)
  - signal every quotation, including striking phrases; "it's better to seem naive than dishonest … use quotation marks freely" (L7685–7723)
  - cite ideas when "(1) the idea is associated with a specific person and (2) it's new enough not to be part of a field's common knowledge"; same for unique methods (L7733–7748)
  - "Inadvertent Plagiarism Is Still Plagiarism." Test: "If the author whose ideas you borrowed read your writing, would that author recognize your words or ideas … as their own?" (L7751–7762)
  - harms: credibility, and "erasing a bibliographic trail" (L7763–7773)

**(d) Checks.** The finger test, the author-recognition test, citation-at-insertion, and the quote-explanation proportionality check.

**(e) Failure modes.** Patch writing, slanted summary, close paraphrase, unmarked striking phrases, uncited ideas or methods, and blind trust in auto-citation.

**(f) Agent mapping [agent-mapping inference].**
- Artifacts: `citations.json` (each in-text span → note_id → source_id → page) and `bibliography`.
- Gates:
  - (i) every sentence derived from a note has a citation (provenance tracking from note to sentence)
  - (ii) exact-match quote verification
  - (iii) a paraphrase similarity ceiling (n-gram/sequence overlap) against the source span
  - (iv) a distinctive-phrase detector forces quotation marks
  - (v) the citation style is validated by a formatter and a human-style linter, and DOIs resolve
  - (vi) each quotation is followed by an interpretive sentence of length proportional to the quote
  - (vii) an AI-use disclosure entry exists (see section 3)

### S16. Communicating evidence visually (ch. 13; QT "Look for Opportunities to Include Visual Evidence")

**(a) Goal.** Choose and design graphics that are clear, framed, and honest.

**(b) Inputs → outputs.** Input: quantitative data. Outputs: tables and figures with titles or legends and introductory sentences.

**(c) Heuristics.**
- Terms: tables vs. figures; charts (bars, points) vs. graphs (lines) (L7892–7898). A few numbers fit in a sentence; more than a few need a table (L7902–7931).
- Table = "precise and objective," bar chart = contrasts, line graph = continuous change. "Choose the form that achieves the effect you want, not the one that first comes to mind." Beginners should stick to basic forms (L7934–7982).
- Don't let software dictate design (L7987–7991).
- **Frame each graphic** (L7994–8072):
  - a descriptive title or legend, not a topic and not an interpretation, distinguishing similar graphics
  - add helpful information inside it (change columns, annotations)
  - introduce it with a sentence that says how to interpret it, and highlight the focus
- **Simplicity** (L8075–8128):
  - only relevant data, with other data "for the record" in an appendix
  - no background color, no dark grid lines
  - patterns or labels, not color alone (accessibility)
  - no iconic or 3D bars "merely for effect"
  - label everything, prefer direct labels, add numbers where they matter
- **Tables:** order by emphasis, not alphabetically; round to a relevant value; totals at the bottom or right (L8133–8199).
- **Bars:** arrange to match the message; stacked bars only for comparing wholes; avoid pie charts for detailed quantitative data (L8203–8257).
- **Lines:** choose the variable direction that fits the point; ≤6 lines; dots for fewer than ~10 points; don't rely on gray shades. "Construct alternative graphics, then ask someone unfamiliar with the data to judge" (L8267–8293).
- **Ethics** (L8297–8356):
  - truncated axes exaggerate
  - dual axes imply false correlation
  - stacked areas mislead
  
  Four rules:
  1. "Do not manipulate a scale to magnify or reduce a contrast."
  2. "Do not use a figure whose image distorts values."
  3. "Do not make a table or figure unnecessarily complex or misleadingly simple."
  4. "If the table or figure supports a point, state it."
- QT: "Decide to use at least one graphic" and study how the field uses visuals (L8361–8388).

**(d) Checks.** Title and legend rules, the intro sentence, the four ethics rules, and testing alternatives on a naive viewer.

**(e) Failure modes.** Software-driven chart junk, unlabeled axes, color-only encoding, truncated or dual-axis distortions, and alphabetical ordering.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `figures/` with a spec (data source → transform → chart spec) for each figure.
- Gates:
  - a linter checks: y-axis starts at 0 for bar charts, or the truncation is disclosed; no dual axes without justification; no 3D; a colorblind-safe palette plus patterns or labels; an axis-label presence check
  - the title is descriptive, with no evaluative words
  - an introductory sentence exists and names the point
  - figure data hash-match the evidence-report data

### S17. Introductions and conclusions (ch. 14; QT "Use Key Terms in Titles")

**(a) Goal.** Motivate readers with a problem they care about, then leave them with the point, its significance, and open questions.

**(b) Inputs → outputs.** Input: a working draft. Outputs: the final introduction, conclusion and title.

**(c) Heuristics.**
- A catchy opening isn't enough; "what keeps their attention is a problem they think needs a solution and a promise that you have found it" (L8404–8410).
- The universal structure is **context + problem + response** (L8464–8470).
- **Context:** a "stable context" that the problem disrupts (fairy tale: "Hungry Wolf"). It establishes common ground. Context types:
  - accepted understanding or prior research (statins)
  - a misunderstanding
  - obsolete research
  - a misunderstanding about the problem itself
  
  You may skip the context when the problem is well established (L8514–8592).
- Don't skimp on context, as if continuing a class conversation, and don't list every source: "Survey only those sources whose findings you will directly modify" (L8594–8611).
- **Problem:** condition + consequence. "Readers are typically more motivated by a real cost than by a potential benefit … state costs or consequences when presenting your problem; state benefits to intensify your solution" (L8615–8648).
- In the sciences, "readers will not know the particular gap … unless you tell them." In the humanities, describe the imperfect understanding explicitly (L8674–8683).
- On consequences: "If in doubt, state them" (L8686–8691). The applied version gives a tangible cost; the pure version gives a misunderstanding (L8692–8723).
- **So-what test** on the condition sentence (L8726–8740). "you have to show them that your problem is their problem." Otherwise: "Wrong audience … find (or create) an audience" (L8745–8757). For beginners, stating the consequences "for yourself" is a big step (L8765–8770).
- **Response:**
  - State the gist at the end of the introduction ("the best approach"). This puts "readers in charge"; fears of boring readers are "baseless" (L8774–8810).
  - Or promise a launching point with key terms; the weakest is "This study investigates [topic]." Better, outline the solution or method (L8816–8856).
- **Pace** signals the imagined audience. Open quickly for peers and slowly for novices; mismatches make you seem ignorant or inconsiderate (L8860–8895).
- **First-words clichés** (L8899–8921):
  - repeating the assignment
  - a dictionary definition
  - "Throughout history"
  - grand openings
  
  These target the wrong community: too narrow or too broad. Better openings: a noteworthy fact, a striking quotation (only if it anticipates key terms), or a relevant anecdote (L8925–8962).
- **Conclusion** runs the elements in reverse (L8966–9022):
  1. main point stated "more fully," not word for word
  2. a new significance or application, which "suggests further questions"
  3. a call for more research: "imagine someone fascinated by your work who wants to follow up"
- **Title:** written last, with key terms and a two-part colon form (L9024–9040).

**(d) Checks.** Identify the C/P/R parts, apply the So-what test, check the pace against the audience, check the title's key terms against the paper's.

**(e) Failure modes.** Gimmicky openings, missing context, an exhaustive literature dump, a vague topic announcement, a conclusion that merely restates, and practical application in the introduction instead of the conclusion (L2052–2055).

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `intro_conclusion.json` with labeled spans `{context, condition, consequence, response|launch, conclusion_point, new_significance, future_research}` and `title`.
- Gates:
  - all spans present, unless a documented reason exists
  - context-cited sources ⊆ sources that the paper extends, modifies or corrects
  - the response appears in the last paragraph of the introduction
  - ≥1 future-research item
  - the title contains ≥2 of the top key terms
  - no cliché-opening patterns, checked by regex

### S18. Revising style (ch. 15; QT "The Quickest Revision Strategy")

**(a) Goal.** Sentences as clear as the ideas allow, judged by diagnosable causes rather than impressions.

**(b) Inputs → outputs.** Input: the revised draft. Output: a stylistically revised and polished text.

**(c) Heuristics.**
- You can't detect your own unclear sentences "just by reading them" (L9055–9060).
- Dense prose is usually "hazy thinking"; novices "imitate the tangled prose they read" (L9099–9111).
- Apply the principles in revision, not while drafting (L9137–9140).
- **Principle 1:** "Readers will judge your sentences to be clear … to the degree that their subjects name the main characters in your story" (L9190–9193).
- **Principle 2:** express actions as verbs, not nominalizations (-tion, -ment, -ence, -ity) (L9196–9276).
- **Diagnose:** highlight the first 6–7 words of each clause. Do you reach the subject? Is it a concrete character? Are the verbs specific? (L9283–9298)
- **Revise:** find the characters (invent them if needed), turn actions into verbs, and use If/because/although/when frames (L9300–9311).
- Characters can be abstractions familiar to readers; avoid needless extra abstractions; "tell the right story, which is not always the most concrete one" (L9316–9439).
- **Principle 3, old before new:** begin sentences with familiar information. "should you ever have to choose between beginning a sentence with a character or with familiar information, always choose the principle of old before new" (L9443–9517).
- **Active vs. passive:** choose by old→new flow. Use the passive for repeatable procedures and first-person active for actions only the authors can claim (*conclude, argue*) (L9521–9605).
- **Principle 4, complexity last:** put new technical terms, complex units, and concepts to be developed at the end of the sentence, the stress position (L9609–9706).
- **Editorial polish:** grammar, spelling, punctuation, conventions for numbers and names (L9710–9723).
- QT: start where explaining was hard; "read your writing out loud. If you stumble, your readers will too." Diagnose and revise for clarity and flow, then for emphasis (L9725–9782).

**(d) Checks.** The first-6–7-words and last-5–6-words procedures, a nominalization count, and the read-aloud test.

**(e) Failure modes.** Abstract subjects, nominalization clutter, new information at sentence starts, mechanical avoidance of the passive, and style applied while drafting.

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `style_report.json` with per-sentence metrics: subject-onset index, nominalization density, old/new continuity (lexical overlap between sentence start and prior context), and end-position salience.
- Gates: thresholds on the share of sentences failing the subject-onset and continuity checks, with LLM rewrites constrained not to change claims or hedges (diff-checked against `claim.json` hedge strengths).

### S19. Research presentations (ch. 16; QT "Treat Your Presentation as a Performance")

**(a) Goal.** Deliver the argument live to auditors, both preliminarily (to test it) and finally (to share it).

**(b) Inputs → outputs.** Input: the argument. Outputs: notes, slides, handouts, prepared Q&A, accessibility materials.

**(c) Heuristics.**
- Auditors can't reread; "they need help to follow complicated lines of thought" (L9813–9818).
- When reading aloud: at most 2 min per 300-word page, eye contact at least once or twice per page, explicit purpose and organization, shorter sentences, lots of I/we/you (L9826–9842).
- **Accessibility:** don't rely on color alone; large fonts; captions; describe visuals orally; provide a script, slide handouts and audio descriptions (L9845–9871).
- **Notes:**
  - not full sentences
  - a full introduction and conclusion
  - reasons in bold, in order, with 2–3 best pieces of evidence named
  - one page per point, stated as a claim
  - numbers and quotes written out
  - "cover the most important ones first … Never build up to a climax that you might not reach"
  - resist a full script (L9874–9922)
- **Preliminary presentation:** goals are (1) to forecast the argument and see "whether it makes as much sense when you say it as when you think it" and (2) to test ideas through responses (L9937–9940). Present the problem and claim, the reasons, and a preview of the evidence (L9941–9946). Plan discussion questions around "What areas of my argument am I most unsure about? … What might I be missing?" (L9980–9988).
- **Final presentation:** "present one best bit of evidence for each reason" (L9997–9999). Narrow the focus: 8–10 double-spaced pages per 20 minutes; give either the claim with a sketch or a summary of one sub-argument (L10002–10026).
- **Introduction**, at most about 3 minutes (L10030–10067):
  1. research extended, modified or corrected (≤1 min)
  2. the question/gap (≤30 s)
  3. So what? (30 s)
  4. the claim up front (≤30 s)
  5. the structure as an oral table of contents
  
  "If you can't remember the content, you're not ready."
- **Conclusion:** the claim in more detail, a new So-what, and more research; time it, and "If your presentation runs short, don't ad lib" (L10070–10087).
- **Q&A:** prepare for data and source questions. For an unknown source, acknowledge that you haven't read it and will check. Pause, ask for a rephrase, and "Use them to refine your thinking" (L10090–10101).
- **Handouts:** be selective (L10104–10113).
- **Performance:** look up, speak naturally, extemporize, show personality, have fun (L10115–10164).

**(d) Checks.** Rehearsal timing, notes covering all reasons, accessibility items, prepared Q&A.

**(e) Failure modes.** Reading at speed, slide-reading, a climax you never reach, overstuffing, and bluffing about unknown sources.

**(f) Agent mapping [agent-mapping inference].** A "preliminary presentation" is a structured mid-project review in which the agent presents the problem, claim, reasons and evidence preview to critic agents with self-identified uncertainty questions.
- Artifact: `review_round_N.json` (questions asked, critiques, dispositions).
- Gate: every critique is dispositioned (accepted → change, or rejected → reason).
- For an unknown source raised in review, the agent must retrieve and evaluate it rather than dismiss it.

### S20. Ethics (ch. 17; also 3.6, 13.5, 12.9; ch. 18)

**(a) Goal.** Integrity toward yourself, obligations to audience and community, and social responsibility, at every stage.

**(b) Inputs → outputs.** Inputs: the whole project. Outputs: an ethical self-assessment and argued justification.

**(c) Heuristics.**
- Three sets of obligations: to yourself, to audiences and research communities, and to others affected (L10184–10187).
- **"Thou shalt nots"** (L10196–10204):
  - Do not plagiarize.
  - Do not claim credit for the results of others.
  - Do not misrepresent sources, invent evidence, or fake results.
  - "Do not present evidence whose accuracy you don't trust, unless you say so."
  - "Do not conceal objections you cannot rebut."
  - "Do not caricature or distort opposing views."
  - "Do not destroy data or evidence or conceal sources important for those who follow."
- Generative AI raises the stakes: "the power to access, process, analyze, create, and fake information and even arguments has never been greater … it is, if anything, more important than ever for researchers to maintain their personal integrity" (L10217–10221).
- **Affirmative duties to the community** (L10238–10249):
  - asking others to accept evidence in good faith sets "a standard for your work higher"
  - responsibility for changing others' beliefs
  - acknowledging strongest objections honors "their dignity"
- Fudging "always erode[s] the trust," with tangible harms (the falsified MMR–autism study; withdrawn drugs) (L10251–10260).
- Power: Watson/Crick vs. Franklin credit (L10261–10274).
- Plagiarism, "whether that work was done by another researcher, purchased online, or generated with AI technology," steals recognition (L10276–10284).
- The ethical summary: "respect sources, preserve and acknowledge evidence that run against your results, assert claims only as strongly as they deserve, acknowledge the limits of your certainty" (L10295–10302).
- **Social responsibility** extends to facilitators, research subjects, and anyone touched by the research, including through "the questions you ask" (L10304–10322).
- Review boards: "Do no harm" (L10331–10339). Field codes: JAMA race/ethnicity reporting; Nature against "helicopter research" and "ethics dumping" (L10340–10354).
- "you need to be able to argue that it is ethical" (L10359–10362).
- **Ethics warrants** (L10372–10380). Research is "(possibly) unethical" when it:
  - relies on deception
  - exploits a researcher/subject power differential
  - exploits a socioeconomic or geopolitical differential
  - has the potential to harm individuals, societies or the environment
- Human subjects: plan; follow review; safeguards are "not … bureaucratic make-work" (L2924–2936).
- Visual honesty (L8297–8356).

**(d) Checks.** The 7 shalt-nots as a checklist, the 4 ethics warrants, and an ethics argument.

**(e) Failure modes.** Fabrication, image alteration, data destruction, uncredited use of others' data, exploitation of marginalized communities, and pressure-driven rationalization (L6846–6850).

**(f) Agent mapping [agent-mapping inference].**
- Artifact: `ethics_assessment.md` with an argued claim that the project is ethical, structured with the ch. 8 warrant tests.
- Gates (hard fail, no override):
  - unverifiable evidence presented without a flag
  - an unrebutted objection suppressed
  - a fabricated or unresolved citation
  - missing AI-use disclosure
  - human-subject data without a documented review analog
  - raw data and logs not preserved (append-only research log)

**Chapter 18 (Advice for Teachers), read as design guidance.**
- Formal rules taught as empty drill kill discovery (L10434–10459).
- Good assignments (L10467–10541):
  1. outcomes beyond a product, with a real audience
  2. help learning about the audience
  3. scenarios rich in context
  4. interim responders, who do more than "editing"
  5. time and interim deadlines, "for false starts and dead ends"
  6. ethics by design; a rich local context "guards against the unethical use of generative AI because the large language models … don't capture local contexts well or class discussions at all" (L10538–10541)
- Success for beginners can be posing a problem convincingly even without solving it (L10561–10564).

**[agent-mapping inference]** For an agent orchestrator, these translate into an explicit audience/context spec, interim critic checkpoints with role-specific rubrics, budgeted time for dead ends, and staged deadlines.

---

## 3. Everything the 5th edition says about generative AI / LLMs / chatbots

`grep` for generative/AI/chatbot/ChatGPT/LLM/large language finds the following (no "ChatGPT" or "chatbot" occurs):

1. **Introduction, examples of everyday research (L630–633):** "PROBLEM: You are looking for new books to read at the beach. RESEARCH: You ask a generative AI tool for suggestions and check them out online." Note the built-in verification step: "check them out online."
2. **QT Finding Topics (L1639–1641):** "Ask a generative AI tool to suggest questions. Then evaluate those questions to see if any are worth addressing, and try to fit the best of them into our three steps."
3. **3.3 Locating Sources Online (L2632–2635):** "maintain a healthy skepticism: much of what we find through Google, other search engines, or generative AI is reliable, but not everything is. In contrast to your library's catalogs and databases, the internet is essentially unmonitored."
4. **QT "Using Generative Artificial Intelligence" (ch. 3, L2938–2989).** Full content:
   - Framing: "In late 2022, a new kind of 'source,' a generative artificial intelligence (AI) tool able to respond to questions and prompts with passages of cogent text, became publicly available … This technology will almost certainly revolutionize much about our lives, including how we research, argue, and communicate … we can offer you some general principles that will help you use generative AI productively and ethically" (L2939–2946).
   - **Explore:** "Every new technology is accompanied by new affordances … So play around with it. Put it through its paces. Ask it questions about your research project or to come up with research questions of its own … Use it to prod your thinking, to generate information, to recommend additional sources, even to create text that you might revise as part of a paper. You won't know how it can help you until you try" (L2947–2955).
   - **Communicate:** "we don't yet have agreed-upon standards for how and when it can be used appropriately in research and writing. If you are a student, discuss that question with your teachers and advisers. Know your school's policies. If you are a researcher, talk with other members of your research community. Learn what practices are endorsed by your field's professional associations or leading journals. And share your own thoughts and suggestions" (L2956–2963).
   - **Be honest:** "Don't use generative AI to falsify data, solve problems you are supposed to solve without such assistance, draft text that you pass off as your own, and so on … A good rule of thumb is this: if you would feel uncomfortable telling a teacher, mentor, or journal editor how you used generative AI, don't use it in that way" (L2964–2972).
   - **Be critical:** "Generative AI is in its infancy, and it will get better and better. But right now, its results are not always reliable. We ourselves … have tested it by asking it questions related to our own areas of expertise. We've found that it can produce useful information and insights but that it can also offer false facts, misattribute quotations, even make up references. So be careful: understand that generative AI is powerful but fallible, and confirm your results using other methods" (L2973–2980).
   - **Be transparent:** "Let your audience … know exactly how you used generative AI in your research and writing. We suggest—at least until there are established conventions governing the matter—that when presenting your research orally or in writing, you acknowledge how and to what extent you used generative AI, just as you would acknowledge any other source. If you are writing a paper, include a statement on your works cited page or in your bibliography. If you are delivering a presentation, consider giving a brief acknowledgment at its beginning or end" (L2981–2989).
5. **7.4, information environment (L4854–4858):** "We live in an age awash in information, much of it of dubious reliability, and much of it created—increasingly not even by other people but by computerized 'bots'—with the intent of manipulating or deceiving us. The last link in that chain of credibility is you." This is about bots, not explicitly LLMs, but it is related.
6. **QT Managing Anxiety, writer's block (L6886–6899):** to combat "anchoring … You can even leverage generative AI: plug in the passage that's got you stuck and ask for alternatives—but if you do, be sure to acknowledge this practice and clear it with your teacher (see the Quick Tip at the end of chapter 3)."
7. **12.9 Plagiarism (L7650–7654):** students "know they cheat when they pass off as their own long passages copied directly from their sources or created wholesale through generative AI."
8. **17.1 Integrity (L10217–10221):** "Thanks to ever-more accessible technologies such as generative AI and their ever-expanding capacities, the power to access, process, analyze, create, and fake information and even arguments has never been greater. For this reason, it is, if anything, more important than ever for researchers to maintain their personal integrity."
9. **17.2 Plagiarism as theft (L10276–10280):** "To misrepresent a source or to take credit for work that is not one's own (whether that work was done by another researcher, purchased online, or generated with AI technology) is to steal the modest recognition that honest researchers should receive."
10. **18.2 Assignment design (L10536–10541):** "a teacher who establishes a rich social and rhetorical context in a class also guards against the unethical use of generative AI because the large language models that enable these technologies don't capture local contexts well or class discussions at all."
11. **Index entries:** generative AI and bibliographies (73), communicating (72), ethical use (72, 286, 296), honesty (72–73, 209–10), transparency (73) (L10876, L10915, L10982, L11006, L11022, L11200). These map to items 4 and 6–9 above.

**The book's implicit stance, synthesized [inference, but grounded in the citations above]:**
- AI is a *source* ("a new kind of 'source'") and a thinking prod.
- It is fallible, with three named failure types: false facts, misattributed quotations, fabricated references.
- Every AI output needs independent confirmation.
- AI use must be disclosed like a source citation.
- Using AI to falsify, to substitute for required work, or to pass off text is dishonest.
- AI weakens on local, situated context.

**[agent-mapping inference]** For an autonomous agent that *is* an LLM, the book's rules become self-constraints:
- (1) Treat its own parametric recall as an unvetted tertiary/"online" source. It may never be cited as evidence.
- (2) Mandatory retrieval-based confirmation of every factual statement, quotation and reference: resolve the DOI or catalog record and exact-match the quote.
- (3) A disclosure section in every deliverable listing which steps were model-generated.
- (4) A "discomfort test" proxy: any action the agent would not log transparently is prohibited.
- (5) Weight local context (user-provided materials, institutional norms) above model priors.

---

## 4. Reader/audience model

**Roles.** Writer and audience enter a *social contract*. Research "depends on an implicit contract between researchers and their audiences" (L573–575). Communication is "a social activity in which writers … and their audiences each play a part" (L665–667). The writer chooses one of three roles (information / practical solution / understanding) and must cast the audience in a complementary role: "If you cast them in a role they won't accept, you can lose them entirely" (L796–804). In authentic research the student–teacher roles reverse: the writer knows what the audience needs to know (L724–731). The 5th edition says "audience" generally and keeps "reader" for writing (L493–505). For live talks, the audience is "auditors" (L9806–9810).

**The reader's predictable questions.** These are the master interface the book returns to.
- "So what?" / "Why should I care?" (L1481–1490; L4529–4531; L5732–5733), the most important; the dreaded response is "I don't care" (L889–890; L2165–2166; L8408–8410).
- "Why should I believe that?" (L3836–3837).
- The five argument questions (L3950–3956): What's your point? Why do you say that? How do you know? How does that follow? But what about …?
- "Can I trust you?", the tacit ethos question (L4254–4255).
- Evidence-quality questions: wrong kind, out of date, inaccurate, imprecise, unrepresentative, not authoritative, unclear, irrelevant (L5795–5810).
- Problem questions: Is there a problem? Is it well defined? Practical or conceptual? Significant? Can you solve it? (L5741–5756).
- The three predictable disagreements: other causes, counterexamples, definitions (L6149–6200).
- Readers of formal papers: "How have you evaluated your evidence? Why do you think it's relevant? What ideas have you considered but rejected?" (L6360–6362).
- Readers want "a sense of the whole, its structure, and … why they should read your paper" before the parts (L6945–6948). They preview citations "to see whose work you read and whose you didn't" (L7542–7550).

**How the audience drives every stage.**

| Stage | How the audience drives it |
|---|---|
| S1–S3 | Step 3 of the formula is phrased "help my audience understand" (L1556–1557); significance is judged "by the cost they pay" (L1779–1781). |
| S4 | A problem exists only when the audience stops asking So what? (L1800–1802); "Wrong audience" (L1929–1931; L8753–8757). |
| S6–S7 | Sources reveal the audience's alternative views, and "your audience will be made up of people like your sources' authors" (L5856–5859); the audience's field decides the acceptable evidence kinds (L1058–1064). |
| S8–S12 | Arguments are answers to imagined audience questions (L3843–3849). Sufficiency is decided by the audience (L4695–4696). Warrants are stated depending on the audience (L5204–5208). Acknowledgments are chosen by what the audience believes, knows, or wants to be true (L5872–5878). |
| S13–S18 | Order "addresses your readers' needs and doesn't just reflect how your ideas occurred to you" (L6601–6603); key terms "signal how you imagine your audience" (L6526–6528); pace signals peer vs. novice audience (L8866–8868); style is revised because "you will read into them what you want your readers to get out of them" (L9057–9059). |
| S19 | Auditors need explicit structure and the claim up front (L10055–10057). |
| Iteration | Revisit the audience checklist at start, planning and revision (L988–989); get feedback from people "as much like your intended audience as possible" (L3880–3881); "If your group has a problem with your draft, so will that audience" (L1133–1134). |

**[agent-mapping inference]** Implement the audience as a persistent *simulated-reader agent* initialized from `audience.yaml`. It is queried at every gate with its role-specific question set (So what? / Why believe? / How follows? / What about? / Can I trust you?). The book's "someone you trust … summarize it" test (L7200–7202) becomes a blind-reader evaluation.

---

## 5. Cross-cutting principles relevant to reliability

1. **Source verification and provenance.**
   - Record complete bibliographic data first (L3023–3026).
   - Check primary sources rather than secondhand reports; use statistics only if you can judge how they were collected (L3374–3380).
   - Cite both primary and secondary when using secondhand data (L3384–3390).
   - "check the accuracy of everything important to your argument" (L3152–3153).
   - Avoid unauthorized and inaccurate copies (L2677–2679).
   - Judge credibility by both the backward and forward citation network (L2616–2619).
   - The 11 reliability signs are "no guarantee" (L2846–2850).
   - AI output must be confirmed "using other methods" (L2979–2980).
2. **Evidence vs. reports of evidence.**
   - You can only offer reports; "facts are shaped by those who collect them and again by the intentions of those who use them"; audiences must "trust the complete chain of reports" (L4779–4831).
   - Data are "constructed" (L4740–4742).
   - Evidence questioned by the audience becomes a reason needing evidence (L4697–4699).
   - "The last link in that chain of credibility is you" (L4857–4858).
3. **Honesty about limitations and uncertainty.**
   - Qualify scope and certainty (L4544–4545). Hedge like the experts in your field (L4618–4622). State limiting conditions (L4548–4587).
   - Flag dubious evidence (L4916–4919).
   - Acknowledge unfixable weaknesses candidly (L5905–5921). Admit limitations "before your audience objects" (L5813–5815).
   - Be candid about unanswered questions (L5943–5945).
   - Preserve and acknowledge contrary evidence (L10296–10298).
   - Never strengthen a source's confidence in notes ("Record the scope and confidence of a claim," L3659–3670).
4. **Avoid falling back on what you know.**
   - Don't start from a claim you know you can prove (L4259–4266).
   - Don't transfer one field's evidence norms to another (L4267–4278).
   - Don't repeat one mastered argument type (L4279–4288).
   - Related: confirmation-seeking search is "wrong" (L2248–2251); seek challenging sources (L2321–2322; L3333–3337); read generously before critically so you don't rig the reading (L3113–3118); "anchoring" bias (L6886–6892).
5. **Managing uncertainty and anxiety.**
   - Uncertainty "doesn't signal incompetence, only inexperience" (L2183–2184).
   - A muddle is a sign of accumulating faster than sorting; return to the 3-step formula and explain to non-specialists (L3778–3796).
   - Cognitive overload is normal when writing about matters you don't yet understand (L4211–4243).
   - Break tasks down, set reasonable standards, start early, don't wait for perfection (L6783–6801; L6855–6881).
   - Trust your gut on worthwhile questions (L1354–1367).
   - Assessing evidence as you gather guards against procrastination by note-taking (L5079–5081).
6. **Groups and collaboration.**
   - Writing groups of 4–5 with the 3-part sentence and elevator pitch, and discussion of the audience (L1116–1138).
   - Partners and teams discuss "not just what you are doing but why" (L1588–1591).
   - Ask for help to find problems (L2090–2094); librarians (L2429–2452); experts (L2906–2915); "smart friends" (L2920–2923).
   - Interim responders, not mere "editing" (L10512–10522).
   - Preliminary presentations for feedback (L9926–9936).
   - Know your teacher's rules on group help (L1139–1142).
   - The discourse-based view: knowledge is made "when research communities hash things out" (L451–456).
7. **Conversation, not combat.**
   - Arguments are cooperative inquiries; "the best answers … generate new questions" (L3824–3831).
   - Read like a conversation with peers (L7080–7088).
   - Criticize work, not persons (L6063–6064).
8. **Field conventions as epistemic infrastructure.**
   - Follow standard methodologies so audiences can accept evidence on your word (L4834–4850).
   - Genres encode "how it knows" (L6374–6377).
   - Warrants must be appropriate to the field (L5343–5365).
   - Authority is assigned by communities (L4992–5000).
9. **Bias awareness.**
   - Bias embedded in questions (L1851–1860).
   - Bias from bibliographic trails (L2596–2601).
   - Institutions excluding new or diverse voices (L2739–2745).
   - Under-citation of marginalized scholars (L2839–2844).
   - Power differentials in research (L10323–10354).

---

## 6. Master checklist: every concrete check, grouped by stage (candidate agent quality gates)

**S0 Audience**
1. Identify the audience type: professional, informed general, or novice (L991–996).
2. Identify the expected role: entertain, inform, understand, or solve (L998–1004).
3. Estimate prior knowledge and whether they recognize the problem, have it unrecognized, or it is only yours (L1005–1013).
4. Predict reactions to the claim: contradiction, standard counterarguments, wanting to see the steps (L1015–1022).
5. Re-run 1–4 at planning and at revision (L988–989).
6. Match register to the audience: no indifference, no talking down (L695–700).

**S1 Topic**
7. List ≥10 interests (L1202).
8. The topic cannot be stated in a few words and contains action nouns (L1238–1256).
9. Topic → claim restatement is non-trivial (L1257–1290).
10. Vet references behind any Wikipedia content (L1213–1215).

**S2 Questions**
11. Generate questions across history, structure, categories, what-if, and source agreement/disagreement; record them without answering (L1318–1423).
12. Mine article endings and online discussions for open questions (L1424–1431).
13. Reject settled-fact, unsupportable and dead-end questions, with the caveat (L1434–1465).
14. Prefer how/why (L1441–1443).
15. Combine questions into a larger one (L1466–1480).

**S3 Significance**
16. Fill topic / indirect question / "in order to help my audience understand" (L1507–1605).
17. "Nothing. I just want to know" is not acceptable at finish (L1498–1500).
18. Explain to knowledgeable and naive listeners to expose gaps (L1588–1595).
19. Re-check the 3 steps before finishing (L1584–1586).

**S4 Problem**
20. State the condition and the cost/consequence (L1762–1777; L1861–1873).
21. Frame the cost from the audience's view; ladder the So-what until "What do we do about it?" (L1779–1802).
22. Q1 helps answer Q2, and Q2 is more important (L1895–1898).
23. Classify pure or applied by step 3 (L1944–1982).
24. Applied: pass the (a)/(b) plausibility test, else move the application to a 4th step and to the conclusion (L2005–2055).
25. Verify the gap is real, not a misreading (L2112–2116).
26. Show that those who think X misunderstand a larger issue (L2117–2120).
27. Ask "Will others think it should be solved?" (L2157–2159).
28. Review bias embedded in the problem framing (L1851–1860).
29. Ch. 9 problem questions 1–5 (L5741–5756).

**S5 Working hypothesis**
30. Hold a provisional claim plus alternatives, including rejected ones (L4185–4188).
31. Begin from a problem, not a claim you "know" you can prove (L4264–4266).
32. State the elevator pitch: question, best guess, evidence type (L1121–1124).

**S6 Sources**
33. Classify each source primary/secondary/tertiary relative to the project (L2376–2389).
34. Do not cite tertiary sources in a scholarly argument (L2349–2353).
35. Include challenging sources (L2321–2322).
36. Start from reference works and bibliographies (L2455–2471).
37. Use flexible search terms, keyword plus subject headings, and browsing (L2499–2528).
38. Date-narrow when overwhelmed (15 → 10 years) (L2536–2538).
39. Follow backward trails, noting retrospective bias (L2588–2601).
40. Do forward citation indexing, with the "cited because bad" caveat (L2604–2615).
41. Treat internet and AI findings as provisional; verify facts in reliable sources (L2648–2652).
42. Use online non-journal material as primary, not secondary (L2664–2670).
43. Avoid unauthorized copies (L2673–2679).
44. Relevance skim protocol for books, articles and sites (L2690–2726).
45. Reliability signs 1–11 (L2746–2844).
46. Indicators don't guarantee reliability; keep reading critically (L2846–2850).
47. Look beyond predictable source types (L2854–2869).
48. If someone got there first: reformulate, or differentiate and acknowledge (L2878–2888).
49. Prepare interviews thoroughly (L2898–2905).
50. Consult experts on open questions and the provisional thesis (L2909–2912).
51. Obtain ethics review for human subjects (L2924–2936).

**S7 Engaging sources**
52. Record complete bibliographic data first, per source-type field list (L3023–3074).
53. If accessed online, record both the original and the access data (L3073–3074).
54. Read generously, then critically (L3113–3121).
55. Comprehension test: can you summarize it? (L3123–3125)
56. Don't accept authority uncritically; check obsolescence (L3126–3135).
57. Check the accuracy of everything important (L3152–3153).
58. Read for a problem via creative agreement (3 moves) and creative disagreement (5 types; causation ≠ correlation) (L3172–3313).
59. Read for arguments to respond to (L3324–3340).
60. Borrow reasoning models, not ideas (L3341–3350).
61. Check primary sources for data; use statistics only if their collection can be judged (L3374–3380).
62. Cite both primary and secondary for secondhand data (L3384–3390).
63. When using a source's claim as support, report its reasoning and evidence (L3404–3409).
64. Separate notes per topic (L3441–3442).
65. Guard against transcription errors (L3443–3450).
66. Record claims plus data plus your responses (L3451–3457).
67. Mark quote / paraphrase / summary / own unambiguously (L3458–3465).
68. Notes system: organized, backed up (L3519–3538).
69. Summary isn't good evidence; paraphrase is weaker than quotation; quote for the 4 purposes (L3566–3589).
70. Never abbreviate a quotation intending to reconstruct it (L3590–3591).
71. Record the line of reasoning (L3604–3620).
72. Record the claim's role: main, concession, qualification (L3622–3657).
73. Record scope and confidence (L3659–3670).
74. Don't attribute an author's summary of others to the author (L3672–3677).
75. Note why sources agree or disagree; use ≥2 sources (L3679–3692).
76. Annotate, don't just highlight (L3710–3716).
77. Annotated bibliography entries: credibility, argument, relevance (L3757–3761).
78. Tag notes with ≥2 reusable keywords by implication (L3765–3776).
79. When muddled: restate the formula, explain to non-specialists (L3784–3796).

**S8 Argument overview**
80. Answer all 5 questions (L3950–3956).
81. Reasons are based on evidence, never the reverse (L4013–4020).
82. Storyboard: claim, reason pages, evidence or needed-evidence lists, warrant pages, acknowledgment pages (L4167–4173).
83. Abandon reasons or revise the claim when evidence is not found (L4190–4194).
84. Ethos check: background, "sufficient (neither thin nor excessive)" support, all sides, respectful style (L4200–4210).
85. Rehearse with an experienced researcher; seek audience-like readers (L3871–3881).
86. Try alternative methods and multiple support routes (L4285–4288).

**S9 Claim**
87. Kind of claim identified (L4328–4357).
88. Practical claims: feasibility, cost, no bigger problem, better than alternatives (L4373–4380).
89. No practical action tacked onto a conceptual claim in the introduction (L4380–4386).
90. Specific (L4397–4410).
91. Significance estimate: beliefs changed and how foundational (L4416–4420).
92. Although-clause acknowledgment (L4450–4484).
93. "Why should I care?" relative to your own prior beliefs (L4525–4531).
94. Limiting conditions the audience would plausibly think of (L4562).
95. Hedges balanced: neither absolute nor timid (L4618–4622).
96. Negation test for contestability (L4638–4659).
97. Ch. 9 claim questions 1–5 (L5767–5774).

**S10 Reasons and evidence**
98. Reasons in plausible order; read the reasons alone (L4684–4687).
99. Evidence the audience won't question; regress until reasonable stop (L4695–4730).
100. Evidence kind fits the claim and field (L4750–4775).
101. Provenance chain of reports is trustworthy; field-standard methods (L4807–4850).
102. Accurate, with double-checks (L4900–4915).
103. Flag questionable evidence (L4916–4919).
104. Appropriately precise; no fudge quantifiers; not over-precise (L4920–4943).
105. Sufficient (L4947–4956).
106. Representative, shown for samples (L4957–4963).
107. Anecdotal risk noted (L4964–4972).
108. No cherry-picking; show all evidence considered (L4973–4978).
109. Authoritative by community standard (L4982–5000).
110. Clear: an interpretive reason links quotations, charts and images (L5004–5054).
111. No fabricating "enhancements" (L5055–5059).
112. Assess and annotate reliability while gathering (L5061–5084).
113. Reasons enough, consistent, relevant (L5788–5790).
114. Pre-answer the evidence objection list (L5795–5810).

**S11 Warrants**
115. Can each reason→claim link be stated as When X, then Y, more general than the reason and claim? (L5220–5236)
116. Reasonable, else argued (L5275–5287).
117. Limited, with exceptions checked (L5289–5308).
118. Superior to competing warrants (L5311–5342).
119. Appropriate to the field (L5343–5365).
120. Covers the reason and the claim (L5368–5406).
121. State a warrant for outsiders, for novel principles, or for resisted claims (warrant first) (L5411–5441).
122. Imagine-the-warrant soundness test; revise the reason if there is no acceptable warrant (L5472–5527).
123. Prefer evidence over warrant-derived reasons; claims of fact need evidence (L5632–5675).
124. No faith-based warrants (L5625–5630).
125. Marginal unstated-warrant check in revision (L7075–7079).

**S12 Acknowledgment and response**
126. Address all 3 question types: problem, soundness, alternatives (L5707–5717).
127. Stress test like the most skeptical reader (L5815–5819).
128. Alternatives harvested from sources, including source–source disagreements (L5843–5852).
129. Persuasive-to-audience sources you reject are explained, not ignored (L5854–5856).
130. Goldilocks selection by the 5 priorities (L5864–5878).
131. Unfixable weakness: redefine or rebuild, else one of the three candid responses; never hide it (L5905–5921).
132. List unanswered questions candidly (L5943–5945).
133. Responses are explained sub-arguments (L5950–5978).
134. Calibrated acknowledgment and response vocabulary; no ad hominem (L5992–6147).
135. Alternative causes acknowledged (L6153–6163).
136. Counterexamples pre-empted, including statistical aberrance (L6164–6176).
137. Contested definitions argued, not dictionary-based (L6177–6200).

**S13 Planning and drafting**
138. Ready-to-plan six conditions (L6216–6227).
139. Field format if conventional (L6340–6343).
140. Working introduction with context (only sources extended, modified or corrected), problem with but/yet plus So what, and response or launching point (L6406–6520).
141. 4–5 consistent key terms (L6534–6544).
142. Background page; one page per section with its point (L6548–6557).
143. Reader-based order with signals (L6558–6608).
144. Section-distinguishing key terms, else cut (L6612–6619).
145. Evidence sources and reliability explained (L6628–6634).
146. Acknowledgments outlined (L6635–6641).
147. Warrant before claim (L6642–6667).
148. Section summaries for fact-heavy text (L6669–6673).
149. Working conclusion (L6676–6678).
150. No narrative-of-thinking (test phrases) (L6691–6708).
151. No patchwork (L6709–6722).
152. No assignment mapping (L6723–6759).
153. Start drafting early; placeholders allowed (L6778–6791).

**S14 Revising**
154. Top-down order (L6949–6959).
155. Frame visible: intro end, conclusion start, point (L6964–6974).
156. Point at the end of the introduction (L6975–6979).
157. Conclusion point consistent and more specific (L6976–6979).
158. Key terms in the point sentence (L6980–6983).
159. Each reason a section (L7043–7045).
160. Reason/evidence balance (L7046–7052).
161. Re-check data and quotes against notes (L7064–7065).
162. Relevance of quotes and data shown (L7066–7067).
163. No skipped sub-reasons (L7068–7070).
164. Hedges added (L7072–7074).
165. Conversation tone; objections addressed (L7080–7088).
166. Key terms in most paragraphs (L7097–7121).
167. Insertable headings (L7123–7128).
168. Transitional logic (L7129–7137).
169. Each section answers an argument question or serves context, else cut (L7138–7144).
170. Section point not buried (L7145–7152).
171. Distinct section terms, else merge (L7153–7160).
172. Paragraph point first or last; sensible length (L7170–7184).
173. Cool the draft; skim top-level; get a third-party summary (L7193–7203).
174. Abstract with problem, themes, point/launch; keywords in title and abstract (L7206–7214, L7286–7289).

**S15 Sources in text**
175. Choose summary, paraphrase or quotation per the rules (L7323–7341).
176. No stitching (L7345–7349).
177. Fair summary (L7358–7360).
178. Paraphrase by the explain-aloud method; finger test (L7376–7406).
179. Quotation form and insertion; ellipses and brackets without meaning change (L7410–7477).
180. Explain evidence in proportion to its length (L7506–7513).
181. Citations complete and findable (L7531–7538).
182. Range and depth of sources (L7546–7548).
183. Correct style down to the comma; verify auto-citations (L7585–7587, L7642–7644).
184. Avoid the 4 plagiarism patterns (L7655–7663).
185. Cite at insertion; each page separately (L7668–7682).
186. Mark striking phrases; use quotation marks freely (L7685–7723).
187. Cite person-associated, non-common-knowledge ideas and methods (L7733–7748).
188. Author-recognition test (L7758–7762).

**S16 Visuals**
189. Visual vs. verbal by data amount (L7902–7931).
190. Form chosen for the intended effect (L7973–7975).
191. Descriptive title or legend, no interpretation (L7999–8028).
192. In-graphic aids (L8029–8034).
193. Introductory interpretive sentence (L8046–8072).
194. Only relevant data (L8080–8081).
195. Minimal ink; no 3D or iconic bars; patterns or labels over color (L8083–8114).
196. Labels on axes, rows and lines (L8116–8127).
197. Tables ordered by emphasis, rounded, totals at the end (L8136–8141).
198. Bars arranged to match the message; stacked and pie rules (L8204–8257).
199. Line direction, ≤6 lines, dots, no gray-only (L8271–8283).
200. Test alternatives on a naive viewer (L8289–8293).
201. Ethics: no scale manipulation, no distorting image, no misleading complexity or simplicity, state the point (L8350–8356).

**S17 Introduction and conclusion**
202. Context + problem + response present (L8464–8470).
203. Stable context disrupted (L8531–8537).
204. Context appropriate to class/peers; sources limited to those modified (L8605–8611).
205. Condition plus consequence; costs to motivate, benefits to intensify (L8615–8648).
206. State the gap even in familiar fields (L8676–8678).
207. If in doubt, state consequences (L8690–8691).
208. So-what test on the condition sentence (L8727–8740).
209. Response at the end of the introduction, or a key-term launching point (not a bare topic) (L8774–8856).
210. Pace matched to the audience (L8893–8895).
211. No cliché openers (L8899–8921).
212. Conclusion: fuller point, new significance or application, further research (L8973–9022).
213. Title with key terms, two-part (L9024–9040).

**S18 Style**
214. First 6–7 words reach a concrete-character subject (L9285–9292).
215. Specific action verbs (L9294–9296).
216. Unneeded nominalizations converted (L9300–9307, L9752–9755).
217. Old before new (L9495–9517).
218. Passive/active chosen for flow and for who can claim the action (L9527–9605).
219. Last 5–6 words carry new, complex or stressed content (L9703–9706, L9762–9782).
220. Read aloud (L9733–9734).
221. Editorial polish: grammar, spelling, numbers, names (L9718–9723).

**S19 Presentation**
222. Reading pace and eye contact (L9830–9837).
223. Explicit organization; simpler sentences (L9838–9842).
224. Accessibility items (L9853–9868).
225. Notes structured by reasons; most important first (L9888–9910).
226. Preliminary talk: problem/claim, reasons, evidence preview, uncertainty-focused questions (L9941–9988).
227. Final talk: one best piece of evidence per reason; scope narrowed (L9997–10026).
228. Intro ≤3 min with the 5 parts; claim up front (L10039–10067).
229. Rehearsed and timed conclusion; no ad lib (L10071–10087).
230. Q&A preparation; honesty about unknown sources (L10091–10101).
231. Selective handouts (L10110–10113).

**S20 Ethics (applies at every gate)**
232. The 7 shalt-nots (L10196–10204).
233. Higher standard when asking for trust (L10238–10240).
234. Responsibility for changing beliefs (L10241–10244).
235. Strongest objections acknowledged (L10245–10249).
236. Credit others' data fully, especially with power asymmetry (L10261–10274).
237. Preserve contrary evidence; claims only as strong as deserved (L10295–10302).
238. Obligations to facilitators, subjects, and the affected (L10309–10322).
239. Human-subjects review (L10335–10339).
240. Field ethics codes; no helicopter research or ethics dumping (L10340–10354).
241. Argue that the project is ethical via the 4 ethics warrants (L10359–10384).
242. Generative-AI use: confirm all AI outputs independently; no falsification, substitution or passing-off; disclose like a source (L2964–2989).
