# Research Kernel（路径 C）设计文档

> 状态：v2，已于 2026-11-05 获批（用户："开始实现"）
> 日期：2026-11-05
>
> 配套资料：
> - `research-agent-plan.md`：总体分析与路径对比
> - `docs/craft-research-process.md`：《The Craft of Research》按阶段蒸馏的流程，S0–S20，带原文行号
> - `docs/research/2026-11-05-c-tech-due-diligence.md`：技术尽调。API、库、驱动与评测的事实都出自这里，每条带 URL
>
> 约定：
> - 标 `[决定]` 的是我替你做的选择，可以推翻。
> - 标 `[推断]` 的没有直接来源。
> - 标 `[待你确认]` 的放在第 13 节统一列出。

---

## 1. 意图与目标用户

**你要什么（你的原话整理）：**

研究领域是 STEM，偏化学、生物、医学、AI、工程、计算机。系统分两个阶段：先确定研究问题，再产出成果。每个阶段各有两种方式。

确定研究问题：
- **D1 引导式发现。** 先和你讨论，多轮检索文献。围绕你感兴趣的方向找资料，帮你发现现有研究的空白（gap）和不准确之处，最后和你一起确认研究问题（RQ）。
- **D2 自主式发现。** 你只给一个大致领域。agent 自己找可研究的点，提出多个 topic 交给你审核，再自行打磨出最终的 RQ。

有了 RQ 之后：
- **P1 全自动。** 产出一篇高质量论文。
- **P2 协作。** 在 AI 薄弱的环节多次与你交互，最终合作完成 report 或 paper。

**你补充的要求（2026-11-05）：**
- 仓库私有，用你个人的 git 身份提交。
- 需要的 API key 由系统引导你去申请。
- 以后**不能依赖 omp**。
- 内置图形化浏览器：你在里面登录学校图书馆；agent 也能像人一样用浏览器检索文献，不必依赖 API key。
- 支持度校准只用外部人工标注数据。
- 论文只用英文；交互用中文或英文都可以。
- 每个研究项目都有一个类似 Zotero 的文献库。

**为什么要做成"Kernel + 驱动"：**
- 可靠性必须由代码保证，而不是由 prompt 保证。证据见 `research-agent-plan.md` §2.5：
  - deep research 系统的引用准确率只有 40–80%。
  - NeurIPS 2025 已录用论文中仍有幻觉引用。
  - LLM 评委对新颖性存在"novelty mirage"，即普遍高估模型自己生成的 RQ 的新颖性（[RQ-Bench, arXiv 2606.12071](https://arxiv.org/abs/2606.12071)）。
- 一项 2015 年的荟萃分析估计，医学期刊文章的引文错误率合计 25.4%，其中严重错误 11.9%；但纳入的各项研究之间异质性很大，这个数字不能当作普遍基准率（[Jergas & Baethge 2015](https://pubmed.ncbi.nlm.nih.gov/26528420/)；2025 年同作者发表了更新版）。即便如此，它说明"找出现有研究中引用不准确的地方"有真实价值，而且可以自动化核查。

---

## 2. 目标、非目标、反目标

### 2.1 目标（v1 = 第 12 节的 Plan 1–5）

1. 支持 D1、D2 两种发现方式，以及 P1、P2 两种产出方式。
2. 第一版支持四种论文类型：
   - `systematic_review`：PRISMA 2020，可附 meta-analysis
   - `scoping_review`：PRISMA-ScR
   - `critical_review`：批判性或叙述性综述，加上空白分析和研究议程
   - `research_proposal`：适合需要湿实验的领域，只写到 RQ、假设和方法设计
3. 最终稿逐句可追溯。每一句都必须带一种来源标记（完整语法见 §9.1 V-COVER）：
   - 文献 claim：能沿"claim → 证据 → 笔记 → 原文快照（带页码和字符区间）→ 已解析的文献记录"一路回溯；
   - 系统自身的研究操作：指向事件日志或已冻结的 protocol；
   - 计算结果：指向代码生成的图表或统计产物；
   - 研究计划：仅限 research_proposal；
   - 明确标注的作者推断；
   - 受限的常识。
4. 代码层面的硬门：
   - 引用必须存在，元数据一致
   - 已撤稿文献不能作为证据
   - 直接引语与原文逐字一致
   - 转述不得与原文过近
   - 措辞强度不得超过原文
   - 句中数字必须出自证据
   - claim 必须有支持；无法判定的一律弃权
5. 两次运行之间可以恢复。完整事件日志，可以审计、导出、复现。
6. 不需要任何付费 API key。**产品不依赖 omp 这个程序，但 2026-11-09 起默认驱动走 omp_gateway**（`omp auth-gateway stdio`，用 omp 自己的登录，避开 Claude Code 和 Codex CLI 的用量限额），所以默认配置需要装好并登录 omp；不装 omp 时，把角色换成你已登录的官方 `claude` 和 `codex` CLI、Ollama 云端模型或 API key 驱动即可，系统照常工作。
7. 内置图形化浏览器：你在里面登录学校图书馆；agent 能在其中按人类节奏检索文献、下载全文；遇到验证码或登录过期时转交给你处理。
8. 每个研究项目一个类 Zotero 文献库：条目、附件、分组、标签、笔记、全文检索，能导入导出 BibTeX、RIS、CSL-JSON。
9. Workbench 网页作为统一界面：聊天、文献库、检查点、进度、草稿、设置。首次运行有向导，引导你申请和验证各项 key。

### 2.2 非目标（v1 不做，另起计划）

- 计算型实证研究（AI/CS 实验、公共数据二次分析如生信、化学数据库、临床试验注册数据）**属于你的 STEM 范围**，但 v1 不实现，作为 Plan 6 单独设计、单独批准。湿实验不在任何计划之内，只能产出研究计划书。
- 消息推送、定时任务、移动端触发、与 Zotero 双向同步 → Plan 7
- 自我改进：根据评测结果自动优化 prompt 和 skill → Plan 7
- 多用户或托管服务。也不能托管：订阅凭据只允许本人在本机使用。
- 投稿系统对接、期刊排版细节。

### 2.3 反目标（做到了也算失败的情况）

- 产出一篇"读起来很像论文"、却无法逐句回溯的文章。
- 为了凑出全自动结果，绕过或降级任何硬门。
- 在 P1 模式下把"未经人审"的新颖性或重要性判断写成确定结论。这类判断必须在披露段注明"未经人类审核"。
- 系统本身成为攻击面：抓取的内容被当作指令执行，或者安装第三方 skill。

---

## 3. 约束

| 类别 | 约束 | 来源 |
|---|---|---|
| 运行环境 | macOS arm64（M4 Pro，24 GB 内存，磁盘剩 71 GB），Python 3.11（python.org）+ uv 0.13；**没有 Docker**；有 Java；有 pandoc 和 tectonic，可生成 PDF | 本机实测 |
| LLM 访问 | `claude` 2.1.309（Claude Code，订阅登录）；`codex` 0.166.0-alpha.6（ChatGPT 登录）；Ollama 0.40.14：本地有 `embeddinggemma-2`、`lfm3`、`bonsai-2`，云端有 `deepseek-v4.1-flash:cloud`、`gemma4:31b-cloud`。目前没有 provider API key | 本机实测 |
| 订阅登录的使用边界 | Anthropic 官方文档（尽调 C14 引用的 legal-and-compliance 页面）写明：OAuth 只供订阅用户在 Claude Code 中正常使用；第三方开发者不得提供 claude.ai 登录，也不得代用户转发订阅凭据；但不禁止"本人登录**未修改的** Claude Code"。omp 的做法是在自己的程序里内置 Claude Code 和 Codex CLI 的 OAuth client ID，以第一方客户端的身份直接调用接口（见 earendil-works/pi `packages/ai/src/auth/oauth/*`，MIT）。**本系统不复制这种做法**（不在自己的代码里内置或转发任何 OAuth 凭据）。我的判断 `[推断]`：这种做法与上述条款存在明显冲突，有账号被停用的合规风险；而且依赖非公开接口，随时可能失效。**2026-11-09 你决定默认驱动改走 omp_gateway**，理由是避开官方 CLI 的用量限额；这样订阅模型就经由 omp 的这种登录调用。上述合规风险和接口失效风险已向你说明，你当天明确表示接受、保持 omp_gateway 默认。要只通过官方 CLI 使用订阅，把角色换回 claude_cli、codex_cli 即可 | 尽调 C14；pi 源码；2026-11-09 你的决定 |
| `claude -p` 的用法 | 必须用非 `--bare` 模式，否则不读 OAuth 凭据；官方计划把 `--bare` 设为默认值，所以要固定 CLI 版本。用 `--strict-mcp-config --mcp-config`、`--allowedTools`、干净的工作目录来隔离 | 尽调 C14 |
| `codex exec` | 用 `--output-schema`、`--json`、`--sandbox read-only`、`--ephemeral`；当前是 alpha 版，flag 可能变化 | 尽调 C15 |
| Ollama Cloud | **不强制 JSON schema**，结果要先解析、再用 Pydantic 校验，失败就重试；本地模型支持 `format` schema | 尽调 C16 |
| omp auth-gateway（2026-11-05 尽调结论；2026-11-07 起是普通驱动，2026-11-09 起是默认驱动，见 §8.1） | 实测 `omp auth-gateway stdio` 能用本机的 omp 登录调用 `anthropic/claude-haiku-4-5`（200）和 `openai-codex/gpt-6-luna`（2026-11-05 实测 200，工具调用正常），但同样不保证按 JSON schema 输出。omp 的 `retry.modelFallback`（默认开启）会让网关在所请求的模型失败时改用 `retry.fallbackChains` 里的模型作答（本机的 default 链是 `anthropic/claude-sonnet-5-5`）；驱动启动网关时关掉它（§8.1）。**系统不绑定它**：不装 omp 时换成别的驱动，整个系统照常工作 | 本机实测 |
| 浏览器自动化 | Playwright 1.60（Python），使用持久化 profile 的 Chromium，可以有界面运行。Google Scholar 禁止自动化查询，会触发验证码；出版商的许可协议禁止系统性批量下载，下载过多可能导致整个学校的 IP 被封。因此浏览器检索必须保持人类节奏，并设每日上限 | 本机实测（版本）；`[推断]`（站点行为） |
| 元数据抓取 | Zotero translation-server（Node；Zotero 本体是 AGPL，该仓库的许可证字段 `[待核实]`），不用 Docker 也能 `npm start` 启动，端口 1969，提供 `/web`、`/search`、`/export`、`/import`；本机已有 Node | GitHub README |
| OpenAlex | 2026 年起免费 key 每天额度 1 美元，不带 key 时同一 IP 共享每天 0.10 美元；按 DOI 单条查询免费，搜索每千次 1 美元；申请 key 需要真实邮箱并点击激活 | 尽调 A1 |
| Crossref | 用 polite pool，必须带真实 `mailto`；列表查询限 3 次/秒；撤稿信号看 `updated-by`（来源包括 `retraction-watch`） | 尽调 A2 |
| Semantic Scholar | 无 key 时公共池实测第一次就返回 429；有 key 时 1 次/秒 | 尽调 A3 |
| arXiv | API 每 3 秒 1 次；全文按 `/html` 优先、`/pdf` 其次，每 15 秒最多 1 篇；只限个人研究用途，不得再分发 | 尽调 A4 |
| PMC | 2026 年 8 月 OA web service 和批量文件已下线；BioC JSON 仍可用（带段落偏移） | 尽调 A5 |
| doi.org | 对脚本返回 Cloudflare 403，不能依赖；改用 Crossref transform 或 DataCite | 尽调 A6 |
| 许可证 | 避开 AGPL 的 `pymupdf` 和权重受限的 `marker`；`bespoke-minicheck` 是 CC BY-NC，只能个人使用（2026-11-05 尽调结论；2026-11-07 起已不使用 MiniCheck） | 尽调 B8、B10 |
| Europe PMC | REST 可用；能按 `OPEN_ACCESS:y` 检索，有全文链接和文本挖掘注释 | 本机实测 |
| bioRxiv、ClinicalTrials.gov v2、PubChem PUG-REST | 可用 | 本机实测 |
| ChemRxiv | 公共 API 实测无返回，改由 OpenAlex 覆盖 | 本机实测 |

---

## 4. 方案取舍

### 4.1 总体路径

你已经选了 C：自建 Kernel，周边尽量复用，驱动层可以替换。其他路径的取舍见 `research-agent-plan.md` §3 和 §7。

### 4.2 C 内部的三个关键取舍

| 问题 | 选项 | 选择与理由 |
|---|---|---|
| 运行在哪里、谁来编排 | (1) 跑在 omp 里；(2) 独立程序；(3) 先 (1) 后 (2) | **(2) 独立程序** `[决定]`，这是你的要求：以后不能依赖 omp。核心是一个常驻的本地服务 **`rk serve`**，它是唯一的状态写入者，进程模型见 §6.0。它负责：持久化阶段状态机与编排器；进程内工具调用循环；内置浏览器；Workbench 网页（聊天、文献库、检查点、进度、运行控制）。D1 在 Workbench 里与 Lead agent 对话完成；D2、P1、P2 的流水线由 `rk serve` 内的编排器派发给各个角色，可以在 Workbench 上启动、暂停、继续，`rk run` 只是同一功能的命令行入口。MCP 有两种用途：**内部通道**，供 claude、codex CLI 驱动回调工具（是必需的，对你不可见）；**外部接入**，让 Claude Code、Codex、omp 等 MCP 客户端也能连接同一个项目（可选）。门只在 Kernel 里，任何入口都无法绕过。 |
| 怎么调用 LLM | 复制 omp 的 OAuth；官方 CLI；官方 API key；Ollama；omp 网关 | **官方渠道的模型提供层** `[决定]`，分三类：① 订阅 → 官方 CLI（`claude -p`、`codex exec`），这是政策允许的用法；② 官方 API key（v1 支持 Anthropic、OpenAI；Gemini、OpenRouter 放到以后），有 key 时启用，无人值守长跑最稳；③ Ollama 云端（2026-11-07 起默认不用本地模型）。omp 网关是第 ④ 类普通驱动，装了 omp 并登录即可用，无需开关；产品不依赖它。**不实现 OAuth 冒用**，理由见 §3。 |
| 内置浏览器 | Playwright 控制的 Chromium；Electron 或 Tauri 桌面壳；omp-webui 的浏览器 | **Playwright 持久化 profile 的 Chromium，有界面运行** `[决定]`。一个浏览器窗口就是整个"内置浏览器"：第一个标签页是 `rk` 的 Workbench 网页，其余标签页是 agent 检索时打开的页面。你在同一个窗口里登录图书馆，登录状态保存在 `$RK_HOME/browser-profile/`。全部用 Python 实现，不需要另写桌面壳。Electron 方案的界面更像一个应用，但前后端要写两套语言，代码量约是这个方案的两倍。 |
| 文献库 | 直接用 Zotero 桌面版；自建 | **自建、每个项目一个库，并与 Zotero 格式兼容** `[决定]`。验证器需要内容寻址的原文快照和字符级定位，Zotero 的数据模型给不了。库支持导入导出 BibTeX、RIS、CSL-JSON；可以选装 Zotero translation-server，从任意出版商页面抓取元数据；后续计划可加与 Zotero 的同步。 |
| 状态与持久化 | SQLite 事件日志 + 自写状态机；LangGraph；DBOS；Temporal | **SQLite WAL + 追加式事件日志（哈希链）+ 显式状态机** `[决定]`。零基础设施，事件日志本身就是审计记录。DBOS 留作备选：如果自写的崩溃恢复不够用再换。 |
| 文献 RAG | 依赖 PaperQA2；借鉴它的做法自己实现 | **借鉴，不依赖** `[决定]`，与尽调 B11 一致。PaperQA2 自带一套 LLM 循环和状态，会和账本、订阅驱动冲突。它会作为 LitQA2 评测的对照基线。 |

---

## 5. 用户流程

### 5.1 D1 引导式发现（在对话里进行）

1. **建项目。** 你在 Workbench 的聊天面板里说想研究什么（中文或英文都可以；也可以从任何 MCP 客户端接入）。Lead agent 调 `project_create` 和 `intake`，记录：
   - 领域、读者
   - 论文类型（暂定）
   - 产出方式（P1 或 P2）
   - 语言：论文默认英文 `[决定]`，交互用中文
2. **每一轮探索：**
   - agent 先检索（`search_works`、`snippet_search`、`expand_citations`），做出"景观图"：主题聚类、近年发文趋势、代表作。
   - 再从选中的方向里挖空白，空白有五类，见 §8.3。
   - 每条发现都带证据链接。
   - 你给出反馈，例如"更关注机制""排除动物实验"，系统记为 `exploration_round`。
3. **形成 RQ 候选。** 每个候选都必须通过 `question` 阶段的门：
   - 书中的三步式："我在研究 X，因为想弄清 Y，以便帮读者理解 Z"
   - 问题陈述：条件 + 后果
   - FINER 标准（可行、有趣、新颖、合乎伦理、相关），临床问题加 PICO
   - 至少两个对立假设，每个写明证伪条件
   - "已经有人做过吗"检查（规则见 §8.3 末尾）
4. **你批准 RQ。**
   - 记为 `checkpoint(kind=rq_approval, by=human)`。
   - 冻结后，RQ 的文本哈希写入事件日志。

### 5.2 D2 自主式发现（由编排器运行）

1. `rk run <project> --until topics`：编排器依次跑 `landscape → gaps → topics`。
2. 产出 N 个候选 topic，默认 6 个 `[决定]`。每个包含：
   - 空白证据（引证）
   - 最接近的已有工作（top-5）
   - 可行性和所需数据
   - 适合的论文类型
   - 风险
3. **topic 审核是硬检查点，任何模式下都不能跳过。** 这是你的要求，也有 novelty mirage 的证据支持。你可以批准、否决，或者给出修改意见；有修改意见时，相关阶段回到 `gaps` 或 `topics` 重跑。
4. 选定 topic 之后，agent 自行打磨 RQ。它要通过和 D1 相同的门；"已经有人做过吗"检查判定为"已有人做过"时，自动退回 `topics`（规则见 §8.3 末尾）。
5. P2 模式下，最终 RQ 还要你确认一次。P1 模式下自动采纳，但在披露段写明"RQ 由 agent 选定"。

### 5.3 P1 全自动 / P2 协作

两种模式走同一条流水线。区别只在于检查点由谁来答：

```
protocol → search_screen → read_extract → synthesize → argue → draft → revise → audit → export
```

- **硬门失败时：**
  - 自动修复，或者沿回边退回上游阶段，每条回边有次数上限；
  - 超过上限：P2 交给你决定；P1 只能**删除**相关内容，或把它**改写成更窄、更弱的新 claim 并重新验证通过**，改动写入局限性段，然后继续。**"标为未证实后保留在正文里"不是选项。**
  - **硬门本身永远不能被关掉。**
- **"AI 薄弱环节"检查点**：P2 必须由人回答；P1 由多厂商 LLM 投票决定，记为"未经人审"。投票规则：两家意见一致才算通过；不一致时取保守选项，即不纳入、不做合并、不下结论。**投票只用于判断类问题，永远不能把支持度判定从 uncertain 改成 supported。**

| 检查点 | 理由（证据） |
|---|---|
| `protocol_approval`：检索式、纳入排除标准、提取字段、分析计划 | 方法选择需要领域判断；预注册必须在看数据之前冻结 |
| `screening_disagreements`：两个筛选者意见不一致的条目 | 系统评价通行做法是双人筛选、分歧由第三方仲裁 |
| `evidence_conflict`：同一问题上证据相互矛盾 | DeepTRACE 发现 deep research 在争议问题上普遍片面 |
| `uncertain_support`：claim 支持度落在无法判定的区间（**只在 P2 中出现**；P1 直接按上面的"删除或改写"处理，不投票） | DeepFact：专家一次性判断准确率只有 60.8%，判定需要裁决 |
| `significance_novelty`：重要性和新颖性 | RQ-Bench 的 novelty mirage；LLM 研究"品味"弱（Trehan & Chopra） |
| `final_review`：终稿审阅 | Agents4Science 的观察：被接收的 AI 论文中人类指导往往更多 |

- **你怎么回答检查点：**
  - 在 Workbench 的"检查点"面板里回答，有通知角标提示；
  - 或者用 CLI：`rk checkpoint list` / `rk checkpoint answer <id>`（交互式终端里需要你再次确认）。
  - **硬性人类检查点**（`topic_review`、`rq_approval`、P2 中的所有薄弱环节检查点、`captcha_wait`、`login_wait`）**只能通过上面两种渠道回答**。任何 agent 都拿不到回答这类检查点的工具：Lead 能在聊天里把检查点展示给你、解释它，但不能替你回答；外部 MCP 客户端也一样。回答接口要求 Workbench 会话令牌，或终端里的二次确认。agent 驱动的浏览器页面被禁止访问 Workbench 所在的源（见 §8.5），所以 agent 不可能在网页上替你点"批准"。
  - 编排器遇到人类检查点时，把该任务挂起（状态 `waiting_human`），继续做不依赖它的工作；没有其他可做的工作时，该项目的运行进入 `paused_waiting_human`。你回答后，`rk serve` 自动继续，不需要重新执行命令。如果 `rk serve` 被关闭，下次启动时从事件日志恢复所有运行 `[决定]`。
  - 并发规则 `[决定]`：每个项目同一时间只有一个运行（由 `rk serve` 内部保证）；所有写操作都经过 `rk serve`（单一写入者），带幂等键；SQLite 使用 WAL，只供其他进程只读访问。

---

## 6. 架构

```
┌──────────────────────── 内置浏览器（Playwright Chromium，持久化 profile）────────┐
│ 标签 1：Workbench（http://127.0.0.1:<port>）：聊天 · 文献库 · 检查点 · 进度 · 设置 │
│ 标签 2…n：agent 打开的检索页面（图书馆、Google Scholar、PubMed、出版商）；         │
│          你可以随时接手：登录、处理验证码、自己点开文章                          │
└───────────────▲───────────────────────────────▲─────────────────────────────┘
                │ HTTP/WebSocket                 │ CDP（Playwright）
┌───────────────┴───────────────────────────────┴─────────────────────────────┐
│ rk serve（常驻本地服务，单一写入者；CLI 和 MCP 代理都是它的客户端）            │
│  orchestrator/  阶段状态机、回边、预算、任务派发、检查点挂起与恢复                 │
│  agent/         进程内工具调用循环（API 类提供者用）；角色提示词与输出 schema      │
│  llm/           提供者：claude_cli · codex_cli · anthropic_api · openai_api ·    │
│                 gemini_api · openrouter · ollama · omp_gateway · human           │
│  browser/       浏览器会话、人类节奏的调度器、各站点适配器、下载管理、验证码转人工  │
│  library/       文献库：条目、附件、分组、标签、笔记、全文检索、BibTeX/RIS/CSL   │
│  ui/            Workbench（FastAPI + 静态前端；WebSocket 推送）                  │
│  setup/         首次运行向导：在内置浏览器里引导你申请 key，并逐项验证             │
│  mcp/           可选 MCP 服务器（按角色暴露工具）                                 │
│  store/         SQLite（WAL）事件日志 + 投影表；blob 与文本层                     │
│  sources/       OpenAlex · Crossref · EuropePMC/PubMed · arXiv · S2 · bioRxiv · │
│                 ClinicalTrials · PubChem；HTTP 缓存与预算                         │
│  fulltext/      BioC → arXiv HTML → OA PDF → 图书馆（浏览器）→ PDF 规范层        │
│  verify/        ref · retract · quote · paraphrase · hedge · support · numeric │
│                 · coverage · balance · prisma · stats · novelty                │
│  gates/  write/  evals/                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.0 进程模型 `[决定]`

- **`rk serve`**：常驻本地服务，每台机器一个（`$RK_HOME/serve.lock` 加上监听端口文件）。它独占：
  - SQLite 写入（单一写入者）
  - Playwright 浏览器上下文
  - Workbench 的 HTTP 和 WebSocket 服务
  - 编排器（每个项目一个运行协程）
  - 进程内工具调用循环
- **`rk` 的其他 CLI 命令**（`run`、`pause`、`checkpoint`、`library`、`setup`、`status`……）一律是 `rk serve` 的 HTTP 客户端，使用 `$RK_HOME/serve.token`（权限 0600），没有例外：任何 CLI 命令都不直接打开 SQLite。`rk serve` 没在运行时，这些命令会先把它启动起来。
- **`rk mcp --role <r> --project <p>`**：无状态代理，由 claude 或 codex CLI 驱动按需启动。它把 MCP 工具调用通过 HTTP 转发给 `rk serve`，并带上本次任务的一次性令牌。令牌绑定 task_id、角色和允许的工具，任务结束即失效。代理自己不打开数据库，也不打开浏览器。外部 MCP 客户端用同一个代理，只是令牌换成你在 Workbench 里生成的长期令牌，权限为 lead 角色，可以随时吊销。
- 关闭 `rk serve` 时，正在执行的任务被标记为中断；下次启动时，按幂等键重新排队。

### 6.1 存储布局 `[决定]`

```
$RK_HOME/                         默认 ~/ResearchKernel
  config.toml                     全局设置：邮箱、提供者与 key（0600）、预算、浏览器节奏
  browser-profile/                内置浏览器的持久化 profile（图书馆登录状态、cookie）
  cache/http/                     hishel 缓存（API 响应）
  projects/<slug>/
    rk.sqlite                     事件日志 + 投影表（WAL，busy_timeout=10s）
    library/                      文献库附件（由 library 表索引）
      files/<item_id>/<name>.pdf  你可见的文件名；内容同时写入 blobs/
    blobs/sha256/<ab>/<hash>      原始 PDF/XML/HTML（内容寻址，只写一次）
    text/<hash>.json              规范文本层：pages[{n, text}]、规范化映射、来源格式
    browser/                      本项目的浏览轨迹：URL、截图、下载记录（事件日志引用）
    artifacts/
      drafts/<section>.v<N>.md
      figures/<id>.{py,png,svg}   图表 = 代码 + 数据哈希 + 输出
      refs.json                   CSL-JSON
      paper.{md,tex,pdf,docx}
      disclosure.md
    exports/run-<ts>.zip          可复现包：事件日志、快照清单、产物（不含 key 和 profile）
```

### 6.2 标识与事件

- **ID 格式：** `<前缀>_<ULID>`。前缀有 `prj, src, snp, nte, clm, evd, edg, obj, hyp, rq, tpc, gap, prt（protocol）, dsg（proposal 的方法设计）, fig, stt（统计结果）, chk, ver, drf, tsk, run, evt`。
- **事件表 `events`：**

  ```
  seq INTEGER PK, evt_id TEXT UNIQUE（evt_<ULID>，供句子标记引用）, ts, project_id,
  actor_kind ∈ {human, agent, kernel},
  actor ("Reader@claude_code:<model>" / "kernel" / "user"),
  type, entity_id, payload JSON,
  prev_hash, hash = sha256(prev_hash || canonical_json(row))
  ```

- 所有写操作都走 `store.apply(event)`。同一个事务里同时写事件和投影表。
- 投影表可以随时由事件日志完整重建：`rk store rebuild`。
- `rk store verify-chain` 校验哈希链。

### 6.3 核心实体（投影表）

| 实体 | 关键字段 |
|---|---|
| `project` | slug, title, field, audience(json, S0 读者清单), paper_type, discovery_mode ∈ {guided, autonomous}, production_mode ∈ {auto, collab}, language, stage, budgets(json) |
| `source`（= 文献库条目 item） | ids(doi, pmid, pmcid, arxiv, openalex, s2, isbn, url), item_type（Zotero 风格：journalArticle, preprint, book, bookSection, conferencePaper, thesis, report, dataset, webpage）, csl(json), title, creators[], year, venue, abstract, src_type ∈ {primary, secondary, tertiary}（相对于本项目而定）, retraction ∈ {none, retracted, withdrawn, eoc, corrected, unknown}, retraction_checked_at, oa_urls, discovered_via ∈ {search, backward, forward, snippet, browser, user, ai_suggested, import}, stance ∈ {supports, challenges, neutral, unknown}, reliability(json, 书中 11 项), tags[], collection_ids[], read_status, rating, added_by ∈ {human, agent} |
| `attachment` | source_id, kind ∈ {pdf, html_snapshot, supplementary, link}, filename, sha256, origin ∈ {oa_api, library_browser, publisher_browser, user_upload, import}, access_note（例如"通过 XX 大学图书馆获取，仅限个人研究使用"）, fetched_at |
| `collection` | name, parent_id?, kind ∈ {manual, smart(query)}（例如"已纳入""反方""待读"） |
| `snapshot` | sha256, source_id, kind ∈ {bioc, arxiv_html, tei, pdf, html}, url, fetched_at, text_path, page_count |
| `note` | source_id, snapshot_sha, kind ∈ {quote, paraphrase, summary, own}, text, locator{page, start, end}, role_in_source ∈ {main, sub, concession, qualification, summary_of_other}, hedge_level 0–3, keywords[], verified(json) |
| `claim` | text, kind ∈ {fact, definition, cause, evaluation, policy}, hedge_level, scope_limits[], status ∈ {draft, supported, uncertain, unsupported, contradicted, withdrawn}, contested bool |
| `evidence` | claim_id, note_ids[], criteria{accurate, precise, sufficient, representative, authoritative, clear, relevant}（每项含分数和理由）, interpretation, provenance_chain[] |
| `edge` | from_id, to_id, type ∈ {reason_of, evidence_of, warrant_of, objection_to, response_to}, warrant{circumstance, consequence, type, tests{…}} |
| `objection` | target_id, category ∈ {problem, soundness, alternative, other_cause, counterexample, definition}, text, source_id?, priority, response_claim_id?, status |
| `hypothesis` | text, falsifier, rival_of, status, prereg_hash |
| `protocol` | version, paper_type, search_strategy(json: 数据源、检索式、日期范围、过滤条件), eligibility(json), extraction_schema(json), synthesis_plan(json: 综合方式、效应量、模型、异质性、敏感性分析), frozen_hash, frozen_at, deviations[] |
| `design` | 仅用于 research_proposal：proposed_methods(json: 实验或计算设计、样本、对照、测量、分析), linked_hypothesis_ids[], frozen_hash |
| `artifact` | 前缀为 fig 或 stt；code_path, input_hashes[], output_path, values(json: 可供 V-NUM 比对的数值), created_by_task |
| `rq` | three_step{topic, question, significance}, problem{type, condition, consequence[]}, finer{…}, pico?, status, approved_by, frozen_hash |
| `topic` | text, gap_ids[], nearest_works[], feasibility, paper_type, risks, status |
| `gap` | kind ∈ {contradiction, stated_limitation, citation_distortion, retracted_still_cited, evidence_map_hole}, description, evidence_ids[], verified |
| `screening` | source_id, stage ∈ {title_abstract, full_text}, decisions[{reviewer, include, reason}], final, by |
| `extraction` | source_id, schema_version, values(json，每个值都带 note_id 定位) |
| `verification` | target_type, target_id, verifier, verdict ∈ {pass, fail, uncertain, skipped}, score, details(json), model?, ts |
| `checkpoint` | kind, stage, prompt, options, answer, answered_by ∈ {human, vote}, votes[], status |
| `task` | stage, role, driver, input_ids, output_schema, status ∈ {queued, running, done, failed, waiting_human}, attempts, idempotency_key |
| `draft` | section, version, path, sha256, verification_summary |
| `browse_log` | task_id, site, action ∈ {search, open, download, login_wait, captcha_wait}, url, query?, result_count?, screenshot_path?, ts |

---

## 7. 阶段状态机

### 7.1 阶段表

**S 编号**对应书中的流程，见 `docs/craft-research-process.md`。门的具体实现见 §9。**下表描述 systematic_review 的完整流程；各论文类型的差异以 §7.3 为准，§7.3 与本表冲突时以 §7.3 为准。**

**D1 也走这台状态机：** 由对话中的 Lead agent 推进，门相同。D1 的 `topics` 阶段产出你选定的方向（至少 1 个 topic，由你通过 `topic_review` 检查点批准）以及探索轮次的记录。

| 阶段 | 书中对应 | 进入条件 | 产出 | 门（主要验证器和规则） | 允许的回边 |
|---|---|---|---|---|---|
| `intake` | S0 | — | `project.audience`, paper_type, 两个 mode | 字段完整 | — |
| `landscape` | S1, S6 | intake | 聚类、趋势、代表作（`source`） | 每个聚类 ≥5 篇已解析的来源；检索日志完整 | — |
| `gaps` | S2, S4, S7 | landscape | `gap[]` | 每条 gap 有证据并通过对应验证器（§8.3） | → landscape |
| `topics` | S2, S3 | gaps | `topic[]`，D1 另有探索轮次记录 | D2：≥N 个候选，每个都有 gap 证据和最近工作；D1：≥1 个；**两种方式都需要人类审核**（`topic_review` 检查点） | → gaps |
| `question` | S3, S4, S5 | topic 已批准 | `rq`, `hypothesis[]` | 三步式槽位完整；后果链 ≥2 层；FINER/PICO；≥2 个对立假设且都有证伪条件；"已有人做过"检索留有日志；RQ 冻结 | → topics |
| `protocol` | S5, S6 | rq 冻结 | `protocol`（检索式、纳排标准、提取 schema、综合计划） | 结构完整；systematic_review 需满足 PRISMA-P 必填项；冻结哈希（是否算预注册按 §7.3）；P2 需人批准 | → question |
| `search_screen` | S6 | protocol 冻结 | 去重后的记录、筛选决定、PRISMA 计数 | **默认全部双筛**。Triage 自动排除是**可选开关、默认关闭**：只有在带标签的验证集上（例如已知纳入研究的系统综述复现集），召回率 95% 置信下界 ≥0.98 时才允许启用；启用后在 PRISMA 中单列 "records excluded by automation tools"，记录模型、阈值和验证结果，每次运行仍抽查一部分被排除的记录由双筛复核，抽查发现漏检就自动关闭 Triage 并改为全部双筛。分歧已处理；计数算术自洽；排除理由齐全 | → protocol（偏离要记录） |
| `read_extract` | S7 | 已纳入的研究 | snapshot、note、extraction | 引语逐字一致；转述不过近；extraction 每个值都有定位；撤稿检查完成 | → search_screen |
| `synthesize` | S10 | 提取完成 | `evidence[]`；按 protocol 预先指定的综合方法得到的结果 | 证据 7 项判据（书中的 5 项 + 相关性，其中"充分"与"有代表性"分开评）；数值可重新抽取；有代表性声明。**合并分析（meta-analysis）只在 protocol 预先指定、并且临床或方法学上的可比性评估通过时才做**；否则做叙述性综合（按 SWiM 报告）或放弃合并。统计结果由代码重算一致 | → read_extract |
| `argue` | S8, S9, S11, S12 | 证据完成 | `claim[]`, `edge[]`, `objection[]` | 支持度判定；措辞强度单调；warrant 五项检验；红队异议已回应；无隐藏弱点 | → synthesize, → question |
| `design`（仅 research_proposal） | S5, S8 | argue 完成 | 冻结的 `design`（dsg_）：方法设计、对照、测量、分析计划、样本或数据来源、预期结果与替代解释 | 每项设计都关联到至少一个 hypothesis，且每个 hypothesis 都有对应的检验设计；每个 hypothesis 的证伪条件都能由设计中的测量判定；关于可行性的 claim（例如"该方法可以测量 X"）必须是 supported；P2 需你批准，P1 投票并记为"未经人审"；冻结哈希 | → argue, → question |
| `draft` | S13, S15, S16, S17 | argue（research_proposal 为 design） | 各节草稿（带标记） | 句子标记覆盖 100%；数字一致；图表来自代码 | → argue（research_proposal 也可回到 → design） |
| `revise` | S14, S18 | draft | 新版本草稿 | SimReader 质询已处理；与上一版的差异已验证；最多 K 轮 | → draft |
| `audit` | S15, S20 | revise | 验证报告、`disclosure.md`、局限性 | §9 的全部硬门重跑并通过；披露完整 | → 对应阶段 |
| `export` | — | audit 通过 | paper.md/tex/pdf/docx、refs | 导出前后标记一致；引文全部解析 | — |

### 7.2 预算与回边上限 `[决定]`

- **回边：** 每条回边默认最多 2 次。超过之后：P2 转为人类检查点；P1 进入降级处理（§5.3）。
- **任务重试：** 每个任务最多 3 次。
- **开放式阶段**（landscape、gaps、topics、argue、revise）：每个阶段在每个运行时段内最多派发 40 个角色任务。
- **枚举式阶段**（search_screen、read_extract）：任务数由记录数决定，不设固定上限，只受驱动调用速率限制。
- **墙钟时间：** 每个项目的单个运行时段默认 12 小时。到时间后运行进入 `paused_budget`，由 `rk serve` 保存进度，你在 Workbench 或用 `rk run` 让它继续。整个项目没有总时长上限，可以分多天完成。
- **订阅驱动：** 无法计 token，只按"调用次数"计数；每类驱动有每小时调用上限，默认 claude 30 次、codex 20 次。
- **批处理与规模：** 筛选每次调用处理一批记录，默认 25 条。v1 的系统综述按**去重后 ≤3,000 条记录**设计。全部双筛时约需 240 次调用，按上面的速率约 12 小时，需要分 1–2 次运行完成 `[推断]`。记录数超过上限时，`protocol` 门会要求先收窄检索式，或者由你确认接受更长的运行时间。

这套预算同时对应书中"再读一篇式拖延"的警告。

### 7.3 各论文类型的阶段图 `[决定]`

所有类型都必须先走完 `intake → landscape → gaps → topics → question`。之后的流程按类型分开：

| 类型 | 阶段序列 | 不适用的门 | 必备章节 |
|---|---|---|---|
| `systematic_review` | protocol → search_screen（双筛 + PRISMA）→ read_extract → synthesize（可含 meta-analysis）→ argue → draft → revise → audit → export | — | Abstract, Introduction, Methods（检索、纳排、提取、偏倚风险、综合方法）, Results（PRISMA 流程图、研究特征、综合结果）, Discussion, Limitations, Conclusion, Disclosure |
| `scoping_review` | protocol（**冻结并算作预注册**，PRISMA-ScR 建议先有 protocol）→ search_screen（单筛 + 随机 10% 双筛抽检；抽检中纳入判定的不一致率 >10% 时改为全部双筛；报告 PRISMA-ScR 流程）→ read_extract（以制图为主）→ synthesize（证据图，不做 meta-analysis）→ argue → draft → revise → audit → export | V-STATS | 同上，Results 改为"证据图与主题综合" |
| `critical_review` | protocol（轻量版：问题、检索策略、选文原则，不要求 PRISMA-P；冻结只用于追溯，不算预注册）→ search_screen（单筛，记录选文理由，不做 PRISMA 计数）→ read_extract → synthesize → argue → draft → revise → audit → export | V-PRISMA、V-STATS、G-PREREG | Introduction（语境 → 问题 → 回应）, 主题分节, Gaps & Agenda, Conclusion, Disclosure |
| `research_proposal` | protocol（轻量版，同上）→ search_screen（单筛）→ read_extract → synthesize → argue → **design（写出并冻结 `design` 实体：方法设计、对照、测量、分析计划，每项都关联到假设）** → draft → revise → audit → export（**没有实验结果**） | V-PRISMA、V-STATS、G-PREREG | Background & Significance, Gap, Research Question & Hypotheses（含证伪条件）, Proposed Methods（计划中的工作，用 `[[plan:dsg_…]]` 标记）, Expected Outcomes & Alternatives, Risks, Disclosure |

---

## 8. 角色、驱动与 MCP 工具

### 8.1 角色

**默认驱动分配原则** `[决定]`：生成和审查分属不同厂商；批量粗筛用便宜的云端模型。**2026-11-09 你决定：默认驱动全部改走 omp_gateway**（用 omp 自己的登录，避开 Claude Code / Codex CLI 的用量限额）：下表中 “Claude” = `omp_gateway:anthropic/claude-haiku-5-5`，“Codex” = `omp_gateway:openai-codex/gpt-6.1-sol`；claude_cli、codex_cli 仍可选。

| 角色 | 职责 | 默认驱动 | 可用工具（MCP role） |
|---|---|---|---|
| Lead | Workbench 聊天中与你对话的 agent（任何 MCP 客户端接入时，是那个客户端里的 agent） | Claude（多轮会话，见 §8.2） | 全部读取；状态工具；可以列出、解释检查点，**不能回答硬性人类检查点**。**写权限按阶段开放**：在 intake 到 question 阶段（D1）可以用 Scout、Reader、GapMiner、Ideator 的工具，**包括浏览器工具**；进入 protocol 及之后的阶段，流水线写工具由编排器的角色负责，Lead 只能做读取和状态查询 |
| Scout | 检索、引文扩展、去重；**浏览器检索**（图书馆、Google Scholar、PubMed 网页） | Claude | search_*, browser_search, source_add |
| Screener-A / Screener-B | 双人独立筛选 | A = Claude，B = Codex | 读 source 元数据和摘要；screening_record |
| Reader | 获取全文（API 渠道优先，其次通过浏览器经图书馆获取）、写笔记、提取数据 | Claude | source_fetch_fulltext, browser_fetch_fulltext, text_read, note_add, extraction_set |
| GapMiner | 五类空白挖掘 | Claude | 读取全部；gap_add |
| Ideator | 提出 topic、RQ 和方法设计 | Claude | 读取；topic_add, rq_propose, hypothesis_add, design_set |
| Arguer | 组织 claim、证据、论证边 | Claude | 读取；claim_add, evidence_link, edge_add |
| Skeptic | 红队：提异议、找反证 | Codex | 读取；search_*；objection_add |
| SimReader | 按 `audience` 扮演目标读者质询 | Codex | 只读，输出质询列表 |
| Writer | 起草和修改各节 | Claude | 读取；draft_write |
| Judge | 独立意见，用于支持度判定和检查点投票 | 三个角色（2026-11-07 你决定；2026-11-09 改走 omp_gateway）：`judge` = `omp_gateway:openai-codex/gpt-6-luna`（厂商 openai）、`judge_2` = ollama cloud `deepseek-v4.1-flash`（厂商 deepseek）、`judge_3` = `omp_gateway:anthropic/claude-haiku-5-5`（厂商 anthropic）；与 claim 作者同厂商的评审自动跳过 | 无工具，输入内联 |
| Triage | 批量相关性粗筛、关键词抽取 | ollama cloud（deepseek-v4.1-flash） | 无工具，输入内联 |

**驱动可以替换：** 每个角色的驱动在 `config.toml` 里配置，也可以用 `rk driver set <角色> <驱动>` 或 Workbench 的“设置 → 模型”（Settings → Models）选择；`rk models` 列出每个驱动能跑的模型、厂商和当前是否可用。配了 API key 时，可以把任意角色换成 `anthropic_api` 或 `openai_api`。**omp_gateway 是默认驱动**（2026-11-07 成为普通驱动，2026-11-09 成为默认）：装了 omp 并已登录就能用；`omp models --json` 列出的每个聊天模型都可以写成 `omp_gateway:<provider>/<model>`。omp 的模型回退（`retry.modelFallback`，默认开启）会在所请求的模型失败（没有凭据、限流、服务故障）时改用用户 `retry.fallbackChains` 里的模型作答；驱动启动 `omp auth-gateway stdio` 时把随包的设置覆盖文件 `rk/llm/chat/omp_no_fallback.yml`（`retry.modelFallback: false`）追加到 `PI_CONFIG_FILES` 末尾，所以失败就是失败（2026-11-09 实测：用户覆盖文件重新打开回退时，请求无凭据的 `openai/gpt-4o-mini` 仍返回 401），同一模型的重试照旧。回复里的模型与所请求的不同时，这次调用仍算失败（重试），角色不会悄悄换厂商。厂商未知的模型可以做生成方，但永远不能做审查方，设置页会注明。默认驱动需要装好并登录 omp（换成 claude_cli、codex_cli 或 API 驱动则不需要）；默认驱动都不用本地模型。唯一的约束不变：同一个 claim 的生成方和审查方必须属于不同厂商。

**"厂商"按模型的研发方认定，不按托管方** `[决定]`。映射表写在 `llm/vendors.toml` 里，可以修改：
- `claude_cli`、`anthropic_api`、`omp_gateway:anthropic/*` → anthropic
- `codex_cli`、`openai_api`、`omp_gateway:openai-codex/*`、`omp_gateway:openai/*` → openai
- 其余按模型族，匹配模型 id（`llm/vendors.toml` 的 `families`），用于 `ollama:<id>` 和 omp_gateway 的 `anthropic`、`openai`、`openai-codex`、`ollama-cloud`、`ollama` 主机：`claude*` → anthropic，`gpt-<数字>*`、`gpt-oss*`、`codex*` → openai，`deepseek*` → deepseek，`gemini*`、`gemma*` → google，`qwen*` → alibaba，`llama*` → meta，`mistral*` → mistral，`kimi*` → moonshot，`glm*` → zhipu，`minimax*` → minimax；其他主机上的模型须带该厂商的组织名（如 `omp_gateway:openrouter/deepseek-ai/…`），否则算未知；用户上传的命名空间模型（`ollama:someuser/…`）也算未知
- 未在映射表中的模型视为未知厂商，**不能**作为审查方。

默认配置下，生成方是 anthropic，三个评审里 anthropic 的 `judge_3`（Haiku）被跳过，由 openai（`judge`）和 deepseek（`judge_2`）判定；生成方是 openai 或 human 时，跳过的是同厂商的那一个或一个也不跳过。只剩一个有效评审时（例如 codex 不可用），仍满足"不同厂商"，但第二意见只有一票，按"一票不足以通过"的保守规则处理。

### 8.2 模型提供层（llm/）与角色执行

```python
class Provider(Protocol):
    name: str          # v1："claude_cli" | "codex_cli" | "anthropic_api" | "openai_api" | "ollama"
                       #     | "omp_gateway" | "human"
    vendor: str        # 按 llm/vendors.toml 解析出的模型研发方
    kind: Literal["agentic_cli", "chat_api", "human"]
    def available(self) -> Availability: ...        # 已登录或有 key、版本信息、失败原因
    def run(self, spec: TaskSpec) -> TaskResult: ...                 # 单次任务（角色执行）
    def open_chat(self, spec: ChatSpec) -> ChatSession: ...          # 多轮会话（Lead 聊天）

class ChatSession(Protocol):
    def send(self, user_message: str) -> Iterator[ChatEvent]: ...    # 流式事件：文本增量、工具调用、工具结果、结束
    def close(self) -> None: ...

@dataclass
class TaskSpec:
    task_id: str; role: str; instructions: str            # 来自 roles/<role>.md + 任务参数
    inputs: dict                                          # 内联数据或实体 ID
    output_schema: type[BaseModel]                        # Pydantic 模型 → JSON Schema
    tools: list[str]                                      # 该角色允许的工具名（空表示没有工具）
    max_turns: int; timeout_s: int

@dataclass
class TaskResult:
    task_id: str; ok: bool; output: BaseModel | None; raw: str
    provider: str; model: str | None; duration_s: float; error: str | None
```

**两种执行方式：**

- **`agentic_cli`**（claude_cli、codex_cli）：CLI 自己运行工具循环，通过临时启动的 `rk mcp --role <role> --project <slug>` 调用 Kernel 的工具。
  - claude_cli：

    ```
    claude -p <prompt> --output-format json --json-schema <schema>
           --strict-mcp-config --mcp-config <tmp.json> --allowedTools "mcp__rk__*"
           --max-turns N --no-session-persistence
    ```

    工作目录是临时空目录；**不能用 `--bare`**；读取 `structured_output`，再用 Pydantic 校验。
    Lead 的多轮会话（`open_chat`）改用 `--output-format stream-json` 并保留会话；从第二轮起每轮用 `--resume <session_id>` 续接，流式事件直接转发给 Workbench。
  - codex_cli（多轮会话用 `codex exec resume <SESSION_ID> <PROMPT>`，已用本机 0.166.0-alpha.6 的 `--help` 确认；多轮时不加 `--ephemeral`）：

    ```
    codex exec --json --output-schema <file> --sandbox read-only --ephemeral
               --skip-git-repo-check -c mcp_servers.rk.command=… -C <tmp>
    ```

    读取最后一条消息，再用 Pydantic 校验。
  - 这里的 MCP 是 CLI 与 Kernel 之间的内部通道，不需要你另外安装 MCP 客户端。
- **`chat_api`**（各家 API、Ollama、omp_gateway）：由 Kernel 自己的 `agent/` 循环执行。循环调用模型的工具调用接口，在进程内执行同名工具；同一套工具注册表、同一套权限。
  - Anthropic 用 Messages API；OpenAI 用 Responses 或 Chat Completions；Ollama 用 `/api/chat`（本地带 `format` schema）。
  - omp_gateway 通过 `omp auth-gateway stdio` 走 `/v1/chat/completions`。
  - 最终输出都要提取 JSON、去掉 markdown 代码围栏、用 Pydantic 校验，失败最多重试 2 次。
- **human**：创建 checkpoint，返回 `waiting_human`。

**所有提供者都必须做到：**
- 每次调用的输入、输出、模型、耗时都写入事件。
- 输出不能通过校验就记为失败，并按重试策略处理。
- 启动时检查 CLI 的版本，与固定版本不一致时发出警告。
- 速率上限按提供者分别计算（§7.2）。
- **Kernel 里不出现任何第一方 OAuth client ID，也不读取、不转发任何 CLI 的凭据文件。**

### 8.3 空白挖掘（GapMiner）的五类方法

每类都有确定性的核查步骤，挖出的 gap 必须 `verified=true` 才能进入 topics。`verified` 的含义仅限于"证据链经过核查"（引语逐字一致、支持度判定通过、针对性检索已执行），**不等于确认这是真实的研究空白，也不等于确认新颖**。gap 和 topic 都只是候选：P2 中要由你确认；P1 中写进论文时必须表述为"在检索范围内未发现"，并在披露段注明检索范围以及"未经人审"。

1. **contradiction（矛盾）**
   - 在同一聚类的 claim 之间，由 LLM 两两找出候选矛盾对（借鉴 PaperQA2 的矛盾检测）。
   - 用 V-SUPPORT 的矛盾判定复核。
   - 双方原文都要以 quote 笔记的形式保存。
2. **stated_limitation（作者自述的局限与未来工作）**
   - 从论文的 discussion、conclusion、limitations 段抽取局限和"未来工作"表述。书中建议过"读来源的最后几页"。
   - 必须是逐字一致的 quote。
   - 还要检索"后来有没有人做过"。
3. **citation_distortion（引文失真）**
   - 场景：论文 A 声称 X，并引用了 B。
   - 取回 B 的原文，用 V-SUPPORT 判断 B 是否真的支持 X。
   - 判为不支持或矛盾，且两个厂商的模型意见一致，才记为 gap。
   - 这一类直接对应你说的"发现不准确之处"。
4. **retracted_still_cited（撤稿后仍被引用）**
   - 撤稿文献（Crossref `updated-by` ∪ OpenAlex `is_retracted`）在撤稿日期之后仍然被引用。
   - 用 OpenAlex `cites:` 加发表年份过滤找出。
5. **evidence_map_hole（证据图空格）**
   - 按 PICO 或"材料 × 方法 × 性质"之类的维度建一个矩阵，统计每个格子的研究数量。
   - 空格必须经过一次针对性检索确认仍然为空。

**"已经有人做过吗"检查（用于 `question` 门，以及 D2 的自动回边）** `[决定]`：
1. Scout 至少用 3 种检索式、至少 2 个数据源检索，取最接近的 top-10 已有工作，检索式和结果写入日志。
2. 两个不同厂商的 Judge 对每篇工作分别判断："它是否已经回答了同一个 RQ？"——即同一研究对象（人群、体系或材料）、同一干预、方法或变量、同一结局或性质。每篇必须引用原文片段作为依据。
3. 判定规则：
   - 只要有任何一篇被两家都判为"已回答"，结果就是"已有人做过"：D2 自动退回 `topics`，D1 提示你修改。
   - 两家意见不一致时：P2 交给你决定；P1 按保守原则处理，当作"已有人做过"。
   - 全部被两家判为"未回答"时，结果只记为"在检索范围内未发现"，**不代表新颖**。

### 8.4 工具接口（进程内注册表；CLI 驱动经内部 MCP 通道调用；外部 MCP 客户端可选接入）

- 命名统一用 `rk_` 前缀；所有写工具都要求带 `idempotency_key`。
- **project 与流程：** `rk_project_create`, `rk_project_status`, `rk_stage_status`, `rk_stage_advance`（运行门；不通过就返回失败明细，不会推进）, `rk_checkpoint_list`, `rk_checkpoint_answer`, `rk_flag_injection(source_id, snippet, reason)`
- **检索：** `rk_search_works(query, sources[], filters)`, `rk_get_work(identifier)`, `rk_expand_citations(source_id, direction, limit)`, `rk_snippet_search(query)`
- **来源与全文：** `rk_source_add(identifier, discovered_via, stance?)`, `rk_source_fetch_fulltext(source_id)`, `rk_text_read(source_id, page?, start?, end?)`, `rk_text_find(source_id, query)`
- **笔记与提取：** `rk_note_add(...)`（返回结果里带即时验证）, `rk_extraction_set(source_id, field, value, note_id)`
- **论证：** `rk_claim_add`, `rk_evidence_link`, `rk_edge_add`, `rk_objection_add`, `rk_hypothesis_add`, `rk_rq_propose`, `rk_topic_add`, `rk_gap_add`, `rk_protocol_set`（protocol 冻结前可以修改）, `rk_design_set`（research_proposal 专用，由 Ideator 编写）
- **验证：** `rk_verify(target_type, target_id, verifiers[])`, `rk_verify_draft(draft_id)`
- **写作：** `rk_draft_write(section, markdown)`, `rk_draft_get(section, version?)`, `rk_export(format)`
- **浏览器：** `rk_browser_search(site, query, max_results)`（返回结构化的结果列表并写入 `browse_log`）, `rk_browser_open(url)`, `rk_browser_fetch_fulltext(source_id)`（经图书馆或出版商页面下载 PDF，作为附件入库）, `rk_browser_snapshot(tab)`（截图 + 可访问性树，供通用导航使用）, `rk_browser_act(tab, action)`（点击、输入、翻页，只用于通用导航兜底）, `rk_browser_request_human(reason)`（请你登录或处理验证码）
- **文献库：** `rk_library_search(query, filters)`（SQLite FTS5 全文检索）, `rk_library_tag`, `rk_library_collect`, `rk_library_import(file|text)`（BibTeX/RIS/CSL-JSON）, `rk_library_export(format, collection?)`, `rk_library_attach(source_id, path)`

所有工具都在 `tools/` 注册表里定义一次：Pydantic 输入输出 + 允许的角色 + 允许的阶段。`chat_api` 循环、可选 MCP 服务器和 Workbench 都使用这一份定义。

### 8.5 内置浏览器（browser/）

- **引擎：** Playwright `chromium.launch_persistent_context($RK_HOME/browser-profile, headless=False)`，**由 `rk serve` 独占持有**。`rk serve` 启动时打开这个窗口，第一个标签页就是 Workbench。所有浏览器工具都在 `rk serve` 进程内执行；CLI 驱动的内部 MCP 代理（`rk mcp --role …`）只把调用转发给 `rk serve`，自己不打开浏览器。
- **隔离 Workbench：** agent 的浏览器工具只能操作由它们自己打开的标签页（按页面 ID 白名单）。agent 页面通过路由拦截，禁止访问 Workbench 所在的源（`127.0.0.1:<port>`）。Workbench 标签页不会出现在任何工具返回的结果里。
- **登录：**
  - setup 向导或检查点会请你在这个窗口里登录图书馆（EZproxy、OpenAthens、Shibboleth 或机构 VPN 网页入口）。登录状态保存在 profile 里。
  - 当 agent 检测到登录过期（被重定向到 SSO 页面）时，暂停浏览器任务并请你重新登录（`login_wait`）。
  - Kernel 不保存你的图书馆密码，也不代你填写。
- **站点适配器** `[决定]`：每个常用站点一个适配器，用稳定的 DOM 选择器把结果解析成结构化记录，并带合法性自检；选择器失效时自动降级为通用导航。
  - Google Scholar
  - PubMed 网页
  - Europe PMC 网页
  - 学校图书馆的发现系统（Primo、Summon、EBSCO Discovery，先支持你学校用的那一种）
  - DOI 落地页 → 出版商页面上的 PDF 链接（ScienceDirect、Springer、Wiley、ACS、RSC、Nature、IEEE、ACM 等，按常见模式匹配）
- **通用导航（兜底）：** Scout 或 Reader 通过截图和可访问性树（`rk_browser_snapshot`），再加 `rk_browser_act` 逐步操作。每个任务有步数上限。
- **无 key 工作流（浏览器是一等数据源，不是 API 的附属）：** 不配置任何 key 时，以下流程必须能完整走通。
  1. 浏览器检索（图书馆检索系统、Google Scholar、PubMed 网页）。
  2. 打开文章页，从页面元数据中解析条目：学术出版商普遍提供 `citation_*`（Highwire）、Dublin Core、JSON-LD、COinS 等元数据标签；适配器还会解析结果列表。
  3. 经图书馆下载 PDF。
  4. 入库并建立规范文本层。
  5. 核验引语。

  元数据补全与校验（V-REF）**优先使用不需要 key 的接口**：Crossref 按 DOI 单条查询、OpenAlex 单条查询（免费）、PubMed E-utilities、arXiv。key 只会提高检索额度和覆盖面，不是前提。

  没有 DOI、PMID 或 arXiv ID 的条目（例如书籍章节、会议页面）也可以入库，V-REF 状态记为 `page_metadata_only`：可以作为背景阅读和笔记；要在终稿中作为 claim 的证据，必须先通过 ISBN 或目录记录核验元数据，或者由你在文献库中确认。可选的 Zotero translation-server 能提高这类页面的解析质量。
- **元数据冲突：** 页面元数据与 API 返回的元数据不一致时，以 API 为准，并记录差异；两者都没有时，以页面元数据为准，并标记为 `page_metadata_only`。
- **人类节奏与上限** `[决定]`（可在设置里调整，默认值偏保守）：

  | 对象 | 默认上限 |
  |---|---|
  | Google Scholar 查询 | 两次之间至少间隔 20–45 秒（随机），每天 ≤60 次 |
  | 每个出版商域名的 PDF 下载 | 两次之间至少间隔 30 秒，每天 ≤40 篇 |
  | 全部浏览器 PDF 下载 | 每天 ≤150 篇 |
  | 浏览器任务并发 | 同一时间只有 1 个 |

  遇到验证码或"异常流量"页面时，立即暂停该站点并发 `captcha_wait` 检查点，由你在窗口里处理。**永远不尝试自动破解验证码。**
- **合规：** 浏览器取得的全文只存在本机项目里，用于你个人的研究；导出包默认不包含这些 PDF，只包含元数据和快照哈希。
- **实现记录（Plan 3，2026-11-06）：**
  - **agent 标签页只能访问公网**（在"隔离 Workbench"之上扩展，审查后决定）：每个 agent 标签页有一个 CDP `Fetch` 拦截器，每个请求和每一跳重定向在发出前都检查目标，用 Chromium 自己的网络栈继续（出版商的人机检测看到的是正常浏览器）。禁止访问 Workbench、loopback、私有、链路本地、保留和 CGNAT 地址；不加载来自其他主机的 frame；屏蔽 service worker；弹窗的第一个页面在拦截器就位后重新加载。校园内网主机可加入 `browser_allow_hosts`。你自己的标签页不受限制。
  - **选择器实测（2026-11-06）：**
    - PubMed、Primo VE、Summon 按真实页面确定；
    - Europe PMC 对 headless Chromium 返回 Cloudflare 验证页，适配器改用 `/article/<来源>/<ID>` 链接解析；
    - Google Scholar 对 headless Chromium 返回"automated queries"页面（结果页同一 URL），检测器把它当作验证码；
    - 隐形 reCAPTCHA v3 不算拦截。
  - **doi.org 同样对 headless 浏览器返回 Cloudflare 验证页。** 有窗口的正常浏览器是否通过 `[推断]`：需要你实际登录后确认。

### 8.6 文献库（library/）

- **每个项目一个库**，复用 `source`、`attachment`、`collection`、`note` 表。条目有 Zotero 风格的类型和字段；附件可以在 Workbench 里直接打开（PDF 用浏览器自带的阅读器）。
- **自动建好的分组：** 检索命中、已筛选纳入、已排除（附理由）、反方证据、已撤稿 / 有关注声明、待你处理。
- **全文检索**（SQLite FTS5，覆盖标题、摘要、笔记、全文文本层）；标签、评分、阅读状态。
- **导入导出：** BibTeX、RIS、CSL-JSON。可以导入你现有 Zotero 导出的文件；导出可以直接拖进 Zotero。
- **"保存到文献库"（类似 Zotero Connector）：** 你在内置浏览器里看到想要的文章时，在 Workbench 中点"保存当前页"（也可以用快捷键），系统会解析当前标签页的元数据，下载可以获取的 PDF，生成网页快照，并把条目放进当前分组，标记为 `added_by=human`。agent 的 `rk_browser_fetch_fulltext` 走同一条入库路径。
- 每个条目都显示验证状态徽标：V-REF、撤稿状态、全文是否已获取、有多少条引语已核验。
- 后续计划（不在 v1）：通过 Zotero Web API（需要你的 Zotero key）双向同步。

### 8.7 Workbench（ui/）

- FastAPI + 无构建步骤的静态前端（原生 HTML/JS + 一个轻量组件库，具体由实现者决定）；用 WebSocket 推送进度。
- **页面：**
  - **项目**：新建、切换项目，选择模式和论文类型。
  - **聊天**：与 Lead agent 对话，中文或英文；它调用的工具和结果可以展开查看。
  - **文献库**（§8.6）。
  - **阶段与进度**：状态机、当前任务、门的结果、回边记录。
  - **检查点**：待你回答的问题；topic 审核卡片带证据链接。
  - **草稿**：句子级来源标记可以悬停查看并跳转到原文定位。
  - **设置与 key 向导**（§8.8）。
- 只监听 `127.0.0.1`，每次启动生成一个随机访问令牌。令牌只放在 Workbench 页面里，agent 的工具接口拿不到。
- 提供运行控制：启动 / 暂停 / 继续 / 停止某个项目的运行，与 `rk run`、`rk pause` 等价。
- **Plan 3 的偏差（2026-11-06，已在计划中说明）：**
  - 运行控制和阶段图随编排器放到 Plan 4；"阶段与进度"页目前显示任务、检查点和事件流。
  - topic 审核卡片放到 Plan 4；目前检查点用通用的选项和文字回答。
  - 草稿预览读取 `artifacts/drafts/<section>.v<N>.md`，由 Plan 5 的 `rk_draft_write` 写入。
  - 访问令牌实现为：每次启动生成一次性登录码，换取 HttpOnly、SameSite=Strict 的会话 cookie；写操作还要求 `Origin` 一致。保存的网页快照以 `sandbox` CSP 提供。

### 8.8 首次运行向导（setup/）

`rk setup`，或在 Workbench 的设置页，逐项引导：

1. **邮箱：** 用于 Crossref `mailto` 和 Unpaywall。
2. **OpenAlex key：** 在内置浏览器里打开注册页，说明步骤；你注册、点激活邮件后把 key 粘贴进来。向导用一次单条查询验证，并显示 `x-ratelimit-*` 额度。
3. **Semantic Scholar key：** 打开申请表单页，说明填写要点（用途写"个人学术研究"）。key 通常要等邮件下发，向导会把它记为"待补"，并提醒你稍后回来填。
4. **LLM 提供者：** 检测 `claude` 和 `codex` 是否已登录，没登录就提示你在终端执行 `claude` / `codex login`；可选填写 API key；检测 Ollama 本地和云端是否可用。
5. **图书馆登录：** 打开你学校图书馆的入口页，请你登录，再用一篇付费墙文章测试能否拿到全文。
6. **生成并显示能力清单：** 哪些数据源、提供者、全文渠道可用，以及对应的额度。

---

## 9. 验证器与硬门

### 9.1 验证器

词面和元数据类验证器（如 V-PARA、V-REF）的阈值是初始值，可在金标集上校准；校准后的值写进 `config.toml` 并记录版本。V-SUPPORT 自 2026-11-07 起没有阈值。

| ID | 检查内容 | 实现 | 判定 |
|---|---|---|---|
| V-REF | 文献存在，元数据一致 | 标识符按注册机构路由：Crossref、DataCite、OpenAlex、PubMed、arXiv；标题 `token_set_ratio` ≥90、第一作者姓氏一致、年份相差 ≤1 | pass / mismatch / not_found |
| V-RETRACT | 撤稿状态 | Crossref `updated-by[].type` ∪ OpenAlex `is_retracted` | retracted、withdrawn：**不能作为证据**，只能作为被讨论的对象，且必须标注；EoC：警告；corrected：附注 |
| V-QUOTE | 引语逐字一致 | 在规范文本层上，先用规范化键匹配：NFKC、连字展开、统一各种破折号和引号、合并空白，行末连字符既试"保留"也试"删除"两种变体；再用 `rapidfuzz.partial_ratio_alignment` 定位 | 规范化键完全一致才算 pass，并返回页码和字符区间；否则 fail，同时返回最接近的片段 |
| V-PARA | 转述不过近（书中的"手指测试"） | **只做词面相似度检查**：与对应原文片段比，最长连续共有词 ≤8；4-gram 包含率 ≤0.35；`token_set_ratio` ≤85。它能发现近乎照抄，但发现不了意思走样；转述是否忠实原意由 V-SUPPORT（转述作 claim，原文片段作证据）判定，抽样交人复核 | pass / too_close |
| V-HEDGE | 措辞强度单调 | 词表分 4 级：0 推测（may, might, possibly）、1 倾向（suggest, indicate, likely）、2 断言（show, find, demonstrate）、3 绝对（prove, establish, always, all, never）；claim 的等级 ≤ 其证据笔记中的最高等级；全称量词需要证据覆盖 | pass / overstated |
| V-SUPPORT | 证据是否支持 claim | **只用云端评审，没有第一层模型**（2026-11-07 你决定：去掉第一层 MiniCheck，判定只来自三家云端评审；默认不用任何本地模型）。三个评审角色 `judge`、`judge_2`、`judge_3`，默认是 `omp_gateway:openai-codex/gpt-6-luna`（openai）、`ollama:deepseek-v4.1-flash:cloud`（deepseek，由 Ollama Cloud 托管）、`omp_gateway:anthropic/claude-haiku-5-5`（anthropic）（2026-11-09 由 codex_cli、claude_cli 改走 omp_gateway），可以换成任意厂商已知的驱动。每个评审各自给出结构化判定 {supported, partial, unsupported, contradicted}；判 supported、partial、contradicted 时须逐字引用证据，引文在证据里找不到的意见记为 uncertain。评审的厂商必须已知、不是 human、不同于 claim 作者的厂商，也不同于另一个已采纳评审的厂商；不符合的评审被跳过，不计票（厂商未知、是 human 或与作者相同记为 `same_or_unknown_vendor`，与已采纳评审同厂商记为 `duplicate_vendor`） | 有效评审（已采纳且调用成功）至少 `support_min_judges` 个（默认 2），且**全部**给出同一个标签时，取这个标签：**supported**（V-NUM 也须通过）、**unsupported** 或 **contradicted**；其余一律 **uncertain**（包括任一评审意见不同、判 partial、只有一个有效评审），触发 `uncertain_support` 检查点或降级。没有阈值，也不校准；`rk eval support` 在 SciFact dev 上按生成方场景（以 anthropic 为主，另有 openai、human）报告覆盖率和各判定的精确率，见 `docs/eval/support-2026-11-07-judges.md`（60 对：anthropic 场景覆盖率 0.533，supported 6/6；用切换前的驱动 `codex_cli:gpt-6-luna`、`claude_cli:claude-haiku-5-5` 测得，模型相同）。2026-11-06 的两层规则和联合阈值作废 |
| V-NUM | 数字出自证据 | 抽出 claim 和句子中的数字、百分比、单位，统一规范。数字必须出现在关联笔记的原文片段中，或者等于句子标记所引用的 `run`/`fig`/`stat` 产物中的值 | pass / ungrounded_number |
| V-COVER | 句子来源标记覆盖 | 分句后，每句必须带**至少一个**来源标记（标记语法 `[[kind:id]]`），一句里可以同时出现多个：<br>① `[[clm:clm_…]]` 来自文献的 claim；<br>② `[[run:evt_…]]` 或 `[[run:prt_…]]` 描述系统自身做过的研究操作（检索、筛选、提取），指向事件日志中的具体事件或已冻结的 protocol；<br>③ `[[fig:fig_…]]` / `[[stat:stt_…]]` 计算结果，指向由代码生成的产物（含数据哈希）；<br>④ `[[plan:dsg_…]]` 或 `[[plan:hyp_…]]` research_proposal 中计划要做的工作；<br>⑤ `[[inf]]` 作者推断；<br>⑥ `[[ck]]` 常识（不超过 5%，且不能含数字）。<br>②—④ 的目标必须存在且哈希一致。句中的每个数字都必须至少能被句内某一个标记的目标覆盖（见 V-NUM）。导出时：`clm` 转为对其证据来源的 citeproc 引用；`run`、`fig`、`stat`、`plan` 转为指向正文中方法部分、图或表的交叉引用；`[[inf]]` 转为推断性措辞 | pass / fail |
| V-BALANCE | 争议点有反方 | claim 被标为 `contested`，或存在有来源支撑的异议时，所在小节必须写出反方观点并引用回应 | pass / one_sided |
| V-PRISMA | 流程数字自洽 | 识别数 − 重复数 = 筛选数；筛选数 − 排除数 = 全文评估数……全部算术一致；清单条目用 LLM 辅助逐项检查 | pass / fail |
| V-STATS | 统计可重算 | 估计方法不写死，由 protocol 中预先指定的分析计划决定：效应量类型、模型、异质性处理、置信区间方法、敏感性分析（候选默认值为 REML 随机效应 + Hartung–Knapp 置信区间，`[待核实]`是否与当前 Cochrane Handbook 推荐一致）。代码从 extraction 表重算，结果必须与正文和图表一致。统计模块作为独立模块，用已发表 meta-analysis 的数据集做验证后才能启用 | pass / mismatch |
| V-NOVELTY | 新颖性（**低可信**） | 用 OpenAlex 和 S2 检索 top-k 最近的工作，再由两个厂商的 LLM 对比；输出"最接近的已有工作 + 差异点" | **只提供参考，永远不自动判定"新颖"**；P2 交人判断；P1 需两厂商一致，且在披露段标注"未经人审" |

### 9.2 硬门（任何模式都不能关）

- G-REF：V-REF 和 V-RETRACT 应用于终稿引用的全部文献。
- G-QUOTE：V-QUOTE 应用于全部 quote 笔记和稿件中的全部引语。
- G-COVER：V-COVER 和 V-NUM。
- G-SUPPORT：终稿中每个 `[[clm:]]` 标记的 claim 都必须是 supported。uncertain 的处理方式：P2 由你裁决；P1 只能删除，或者改写成更窄、更弱的新 claim 并重新验证为 supported。投票不能替代这一判定。
- G-HEDGE：V-HEDGE。
- G-DISCLOSE：`disclosure.md` 存在且完整，包括：
  - 使用了哪些模型、做了什么
  - 人类做出过哪些决定
  - 哪些结论未经人审
  - 局限性
  - 预注册的偏离
- G-PREREG（systematic_review 和 scoping_review）：protocol 冻结在先，偏离全部有记录。

### 9.3 安全（吸取 OpenClaw 的教训）

- **抓取内容只当数据。**
  - 入库时去除隐藏文本：零宽字符、HTML 注释、`display:none`，以及 PDF 中白色或极小字号的文字（按 pdfium 字符属性过滤）。
  - 角色提示词明确规定：来源文本中的任何指令都要忽略，并作为可疑内容上报（`rk_flag_injection`）。
- **最小权限：** 工具注册表按角色和阶段授权；内部 MCP 代理按 `--role` 只暴露该角色的工具。Reader、Skeptic 没有写草稿的权限。CLI 驱动的工作目录是临时空目录。浏览器工具只给 Scout、Reader，以及 D1 阶段的 Lead；agent 页面禁止访问 Workbench 的源；通用导航禁止在非检索类站点上提交表单（例如付款、发帖、修改账户）。**硬性人类检查点没有任何 agent 可用的回答工具**（§5.3）。
- **不安装第三方 skill 或插件。** 角色提示词全部放在仓库里，纳入版本控制。
- **凭据：**
  - Kernel 不保存、不读取任何订阅凭据。订阅只通过官方 CLI 自身使用；API key 只在你主动填写时保存。
  - 图书馆密码不经过 Kernel，只存在浏览器 profile 的 cookie 里；导出包和日志里都不包含 profile。
  - API key 和邮箱放在 `config.toml`，权限 0600；也可以用环境变量。
  - key 和邮箱不写入事件日志和导出包，导出时自动脱敏。

---

## 10. 评测与验收

**评测先行：** Plan 1 开始就要建金标集，每次合并代码都跑回归。

| 层级 | 数据 | 指标与目标（v1） |
|---|---|---|
| 单元金标 | 用 30 段 OA 原文（覆盖化学、生物、医学、CS）**程序化生成**：正例是原文的逐字子串，反例是扰动版本（改词、改数字、跨行拼接、加强措辞），所以标签由构造方式决定，不依赖任何模型 | V-QUOTE / V-PARA / V-HEDGE 准确率 100% |
| 引用陷阱集 | 伪造 DOI；真 DOI 配错标题；已撤稿文献（Wakefield 1998 `10.1016/S0140-6736(97)11096-0`；Surgisphere 2020 `10.1016/S0140-6736(20)31180-6`）；EoC；更正 | 检出率 100%；撤稿文献用作证据的次数 0 |
| 支持度评测 | **只用外部人工标注数据**，避免让系统给自己打分：DeepFactBench（MIT，专家审计标签）和 SciFact（生物医学 claim–摘要对，人工标注；数据许可证 `[待核实]`，仅用于本地评测）；另外可以加你标注的 STEM 样本（见 §13） | supported 判定的精确率 ≥0.95，覆盖率（未弃权的比例）≥0.70；报告混淆矩阵 |
| 文献问答 | LitQA2（199 题，不需要评委） | 与 PaperQA2+Ollama 基线比较；"信息不足"的回答精确率 |
| 引用质量 | ReportBench（引用精确率和召回率是确定性匹配） | 高于开源 deep research 基线 |
| 端到端 | **系统综述复现集**：挑 6 篇 PMC OA 的系统综述（每个领域至少 1 篇），ground truth 是它们纳入的研究和主要结论 | 纳入研究的召回 ≥0.85；主要结论方向一致；全部硬门通过 |
| 报告质量（内部参考） | ResearchRubrics、DRACO 子集，用 `claude -p` 或 `codex exec` 做评委 | 只作趋势参考，不与公开榜单比较 |

DeepResearch Bench、DeepFact 的官方分数和 ReportBench 的事实性部分需要付费 API 评委，v1 不跑（尽调 D 节）。

---

## 11. 失败与边界情况（实现时必须处理）

1. **全文获取顺序** `[决定]`：Europe PMC fullTextXML / PMC BioC → arXiv HTML → Unpaywall 或 OpenAlex 给出的 OA PDF 直链（直接从出版方或仓储下载，不花 OpenAlex 额度）→ **内置浏览器经图书馆或出版商页面获取**（§8.5，受人类节奏和每日上限约束）→ OpenAlex 付费的 content/TEI（默认关闭，需要在配置里打开并设预算）。本机有 Java 27，本地 GROBID 作为可选项，兼容性 `[待核实]`。都拿不到时：只能使用摘要层级的证据，这类证据在判据上自动标为 `sufficient=low`；也可以手动上传 PDF（`rk library attach <id> <pdf>`）。
2. **扫描版 PDF 或没有文本层：** 用 docling 做 OCR，OCR 文本标记为低置信，引语验证要求在附近再找到一次人工可查的定位。`[推断]`
3. **API 限流或预算耗尽：** 读取 `x-ratelimit-*` 响应头，按退避重试；预算耗尽时把相关任务挂起，并在 `rk status` 里显示原因，不会悄悄跳过。
4. **订阅 CLI 被限额或报错：** 驱动返回可重试错误；连续失败时切换到备选驱动，例如 claude 换 codex。但同一个 claim 的生成和审查仍然必须分属不同厂商；做不到时任务挂起。
5. **并发请求：** 只有 `rk serve` 写 SQLite（§6.0）。CLI、Workbench、内部 MCP 代理、外部 MCP 客户端的请求都在 `rk serve` 内排队执行，写操作带幂等键，同一个键只生效一次。另一个 `rk serve` 实例启动时，如果检测到 `serve.lock` 被一个仍存活的进程持有，就拒绝启动。
6. **中途崩溃：** 状态完全由事件日志决定。`rk serve` 重启时恢复所有项目的运行：`running` 状态的任务标记为中断，然后按幂等键重新排队；处于 `paused_waiting_human` 或 `paused_budget` 的运行保持暂停。
7. **同一文献有多个版本**（预印本和正式发表版）：按 OpenAlex work 合并；引用时优先正式版，但笔记的定位以实际读取的那个版本快照为准。
8. **非英文文献：** v1 只纳入英文全文 `[决定]`；非英文文献在筛选阶段标记出来，排除理由写"语言"。
9. **验证码、异常流量页、SSO 过期：** 暂停对应站点的浏览器任务，发出检查点，由你在窗口里处理；同时继续做其他不依赖浏览器的工作。
10. **站点改版导致适配器失效：** 适配器自检失败（解析出 0 条，或字段缺失率过高）时，降级为通用导航，并记录告警；告警累计 3 次后，在 Workbench 中提示需要更新适配器。
11. **达到下载或检索上限：** 当天停止该渠道，任务挂起到第二天，或者改用其他全文渠道；在进度页上明确显示原因。
12. **官方 CLI 未登录或被限额：** 驱动报告不可用；若有备选提供者（API key、Ollama、omp_gateway），按配置切换，但仍要满足"生成与审查分属不同厂商"；否则挂起任务并通知你。

---

## 12. 里程碑（每个是一份独立计划，按顺序执行）

| 计划 | 范围 | 可演示的结果 | 粗估工期 `[推断]`，按 agent 实现计 |
|---|---|---|---|
| **Plan 1 基础层与文献库** | 仓库骨架、配置、存储（事件日志、哈希链、投影表、重建）、文献库核心（条目、附件、分组、标签、FTS5、BibTeX/RIS/CSL 导入导出）、数据源适配（OpenAlex、Crossref、Europe PMC/PubMed、arXiv、S2、bioRxiv）、HTTP 缓存与预算、全文管线（BioC、arXiv HTML、OA PDF、PDF 规范层）、V-REF / V-RETRACT / V-QUOTE / V-PARA / V-HEDGE、`note_add` 即时验证、工具注册表、CLI（含 `rk setup` 的命令行版本）、单元金标与引用陷阱集 | 用 CLI 建项目、检索、入库、取全文、写笔记：每条引用都有验证结果，每条引语都有页码定位；能导出 BibTeX 并导入 Zotero | 4–6 天 |
| **Plan 2 模型提供层与验证** | 提供者层（claude_cli、codex_cli、anthropic_api、openai_api、ollama、omp_gateway、human），包括单次任务和多轮会话（Claude 用 `--resume` + stream-json，Codex 用 `exec resume`，chat_api 用进程内循环）；厂商映射表；进程内工具调用循环；内部 MCP 代理与外部 MCP 接入；角色提示词框架；claim / evidence / edge / objection 实体；V-SUPPORT 判定（2026-11-07 修订：由原来的两层判定改为三家云端评审一致才定案，见 §9.1）；V-NUM；评测框架（LitQA2、DeepFactBench 和 SciFact 评测、ReportBench 引用部分） | 给一段文本加一组来源，输出逐 claim 的支持判定和混淆矩阵；同一个任务可以分别由 claude_cli、codex_cli、ollama 执行 | 5–7 天 |
| **Plan 3 内置浏览器与 Workbench** | Playwright 持久化浏览器、人类节奏调度器、验证码与登录转人工、站点适配器（Google Scholar、PubMed、Europe PMC、你学校的发现系统、DOI→出版商 PDF）、通用导航兜底、可选 translation-server；Workbench（聊天、文献库、检查点、进度、草稿预览、设置与 key 向导） | 在内置浏览器里登录你学校的图书馆；Lead 在聊天中按你的要求检索 Scholar 和图书馆，经图书馆下载付费墙 PDF，入库并核验引语 | 6–8 天 |
| **Plan 4 发现** | 编排器状态机（阶段、回边、预算、检查点挂起）；landscape；五类 gap 挖掘；topic 生成与审核；RQ 门；"已有人做过"检查；V-NOVELTY；D1、D2 两种模式 | D2：输入"钙钛矿太阳能电池稳定性"，产出 6 个带证据的 topic → 你在 Workbench 中审核 → 冻结的 RQ | 5–7 天 |
| **Plan 5 产出** | protocol 与预注册；检索与筛选（PRISMA）；提取；综合（统计模块经验证后才启用）；argue；design（计划书）；draft 与 revise；audit 与披露；导出（pandoc + citeproc + tectonic，英文论文）；P1、P2 两种模式；系统综述复现集 | 端到端产出 systematic_review、critical_review 或 research_proposal，附验证报告和可复现包 | 8–10 天 |
| Plan 6（之后，单独批准） | 计算型实证研究：代码实验沙箱、预注册、autoresearch 式循环、公共数据二次分析 | — | — |
| Plan 7（之后） | 与 Zotero Web API 双向同步；定时与消息推送；基于评测的自我改进 | — | — |

---

## 13. 需要你确认的事项

**已确认（2026-11-05 你的答复）：**
- 仓库私有（GitHub 私有仓库，账号 `9s94ttyfct-bit`）；**只在本仓库设置局部 git 身份** `9s94ttyfct-bit <273716724+9s94ttyfct-bit@users.noreply.github.com>`，不使用全局 `git config` 中的身份。
- 系统引导你申请 key（§8.8）。
- 不绑定 omp：omp_gateway 是默认驱动（2026-11-09 起；2026-11-07 起是普通驱动），不装 omp 时换成 claude_cli、codex_cli、Ollama 或 API 驱动照常运行。**开发测试中需要 LLM 的部分，使用 omp_gateway 调用 `openai-codex/gpt-6-luna`**。
- 内置浏览器（§8.5），可以登录图书馆，也可以像人一样检索。
- 支持度评测只用外部人工标注数据（DeepFactBench、SciFact）。
- 论文只用英文，交互中英文都可以。
- 每个项目一个类 Zotero 文献库（§8.6）。

**仍需你确认（都有默认值，不回答就按默认执行）：**
1. **订阅 CLI 的调用强度**：默认每小时 claude 30 次、codex 20 次。无人值守长跑时可能触发订阅限额；如果以后加了 API key，可以把批量角色迁过去。
2. **浏览器节奏与上限**：默认值见 §8.5，偏保守。如果你学校的图书馆对下载量有明确规定，以学校规定为准。
3. **你学校图书馆的发现系统**：默认先做 Primo，其次 Summon；告诉我学校名或图书馆网址，我优先适配那一种。
4. **Zotero translation-server**：默认作为可选组件，不装也能运行。它是 AGPL 体系的 Node 程序，只在本机运行，供你个人使用。

## 14. 留给实现者决定的

- 具体模块拆分和函数命名，在每份计划里定。
- 角色提示词的措辞。由评测结果驱动迭代。
- docling 什么时候启用（只在需要表格或章节结构时）。
- 景观图的聚类算法：OpenAlex topics + embeddinggemma-2 嵌入 + HDBSCAN 或 k-means 都可以。只要每个聚类都能给出代表作和引证即可。
- 站点适配器的具体选择器和自检阈值；通用导航的步数上限（默认 25 步）。
- Workbench 前端的具体组件库（要求：不需要构建步骤）。
