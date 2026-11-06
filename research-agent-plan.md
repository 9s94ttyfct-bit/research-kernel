# 全流程、高准确、高可靠的自主 Research Agent：分析、架构与路径选择

> 日期：2026-11-05
> 依据：
> - 《The Craft of Research》第 5 版正文（Preface 至第 18 章及全部 Quick Tip）由子 agent 通读蒸馏，主线抽查核对；附录（书目资源指南）只略读，本报告不依赖它。蒸馏稿见 `docs/craft-research-process.md`，约 1.8 万词，引用标注原文行号，含 242 条检查项。
> - 2026 年 11 月前的公开资料：项目仓库、论文、官方文档、安全报告。链接见文末。
> - 标注 `[推断]` 的内容是我的判断，没有直接来源。

---

## 0. 结论

1. **Hermes Agent 和 OpenClaw 都不该作为 research agent 的核心来 fork。** 两者都是"个人通用 agent 运行时"，主要能力是消息网关、工具调用、技能、记忆和定时任务。它们都没有研究专用的状态模型：问题、假设、来源、笔记、证据链、论证图、claim 账本、质量门都不存在。一个 research agent 准不准、可不可靠，几乎全部取决于这部分，而这部分无论选哪条路都得自己写。
2. **也不该从零写 agent loop。** 工具调用、沙箱、浏览器、MCP、上下文压缩都已经是现成的商品，自己重写只会多出维护负担，不会提高可靠性。
3. **推荐路径（C）：自建 Research Kernel，其余尽量复用。** Kernel 用 Python，以 MCP 和 CLI 暴露，包括：
   - 类型化研究状态
   - 持久化阶段状态机，允许回边
   - 以代码为主的验证器
   - 评测基准

   Kernel 内部复用 PaperQA2、OpenAlex、Crossref 等成熟组件。驱动层不绑定：Claude Agent SDK、Codex、omp、Hermes 都可以。Hermes 可以后期作为外壳接入，负责定时、消息推送和远程触发。OpenClaw 只借鉴 Lobster 的设计（类型化流水线、审批门、恢复令牌），不作为运行时。
4. **行业现状和瓶颈：问题不在"会不会做"，而在"能不能被验证"。** 2026 年的综述统计了 24 个可运行的 AI Scientist 系统：83% 开源了代码，但只有 38% 提供种子或执行轨迹，也只有 38% 做了新颖性验证；没有一个系统具备经过外部验证的 in-loop oracle（[Ding et al. 2026](https://arxiv.org/abs/2608.05179)）。因此本方案的投入重点是验证与溯源，而不是 prompt 编排。

---

## 1. 书中的 research 全流程

### 1.1 基本立场

- **研究的定义。** 研究是从研究者的问题出发、以把答案分享给关心它的读者为终点的定向探究（L606–609）。
- **三段划分。** 第 5 版把过程拆成 research（Part I–II）、argument（Part III）、communication（Part IV）。
- **知识观。** 知识是"共同体通过论证追求更好的理解"，"truth is always partial"（L451–478）。
- **对 agent 的含义 `[推断]`。** 产出不是"找到的事实"，而是一个能经受目标读者质询的论证。所以 agent 的终极检验方式应该是读者质询，而不是检索召回率。

### 1.2 阶段模型（S0–S20）

| # | 阶段 | 书中核心方法 | 关键自检 | Agent 产物与质量门 `[推断]`：书中没有，是我的设计 |
|---|---|---|---|---|
| S0 | 读者与角色 | 三种作者角色（新信息 / 实际方案 / 理解）对应三种读者角色；读者清单分别在开始、规划、修改时各做一次 | 读者是谁、期待什么、知道多少、会怎么反对 | `audience.yaml`；字段全填；在 S13、S14 复核 |
| S1 | 兴趣 → 话题 | 列出 10 个以上兴趣；用动作名词聚焦；把话题改写成句子测试 | 静态话题会改写出空洞的 claim | 候选话题 ≥10；改写出的 claim 不能是同义反复 |
| S2 | 话题 → 问题 | 先列问题、暂不回答；问题族包括历史、结构、分类、假设、来源分歧；优先 how/why | 排除三类：查得到的事实、没有数据可答的、和更大议题无关的 | `questions.jsonl`；先尝试检索，能直接查到答案的问题被拒 |
| S3 | So what 三步式 | "I am studying X because I want to find out Y in order to help my audience understand Z" | 第三步在定稿前必须补齐 | 三槽都要填；定稿前第三步缺失就阻断 |
| S4 | 问题 → 研究问题（problem） | 实践问题 = 条件 + 代价；概念问题 = 不知道 Q1，因此无法理解更重要的 Q2；区分纯研究和应用研究 | 先确认"空白"真实存在，而不是误读（"无数论文反驳了一个没人提出过的观点"） | `problem.json`；后果链 ≥2 层；`gap_evidence` 要引证 |
| S5 | 工作假设 | 用暂定 claim 聚焦搜索，同时记录被否掉的备选；不要从"我知道能证明的"出发 | 是否记录了反证条件 | `hypotheses.json`；≥2 个对立假设，每个都写明证伪条件 |
| S6 | 找来源、评来源 | 一手 / 二手 / 三手来源（相对项目而定）；前向和后向引文追踪；11 项可靠性信号；主动找反对自己的来源 | 相关性速读协议；可靠性筛查；三手来源不能作证据 | `sources.jsonl`；每条主要理由至少 1 个反方来源；AI 推荐的来源必须解析到 DOI 或目录记录 |
| S7 | 读来源、做笔记 | 先抄全书目；先宽容地读、再批判地读；分开记录引文、转述和自己的想法；记录论证链、该论点在原文中的角色、限定程度 | 摘要理解测试；"上下文五条"；两源规则 | `notes` 表；引文与原文逐字比对；hedge 强度不得加强 |
| S8 | 论证总装 | 五个问题对应五要素：claim / reason / evidence / warrant / acknowledgment-response；用 storyboard 组织 | 每条理由都有证据，或标明"证据待补" | `argument_graph.json`；证据缺口触发回到 S6 |
| S9 | Claim | 区分事实、定义、因果、评价、政策；要具体、有意义、可争议、有限定；用 although 从句加厚 | 否定测试；hedge 审计；实践类 claim 需要 4 个子论证 | `claim.json`；全称量词必须有证据覆盖，否则删掉 |
| S10 | 理由与证据 | 证据和"证据的报告"不同；判据：准确、精度恰当、充分且有代表性、权威、可理解、相关 | 樱桃采摘意味着"不诚实"；数字必须复核 | `evidence_reports[]`；完整溯源链；数值重抽取；反证搜索要留日志 |
| S11 | Warrant | 写成 "When X, then Y"；五项检验：合理、有限定、优于竞争性 warrant、符合领域、覆盖理由和 claim | 事实性 claim 不能只靠推理支撑 | 每条论证边带 warrant 和检验结果；信仰型 warrant 直接阻断 |
| S12 | 承认与回应 | 三类质疑：问题本身、论证是否站得住、替代解释；三种可预见的分歧：其他原因、反例、定义 | 不能隐藏无法修复的弱点（"that's dishonest"） | 独立红队生成 `objections.jsonl`；隐藏已知弱点直接判失败 |
| S13 | 规划与起草 | 起草本身就是发现；避免三种坏结构：照搬作业、照搬来源、按发现顺序 | 草稿可能反过来改写 S3、S4 | 大纲、草稿；出现意外 claim 时回到 S4 |
| S14 | 修改与组织 | 自上而下：框架 → 论证 → 组织 → 段落；冷却后重读 | 页边写出最重要的隐含 warrant | 修订 diff；盲读者复述测试 |
| S15 | 引用与整合 | 公平地摘要、转述、引用；"手指测试"检查转述是否过近；插入引文时立即标注；无意剽窃也是剽窃 | 原作者能否认出这是自己的想法 | `citations.json` 逐句溯源；转述相似度设上限；DOI 必须可解析 |
| S16 | 图表证据 | 选择合适的图表；伦理地表达数据 | 图表不能误导 | 图表由代码从数据生成，禁止手填数字 |
| S17 | 引言与结论 | 引言 = 语境 → 问题 → 回应；结论 = 要点 + 新意义 + 后续研究 | 标题包含关键词 | 结构检查 |
| S18 | 文风 | 角色作主语，动作作谓语；旧信息在前；复杂内容放后 | 可诊断的风格问题 | 风格 lint |
| S19 | 汇报 | 初步汇报用来检验思路 | 准备问答 | 可选 |
| S20 | 伦理（贯穿全程） | 对自己、对读者与共同体、对社会的责任；保留并承认反证 | — | 每个阶段都受约束 |

### 1.3 非线性与回边

书中至少 10 处明确说过线性顺序只是教学上的简化。例如 Preface L350–352："Experienced researchers loop back and forth"。

**具体回边：**

| 回边 | 触发条件 |
|---|---|
| S8/S10 → S6 | 证据不足 |
| S10 → S9/S5 | 某条理由找不到证据，于是放弃这条理由或修改 claim（L4184–4194） |
| S12 → S4 | 发现无法修复的弱点，需要重新定义问题（L5906） |
| S13 → S3/S4 | 草稿里冒出意外的 claim（L2144–2149） |

**反向警告：** 书中同样提醒不要无限循环。"再读一篇"和无休止地记笔记都是拖延（L5079–5081、L6788–6791）。

**对 agent 的含义 `[推断]`：** 编排器应该是带回边、带预算的有向状态机，不是固定的流水线。每条回边都要记录触发原因，并设循环上限。

### 1.4 书对生成式 AI 的立场

第 3 章的 Quick Tip（L2938–2989）给出五条原则：

- **Explore**：多试用，了解它能做什么。
- **Communicate**：遵循所在领域和机构的规范。
- **Be honest**：不用来伪造，不冒充自己的成果。
- **Be critical**：书中点名了三类失败——"false facts, misattribute quotations, even make up references"——要求"confirm your results using other methods"。
- **Be transparent**：像引用来源一样披露 AI 的使用。

**对一个本身就是 LLM 的 agent，这些原则应该变成硬约束 `[推断]`：**

1. 模型自己的记忆（参数知识）只算未经核验的三手来源，**永远不能作为证据被引用**。
2. 每个事实、引文、参考文献都必须经过检索确认：DOI 能解析，引文能逐字匹配。
3. 交付物里必须有 AI 使用披露段。
4. 用户提供的本地语境，优先级高于模型先验。

---

## 2. 外部资料：现状、项目与证据

### 2.1 通用 agent 运行时：Hermes Agent 与 OpenClaw

| 维度 | Hermes Agent（Nous Research） | OpenClaw |
|---|---|---|
| 规模 | 约 26.1 万 stars，MIT，Python；发布频繁，2026-10-23 的稳定版是 v0.22.5 | 约 39.4 万 stars，MIT，TypeScript/Node；OpenClaw 基金会治理 |
| 核心形态 | `AIAgent` 循环；70 多个工具；SQLite FTS5 会话库；网关；cron；ACP；子 agent 委派；插件 | 单一 Gateway 守护进程，统管各消息通道；以 WebSocket 协议连接客户端和设备节点 |
| 学习机制 | 两个有字数上限的记忆文件（MEMORY.md 2,200 字符，USER.md 1,375 字符）；agent 自己写和 patch skill；对话后台复盘（默认开启，要求"ACTIVE"，即多数会话应至少产出一个 skill 更新）；curator 定期清理 | 记忆有来源追踪、信任门控和可归因的清除记录 |
| 与研究相关的现成能力 | 已有 research 技能：`grounded-citations` 用台账脚本管理 `url → [n]` 映射，引文必须在抓取的原文中逐字出现，`verify --evidence` 会让无证据的草稿失败；还有 `arxiv`；可选的 `research-paper-writing`（含"不要编造引用"规则、实验服务于 claim 的原则）和 `llm-wiki`。以上来自仓库 SKILL.md 原文，未实测效果 | 内置技能以个人效率工具为主（笔记、日历、GitHub 等），没有专门的研究技能。Lobster 是**需单独安装的可选插件**，提供类型化流水线、审批门和恢复令牌，属于通用工作流运行时，不是研究规划或可靠性系统 |
| 可靠性 / 安全短板 | 记忆和 skill 写入**默认不需要审批**；内置记忆没有来源会话谱系，也没有删除墓碑；插件在进程内运行、不沙箱；同一适配器内的调用者被视为同等信任；有第三方 CVE 记录 | 一篇论文对历史上提交的 470 条安全通告做了分类学研究（这是累计通告数，不代表当前仍未修复的漏洞数），其中 3 条中/高危可组合成未认证 RCE 链，exec allowlist 可被绕过（[Suwansathit et al.](https://arxiv.org/abs/2603.27517)）；ClawHub 供应链问题：Koi Security 审计 2,857 个 skill 发现 341 个恶意，后续在 1 万多个中发现 824 个，IBM X-Force 统计累计超过 1,100 个，Unit 42 发现了专门绕过扫描的样本（均为 [CSA 报告](https://labs.cloudsecurityalliance.org/research/csa-research-note-openclaw-clawhub-supply-chain-risk-2026070/)转述）；沙箱默认关闭（OpenClaw 官方对比文档自述） |
| 对 research agent 的价值 | 中：可以作为外壳和技能载体；`grounded-citations` 的台账思路值得直接借鉴 | 低到中：Lobster 的设计值得借鉴，其余能力大多用不上，攻击面却很大 |

**结论：** 两者擅长的都是"随时可唤起的个人助手"，而 research agent 需要的是"长时、可审计、可复现的知识生产流水线"，两者的设计重心几乎正交。OpenClaw 官方自己的对比文档也指出，两者的插件都是"in-process and unsandboxed"（[OpenClaw docs](https://github.com/openclaw/openclaw/blob/main/docs/start/why-openclaw/openclaw-and-hermes-agent.md)）。

### 2.2 Deep Research 类（文献、网页综合报告）

| 项目 | 要点 | 可复用的部分 |
|---|---|---|
| [GPT Researcher](https://github.com/assafelovic/gpt-researcher)（约 3.05 万 stars，Apache-2.0） | planner 加并行执行器，生成带引用的报告 | 检索并行化；报告模板 |
| [STORM / Co-STORM](https://github.com/stanford-oval/storm)（约 3.18 万 stars，MIT；2025-09 后不活跃） | 多视角提问，先出大纲再写维基式长文 | "多视角提问"对应书中的 S2 和 S12 |
| [open_deep_research](https://github.com/langchain-ai/open_deep_research)（约 1.27 万 stars，MIT） | LangGraph 实现：澄清、研究监督者、子研究者、写作 | 可以作为编排参考实现 |
| [DeerFlow 2.0](https://github.com/bytedance/deer-flow)（约 8.5 万 stars，MIT，基于 LangGraph） | 长时 SuperAgent harness：沙箱、记忆、技能、子 agent、网关、项目文档架、检查点 | 若想要现成的研究型 harness，它在开源方案里最完整 |
| [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)、[MiroThinker](https://github.com/MiroMindAI/MiroThinker) | 专门为深度搜索训练的开源模型和框架；MiroThinker-1.7 在 BrowseComp 上得分 74.0 | 检索子任务可以考虑用本地或开源模型 |
| [dzhng/deep-research](https://github.com/dzhng/deep-research) | 极简的迭代深挖实现 | 适合教学或原型 |

### 2.3 科学文献 agent（准确性最高的一类）

- **[PaperQA2](https://github.com/Future-House/paper-qa)**（FutureHouse，Apache-2.0，仍在维护）
  - 在检索、摘要、矛盾检测三类真实任务上，达到或超过学科专家，而且对照组专家可以自由使用互联网。
  - 生成的 WikiCrow 式综述比人写的维基百科条目更准确。
  - 每篇论文平均找出 2.34 处矛盾，其中 70% 经专家确认（[arXiv 2409.13740](https://arxiv.org/abs/2409.13740)）。
  - **它是 S6–S7 文献 RAG 层最值得直接复用的组件。**
- **[OpenScholar](https://doi.org/10.1038/s41586-025-10072-4)**（Ai2 / UW，Nature 2026-02）
  - 研究中发现 GPT-4o 编造了 78–90% 的研究引用。
  - OpenScholar 基于 4,500 万篇论文做检索增强，引用准确性与人类专家相当。
  - 专家在 51% 的情况下更偏好它的答案；把它的流程接到 GPT-4o 上后，这个比例升到 70%。
  - 后续工作是 DR Tulu。
  - **启示：把检索限定在学术语料，比通用网页搜索更可靠。**

### 2.4 AI Scientist 类（含实验）

| 系统 | 要点 | 已知问题 |
|---|---|---|
| [The AI Scientist](https://www.nature.com/articles/s41586-026-10265-5)（Sakana，Nature 2026-03） | 全流程：想法、文献筛新、树搜索实验、写作、自动审稿；一篇论文通过了 ICLR workshop 一审（该 workshop 接收率 70%）；论文质量随基座模型的发布时间显著提升（评分来自其自动审稿器） | 针对 **2024 年 v1 版本** 的独立评测（[Beel et al., 2025-02](https://arxiv.org/abs/2502.14297)）发现：新颖性判断差；42% 的实验因代码错误失败；引用少且陈旧；存在幻觉数值。这些比例不能直接套用到 Nature 版和 v2。许可证是自定义的 |
| [Kosmos](https://arxiv.org/abs/2511.02824)（Edison） | 用**结构化世界模型**让数据分析 agent 和文献 agent 共享状态；单次运行 12 小时、200 次 rollout、约 4.2 万行代码、读约 1,500 篇论文；每句话都引用代码或一手文献 | 独立科学家评定 79.4% 的陈述准确，即约 1/5 不准确 |
| [Co-Scientist](https://arxiv.org/abs/2502.18864)（Google） | 生成、反思、锦标赛 Elo 排名、进化、元评审；部分假设经湿实验验证 | 主要是提出假设，不是完整研究 |
| [autoresearch](https://github.com/karpathy/autoresearch)（Karpathy，2026-03） | 固定 5 分钟预算、单一指标（val_bpb）、单文件可改、保留或丢弃；人负责迭代 `program.md` | 范围极窄，但"单一客观指标 + 固定预算 + 可 diff"的思路是实验模块的黄金模式 |
| Agent Laboratory、AI-Researcher、RD-Agent | 端到端或面向 R&D 的流水线 | 参考 |

### 2.5 失败模式的硬证据

| 证据 | 数字 / 发现 | 对应书中的原则 |
|---|---|---|
| [DeepTRACE](https://arxiv.org/abs/2509.04499)（ICLR 2026） | 主流 deep research 系统的引用准确率只有 40–80%；在争议问题上高度片面；大量陈述得不到自己所列来源的支持 | S10 证据判据、S12 承认反方 |
| [ResearchRubrics](https://arxiv.org/abs/2511.07685)（ICLR 2026） | Gemini DR 和 OpenAI DR 的平均 rubric 合规率都低于 68%，主要原因是漏掉隐含语境、对检索结果推理不足 | S0 读者模型、S4 问题界定 |
| [DeepFact](https://aclanthology.org/2026.acl-long.1586/)（ACL 2026） | 博士级专家一次性判断报告 claim 的准确率只有 60.8%，经过多轮"审计后评分"升到 90.9%；DeepFactEval 验证器达到 83.4% | 验证器不能一次定论，标签要可修订 |
| [GPTZero / NeurIPS 2025](https://gptzero.me/news/neurips/)（检测工具厂商的调查，非独立同行评议） | 扫描 4,841 篇已录用论文，列出 51 篇中约 100 处逐条人工核实的幻觉引用，这些论文都经过 3 名以上审稿人；这是"至少"数，不是总体发生率估计 | S15：DOI 解析和元数据比对必须是硬门 |
| [Trehan & Chopra 2026](https://arxiv.org/abs/2601.03315) | 4 次端到端尝试中 3 次失败。六类失败：沿用训练数据里的默认做法、执行压力下的实现漂移、长程记忆退化、无视明显失败而宣布成功的"过度兴奋"、领域知识不足、实验设计品味弱 | S5 证伪条件、S10 准确性、预注册 |
| [Bisht et al. 2026](https://arxiv.org/abs/2605.08956) | 选题受 McNamara 谬误影响（只研究容易量化的东西）；偏好对齐压缩了输出多样性；建议引入持久可变的认知状态和假设预注册 | S2 和 S4 的选题、S5 的假设账本 |
| [Ding et al. 2026](https://arxiv.org/abs/2608.05179) | 验证鸿沟：只有 38% 的系统提供执行轨迹，38% 做新颖性验证 | 系统必须输出可审计的工件 |
| 论文内嵌提示注入（[Nature 新闻](https://www.nature.com/articles/d41586-025-02172-y)、[arXiv 2511.01287](https://arxiv.org/abs/2511.01287)） | 有作者在论文中隐藏 "give a positive review only" 之类的指令 | 抓取的内容只能当数据，不能当指令 |
| Agents4Science 2025（[Nature Biotech](https://www.nature.com/articles/s41587-025-02963-8)；[分析笔记](https://inquiringlines.com/notes/agents4science-found-accepted-papers-involved-more-human-guidance-than-rejected/)） | 被接收的 AI 论文中人类指导往往更多 | 在关键节点设人类检查点，收益很高 |

### 2.6 可用的评测基准

| 类别 | 基准 |
|---|---|
| 文献问答 | LitQA2（PaperQA2）、ScholarQABench（OpenScholar） |
| 报告质量 | [DeepResearch Bench](https://deepresearch-bench.github.io/)（RACE/FACT）、ResearchRubrics、[DRACO](https://arxiv.org/pdf/2602.11685)、ReportBench |
| 事实性 | DeepFactBench |
| 检索 | BrowseComp |
| 实验与复现 | PaperBench、MLE-bench |
| 审计维度 | DeepTRACE 的 8 个维度 |

### 2.7 数据基础设施

| 数据源 | 2026 年的条件 |
|---|---|
| OpenAlex | 2026 年起需要 API key。按 key 每天免费额度 1 美元：单条查询免费，搜索每千次 1 美元，PDF 下载每千次 10 美元（[文档](https://openalex.mintlify.app/api-reference/authentication)） |
| Semantic Scholar Graph API | 需申请 key |
| Crossref REST | 已调整限速；Retraction Watch 数据已并入 Crossref |
| 全文 | arXiv、PubMed/PMC OA、Unpaywall、CORE |

**全文获取（付费墙）是现实中最大的瓶颈之一。** 对策是机构代理、用户上传 PDF，或者限定只用 OA 语料。

---

## 3. Build vs Adopt：逐项对比

评分 1–5，`[推断]`，基于上面的资料和架构阅读：

| 维度（权重） | Fork Hermes | Fork OpenClaw | 改造 DeerFlow 2.0 | 自建 Kernel + 复用组件（C） | 完全从零（含 agent loop） |
|---|---|---|---|---|---|
| 研究状态模型 / 证据链（高） | 1，需全部自建 | 1，需全部自建 | 2，有项目文档架和检查点 | 5，按 S0–S20 原生设计 | 5 |
| 验证器与质量门（高） | 2，有 `grounded-citations` 雏形 | 1 | 2 | 5 | 5 |
| 安全与供应链（高） | 2，写入默认无审批，插件不沙箱 | 1，已有 470 条通告和 ClawHub 恶意 skill | 3，有沙箱 | 4，攻击面最小 | 4 |
| 上游变动成本（中） | 1，每周多次发布，fork 难以 rebase | 2 | 3 | 4，只依赖稳定协议（MCP） | 5 |
| 开发启动速度（中） | 5 | 4 | 4 | 3 | 1 |
| 长时运行 / 定时 / 消息（低到中） | 5 | 5 | 4 | 2，可以借 Hermes 补齐 | 1 |
| 可评测性与复现（高） | 2 | 2 | 3 | 5 | 5 |
| 维护负担（中） | 2 | 2 | 3 | 4 | 1 |

**关键判断：**

- **可靠性来自研究层的数据结构和验证器，与选哪个运行时无关。** 运行时之间的差别只影响"多快能跑起来"和"攻击面多大"。
- **Fork 一个快速迭代的大型通用 agent 代价很高。** 研究需求和上游路线不一致，结果要么长期背着 rebase 负担，要么事实上变成一个过时的分支。
- **因此最优解是"核心自建、边缘复用、接口标准化"**：核心通过 MCP 和 CLI 暴露，任何 harness 都能驱动；Hermes 或 OpenClaw 只当可替换的外壳。

---

## 4. 推荐目标架构：Research Kernel

```
┌───────────────────────────────────────────────────────────────────────┐
│ L0 Harness（可替换）：Claude Agent SDK / Codex / omp / Hermes（cron+消息）│
└───────────────▲───────────────────────────────────────────────────────┘
                │ MCP tools + CLI（research.* 命名空间）
┌───────────────┴───────────────────────────────────────────────────────┐
│ L1 Orchestrator：持久化阶段状态机 S0–S20 + 回边 + 预算 + 检查点/恢复      │
│    （SQLite/Postgres 事件溯源；可选 LangGraph/Temporal）                  │
├───────────────────────────────────────────────────────────────────────┤
│ L2 Research State Store（类型化、版本化、内容寻址）                        │
│    audience · project_sentence · problem · hypotheses · sources ·       │
│    snapshots(sha256) · notes · evidence_reports · argument_graph ·      │
│    objections · claim_ledger · drafts · disclosures · run_trace         │
├───────────────────────────────────────────────────────────────────────┤
│ L3 Worker agents（最小权限，单职责）                                     │
│    Planner · Scout(检索) · Reader(笔记) · Analyst/Experimenter ·         │
│    Arguer(论证图) · Skeptic(红队) · SimReader(模拟读者) · Writer ·        │
│    Editor · Auditor(只读审计)                                           │
├───────────────────────────────────────────────────────────────────────┤
│ L4 Verifiers（代码优先，LLM 作为第二道）                                  │
│    ref-resolver · quote-exact · paraphrase-overlap · hedge-monotonic ·  │
│    claim-support(NLI 多模型) · numeric-reextract · retraction-check ·   │
│    figure-from-code · cherry-pick-audit · warrant-tests · novelty-check │
├───────────────────────────────────────────────────────────────────────┤
│ L5 Tools/Data：OpenAlex · Semantic Scholar · Crossref(+RW) · arXiv ·     │
│    PubMed/PMC · Unpaywall · CORE · Web 搜索 · PDF 解析(GROBID/docling) · │
│    PaperQA2 · 代码沙箱（容器/VM，无凭据）                                 │
├───────────────────────────────────────────────────────────────────────┤
│ L6 Eval & Observability：轨迹、成本、golden set、基准、回归门              │
└───────────────────────────────────────────────────────────────────────┘
```

### 4.1 核心数据结构：Claim 账本（可靠性的骨架）

最终报告里的每一句陈述都必须能沿下面这条链回溯：

```
sentence → claim_id → evidence_report[]（附 S10 的六项判据评分）
                   → note_id（类型为 quote / paraphrase / summary，带定位）
                   → snapshot_sha256（抓取时冻结的原文）
                   → source_id（DOI 或目录记录已解析，撤稿状态已检查）
```

**规则：**

- **模型不能凭记忆产出 DOI、URL 或页码。** 所有标识符都由检索工具返回，并进入台账。这一点借鉴自 Hermes `grounded-citations` 的设计。
- **无法回溯的句子只有三种结局：** 删除；降级为明确标注的作者推断（`[inference]`，对应书中的 own thoughts）；或者阻断交付。
- **hedge 强度只能减弱，不能增强。** 原文说 "suggests"，下游就不能写成 "shows"（书 L3659–3670）。用模态词强度的 diff 自动检查。

### 4.2 质量门（Gate）与阶段的映射

| 门 | 位置 | 实现 | 失败时的动作 |
|---|---|---|---|
| G-Audience | S0、S13、S14 | `audience.yaml` schema 校验，加 SimReader 提问 | 回到 S0 |
| G-Problem | S4 | 后果链 ≥2 层；`gap_evidence` 有引证；先做一次"是否已有人解决"的检索（书中 "When Someone Gets There First"） | 回到 S2，或重新表述问题 |
| G-Hypothesis | S5 | ≥2 个对立假设，每个都写明证伪条件；检索计划中要包含反证查询 | 阻断进入 S6 |
| G-Source | S6 | 11 项可靠性向量；三手来源不能作证据；每条理由 ≥1 个反方来源；前向和后向引文扩展都要做 | 补充检索 |
| G-Note | S7 | 引文逐字匹配快照；转述的 n-gram 重叠不超过阈值；`role_in_source` 必填 | 重读原文 |
| G-Evidence | S10 | 六项判据；数值独立重抽取；有代表性声明；反证搜索有日志 | 回到 S6，或修改理由或 claim |
| G-Warrant | S11 | 对抗模型尝试找出竞争性 warrant 或不覆盖的实例；事实性 claim 必须有直接证据 | 修改理由 |
| G-Objection | S12 | 红队按三类质疑和三种可预见分歧生成反对意见；已知弱点不得隐藏；每个因果 claim 都要列出替代原因 | 加入回应子论证，或回到 S4 |
| G-Citation | S15 | DOI、arXiv、PMID 都可解析，元数据（作者、年份、标题）与引用一致；撤稿检查；逐句溯源覆盖率 100% | 阻断交付 |
| G-Figure | S16 | 图表中的每个数字都能追溯到生成它的代码和数据哈希 | 阻断 |
| G-Disclosure | 交付 | AI 使用披露、局限性、开放问题都已列出 | 阻断 |

### 4.3 抗"模型自欺"的机制（对应 Trehan & Chopra 的六类失败）

- **预注册**：在 S5、S10 之前，把假设、成功判据和分析计划写入账本并做哈希。实验结束后，只能和预注册版本比较；偏离必须显式记录。这条用来对付"过度兴奋"和事后编故事。
- **生成者和验证者分离**：不同角色用不同 prompt、不同上下文；关键验证尽量用不同厂商的模型投票。分歧不由模型裁决，而是升级给人，或者在报告里标记。
- **校准与弃权**：DeepFact 显示，即使是专家，一次性标注的准确率也只有 60.8%，所以验证器的输出要带置信度。低置信或证据不足的 claim 一律弃权，或标为"未证实"，不能硬下结论。评测集标签采用"审计后评分"，可以修订：验证器与标签冲突时，提交证据由人裁决。
- **数值只来自代码**：报告中的任何数字都必须来自运行日志或数据文件，由代码重新抽取，不接受模型转述。这条对付幻觉数值。
- **反"沿用默认"**：S8 的 QT "Falling Back on What You Know" 要求列出至少两种可选方法或论证路径，并说明选择理由。
- **世界模型而非对话记忆**：长时任务的共享状态放在 L2 的结构化存储里（Kosmos 的做法），不依赖上下文压缩，以避免记忆退化。
- **预算与回边上限**：防止"再读一篇"式的无限循环，也防止实现漂移。

### 4.4 安全设计（吸取 OpenClaw 和 ClawHub 的教训）

- **抓取内容只当数据。** Reader、Scout 没有写权限，也不能执行代码；去除 PDF 和网页中的隐藏文本，例如白字、零宽字符、HTML 注释里的指令。
- **不从第三方市场安装 skill 或插件。** 所有 skill 都纳入版本控制，并经过评审。
- **代码执行放在无凭据的容器或 VM 里**，默认禁网，需要时只放行白名单域名。
- **自我改进必须经过评测。** 对 skill、prompt 的修改只有在 golden set 上不退化才能合入。可以参考 Hermes 的 `hermes-agent-self-evolution`（DSPy + GEPA）思路，但写入必须审批，这与 Hermes 默认开放写入的做法相反。

### 4.5 自主等级与人类检查点

| 等级 | 人类介入点 | 适用场景 |
|---|---|---|
| L2 协作 | S4 问题定义、S9 最终 claim、交付前 | 默认，推荐 |
| L3 有监督自治 | 只在交付前审阅；中途出现门失败时通知 | 文献综述、内部报告 |
| L4 全自治 | 无（但仍有全部自动门；失败的门写入报告的局限性部分） | 批量、定时监测类任务 |

根据 Agents4Science 的观察和 DeepFact 中专家一次性判断只有 60.8% 准确率的数据，"全自治"应该是可配置选项，而不是默认值。

---

## 5. 实施路线图（推荐路径 C）

工期按一名工程师主导、agent 辅助估计，`[推断]`。

| 阶段 | 内容 | 验收指标 |
|---|---|---|
| **P0 评测先行**（1–2 周） | 建 golden set：30–50 个跨领域研究问题，含已知答案、已知反方、已知撤稿陷阱；指标脚本；接入 LitQA2 / ScholarQABench 子集和 DeepTRACE 的审计维度 | 基线跑通：现有 deep research 工具（如 GPT Researcher 或 open_deep_research）的分数作为对照 |
| **P1 文献内核**（3–4 周） | L2 存储；S0–S7；Scout（OpenAlex、S2、arXiv、Crossref）；Reader（PDF 解析加 PaperQA2）；验证器 ref-resolver、quote-exact、paraphrase-overlap、retraction-check | 引用存在率 100%；引文逐字匹配 100%；撤稿检出率 100%（用注入的陷阱测试） |
| **P2 论证层**（3 周） | S8–S12；论证图；warrant 检验；Skeptic 红队；SimReader | 在 golden set 上，已知反方观点的覆盖率 ≥90%；claim 支持率（多模型 NLI，经人工抽检校准）≥95% |
| **P3 写作与审计**（2–3 周） | S13–S18；逐句溯源；hedge 单调检查；Auditor；披露段 | 逐句可回溯率 100%；在 DeepResearch Bench 和 ResearchRubrics 上超过 P0 的基线 |
| **P4 实验模块**（4–6 周，可选） | 沙箱；预注册；autoresearch 式固定预算加单一指标循环；数字和图表只来自代码 | 报告里的每个数值都可由代码复现；预注册偏离 100% 被记录 |
| **P5 长时与外壳**（2–3 周） | 世界模型的跨会话持久化；Hermes 外壳（cron、消息、远程触发）通过 MCP 调 Kernel | 12 小时级运行不发生状态丢失；可以从任意检查点恢复 |
| **P6 自我改进与加固**（持续） | 基于评测的 skill 和 prompt 优化（GEPA / DSPy）；红队测试提示注入；成本优化 | 每次合入都不退化；注入攻击成功率趋近 0 |

**成本粗估 `[推断]`：** 一次文献型研究约 50–200 篇论文、数百次模型调用，前沿模型下大约 5–50 美元。实验型研究取决于算力，数据 API 的开销可以忽略（OpenAlex 每天 1 美元免费额度够日常使用）。

---

## 6. 预测与风险

| 预测 / 风险 | 置信度 | 应对 |
|---|---|---|
| 基座模型继续变强，生成质量随之提升（Nature AI Scientist 显示论文质量与模型发布时间显著相关），**但验证鸿沟不会自动消失** | 高 | 投资验证器和数据结构，它们不会随模型升级过时；prompt 编排会过时 |
| 通用 agent 运行时在 12 个月内仍会剧烈变动（Hermes 每周多次发布，OpenClaw 通告量很大） | 高 | 只通过 MCP 耦合，不 fork |
| 学术出版界对 AI 生成内容和幻觉引用的审查越来越严（NeurIPS / ICLR 已把幻觉引用列为拒稿理由） | 高 | G-Citation 设为硬门；默认生成披露段 |
| 全文获取和数据 API 的成本、限额会持续变化（OpenAlex 2026 年已转为免费加付费模式） | 中高 | 抽象出数据源适配层；缓存快照；支持用户上传 |
| 提示注入和恶意内容会针对 AI 审稿人和 AI 读者 | 高 | 内容与指令隔离；读者 agent 不给执行权限 |
| LLM 裁判本身不可靠（专家一次性判断都只有 60.8%） | 高 | 多模型投票加人工抽检校准；按 DeepFact 的做法维护可修订的评测标签 |
| 自我改进带来漂移或投毒（例如 Hermes 默认要求后台复盘"ACTIVE"写入） | 中 | 写入审批、评测把关、可回滚台账 |
| McNamara 谬误：agent 倾向于选择容易量化的问题 | 中 | S3、S4 的"so what"门由读者模型和人类把关，而不是看指标 |

---

## 7. 路径选择

| 路径 | 做法 | MVP 时间 `[推断]` | 可靠性上限 | 主要风险 | 适合谁 |
|---|---|---|---|---|---|
| **A. Hermes 为宿主** | 把研究流程写成 Hermes skills 加插件；复用 `grounded-citations`、`arxiv`、`research-paper-writing`；用 cron 做长时任务 | 2–4 周 | 中：状态和门都要靠 skill 约定，难以做硬保证 | 默认写入无审批；插件不沙箱；上游变动快；Python 生态合适 | 个人日常使用、想最快上手、需要消息触发 |
| **B. OpenClaw + Lobster** | 用 Lobster 把 S0–S20 写成类型化流水线，带审批门和恢复令牌；研究工具做成插件 | 3–5 周 | 中：流水线确定性好，但研究层仍需全部自建 | 攻击面最大（通告、ClawHub）；研究生态缺失；TypeScript | 已深度使用 OpenClaw、重视审批流程的团队 |
| **C. 自建 Research Kernel + 复用组件 + 任意 harness（推荐）** | 如第 4、5 节；核心自建，复用 PaperQA2 和学术 API；通过 MCP 暴露；harness 可替换，后期可接 Hermes 外壳 | 6–8 周到 P3 | 高：硬门和逐句溯源都是代码保证 | 前期投入最多；需要自己维护评测集 | 目标是"高准确、高可靠、可审计"的长期项目 |
| **D. 改造研究型开源项目** | 以 DeerFlow 2.0 或 open_deep_research 为底座，插入第 4.2 节的门和 claim 账本；实验部分参考 AI-Scientist-v2 | 3–6 周 | 中高：编排现成，但要侵入式修改 | 与上游分叉；LangGraph 绑定；AI-Scientist 许可证 | 想尽快拿到"报告型 deep research"并逐步加固 |
| **E. 完全从零（含 agent loop 和训练）** | 自写运行时；再用 RL 训练专用研究模型（Tongyi DeepResearch、MiroThinker 的路线） | 数月起 | 理论上最高，但实际取决于数据和算力 | 成本极高，大量重复造轮子 | 研究机构，或以"训练研究型模型"本身为目标 |
| **F. 分期混合：C 的核心 + A 的外壳** | P0–P3 按 C 做；P5 接入 Hermes，只用它的 cron、消息、远程触发，研究逻辑全部在 Kernel 里 | 与 C 相同，多约 1–2 周 | 高 | 两套系统的运维 | 既要可靠性，又要"随时随地派任务" |

### 决策建议

- **目标是可靠的研究产出**（论文级综述、可审计报告、可复现实验）：选 **C**，需要移动端或消息触发时升级为 **F**。
- **目标是一个月内好用的个人研究助理**：选 **A**，但要做到三件事：打开 `memory.write_approval` 和 `skills.write_approval`；强制使用 `grounded-citations` 的 `verify --evidence`；不装第三方 skill。这样以后仍能迁移到 C，因为技能和验证脚本都可以复用。
- **主要需求是报告型 deep research，不做实验**：选 **D**（DeerFlow 2.0），并把 G-Citation、G-Evidence、G-Objection 三道门作为最低改造范围。
- **不建议** 把 B 作为研究 agent 的底座，除非已有 OpenClaw 基础设施。
- **不建议** 选 E，除非目标就是训练模型。

---

## 附：主要来源

- 书：`docs/craft-research-process.md`（行号对应 pdftotext 提取的全文）
- Hermes：[架构文档](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)；[学习机制分析（Safeguard, 2026-10）](https://safeguard.sh/resources/blog/how-hermes-agent-learns-memory-skills-session-search-and-background-review)；`skills/research/grounded-citations/SKILL.md` 与 `optional-skills/research/research-paper-writing/SKILL.md`（仓库 main 分支）
- OpenClaw：[架构](https://docs.openclaw.ai/concepts/architecture)；[Lobster](https://docs.openclaw.ai/automation/taskflow)；[与 Hermes 的对比](https://github.com/openclaw/openclaw/blob/main/docs/start/why-openclaw/openclaw-and-hermes-agent.md)；[安全分析 arXiv 2603.27517](https://arxiv.org/abs/2603.27517)；[CSA ClawHub 报告](https://labs.cloudsecurityalliance.org/research/csa-research-note-openclaw-clawhub-supply-chain-risk-2026070/)
- AI Scientist：[Nature 2026](https://www.nature.com/articles/s41586-026-10265-5)；[Beel et al.](https://arxiv.org/abs/2502.14297)；Kosmos [arXiv 2511.02824](https://arxiv.org/abs/2511.02824)；[Co-Scientist](https://arxiv.org/abs/2502.18864)；[autoresearch](https://github.com/karpathy/autoresearch)
- 文献 agent：[PaperQA2](https://arxiv.org/abs/2409.13740)；[OpenScholar（UW 新闻）](https://www.washington.edu/news/2026/02/04/in-a-study-ai-model-openscholar-synthesizes-scientific-research-and-cites-sources-as-accurately-as-human-experts/)
- 评测与失败：[DeepTRACE](https://arxiv.org/abs/2509.04499)；[ResearchRubrics](https://arxiv.org/abs/2511.07685)；[DeepFact](https://aclanthology.org/2026.acl-long.1586/)；[GPTZero NeurIPS](https://gptzero.me/news/neurips/)；[Trehan & Chopra](https://arxiv.org/abs/2601.03315)；[Bisht et al.](https://arxiv.org/abs/2605.08956)；[验证鸿沟综述](https://arxiv.org/abs/2608.05179)；[DeepResearch Bench](https://deepresearch-bench.github.io/)
- 数据：[OpenAlex 定价与认证](https://openalex.mintlify.app/api-reference/authentication)
