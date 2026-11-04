# Generalist Game QA: Autonomous Playtesting with Meta AI

A Meta-only ViZDoom research harness for autonomous playtesting of human-created levels. Muse Spark chooses actions from screenshots; experiments vary the information it receives to investigate navigation, progression and candidate gameplay issues. Agent failures are not automatically game defects or evidence of human playability.

**Current status:** M0–M2 implementation is merged. One recorded L3 episode completed `my_way_home`; reliable completion and effective grounding-guided navigation remain unverified. M3–M5 are not implemented.

- [Project overview](#project-overview)
- [Quick start](#quick-start)
- [Running L1/L2/L3 episodes](#running-l1l2l3-episodes)
- [Demo and replay](#demo-and-replay)
- [System architecture](#system-architecture)
- [Milestone status](#milestone-status)
- [Key findings](#key-findings)
- [Validation](#validation)
- [Reports and specifications](#reports-and-specifications)
- [Research motivation](#research-motivation)

## Project overview

The three information levels share the same action space, approved Meta models, bounded memory and safety guards:

| Level | Information and current navigation protocol |
|---|---|
| **L1 — Blind play** | Current RGB frame, up to two prior thumbnails, controls and visible history. Infer objectives from pixels; no scenario briefing or map. |
| **L2 — Guided play** | Adds the scenario objective/mechanics briefing. Model-written navigation intentions cite actual `locate` receipts, retaining uncertainty and falling back to visual reasoning when grounding is inconclusive. |
| **L3 — Informed play** | Adds one frozen initial floor plan. The model maintains a tentative location/heading, destination, map route/subgoals and replanning updates; screenshots guide immediate motion. There is no live GPS marker. |

L2/L3 require a current model-written `NAV` annotation in the existing note ledger before acting. These conditions therefore differ in navigation instructions as well as information. A comparison of their outcomes alone does not establish that the extra information caused an improvement. Navigation declarations and map delivery are not proof of effective planning.

Every executed primitive originates in a validated Muse `act` call. Guards issue text warnings or stop a run; they never choose a route, insert an action or supply scripted gameplay. Ground-truth position, labels and geometry remain evaluation-only, apart from the permitted initial L3 map artifact.

## Quick start

Tested environment: **WSL/Linux, Python 3.12, `uv`, and ViZDoom 1.3.2**. Model inference uses the remote Meta Model API. The ViZDoom package supplies the bundled scenarios and Freedoom assets used here.

Run in a WSL/Linux terminal with `git` and `uv` available:

```bash
git clone https://github.com/Ramsi-K/vizdoom-agent-research.git
cd vizdoom-agent-research
uv python install 3.12
uv sync --locked --python 3.12
[ -f .env ] || cp .env.example .env
```

Edit `.env` and set `MODEL_API_KEY` to your Meta Model API key. The existing local name `META_MODEL_API_KEY` is also accepted. `.env` and generated run directories are git-ignored; never commit credentials. Muse Spark access is required for gameplay; SAM access is needed when grounding is called.

Check the CLI and local engine/calibration without making model requests:

```bash
uv run --locked autoqa --help
uv run --locked autoqa doctor --offline
```

The measured M0 calibration is already committed in [configs/calibration.json](configs/calibration.json). The offline doctor deliberately returns a nonzero readiness status because live API checks are skipped; inspect its local-check results. It does not verify current API access or account balance. Running `autoqa doctor` without `--offline` performs live checks and incurs API usage.

## Running L1/L2/L3 episodes

These commands start independent model-controlled episodes with the same game seed and limits. **They make paid Meta API requests.** The defaults used below are Muse Spark 1.3, low reasoning effort, a 2,100-tic limit and a 900-second wall-clock limit.

```bash
uv run --locked autoqa run --scenario my_way_home --level 1 --seed 101 --max-decisions 60 --max-usd 0.60
uv run --locked autoqa run --scenario my_way_home --level 2 --seed 101 --max-decisions 60 --max-usd 0.60
uv run --locked autoqa run --scenario my_way_home --level 3 --seed 101 --max-decisions 60 --max-usd 0.60
```

`--max-usd` is a **stop threshold**, including Pilot and grounding costs. A response can cross it before usage is returned; subsequent actions are stopped. Charges for failed/timed-out requests may be unknown. A run reaching its decision limit has terminated normally but has not necessarily completed the level.

The default grounding mode is the explicitly experimental `muse_box_sam`: Muse proposes a pixel target box and SAM segments it. `--grounding-mode sam_text` selects the direct text-SAM path. Neither mode is a verified object-recognition service; every returned candidate has `identity_verified=false`. No automatic mode fallback or scripted navigation is used.

Other configured scenarios are `health_gathering_supreme`, `deadly_corridor_nav` and `freedoom2_map01`. Use `uv run --locked autoqa run --help` for the available settings. Their configuration does not establish successful play or full validation; the Freedoom L3 exit marker remains unannotated.

Each run prints its new directory under `runs/<timestamp>_<scenario>_L<level>_s<seed>/`. Important artifacts are:

| Artifact | Contents |
|---|---|
| `manifest.json` | Configuration, source/asset/prompt hashes, recorded usage/cost and outcome. |
| `events.jsonl` | Model tool calls, actions, grounding results, warnings and linked navigation intentions. |
| `telemetry.jsonl`, `geometry.json` | Evaluation-only engine observations and initial geometry; not agent navigation inputs. |
| `memory.json`, `frames/` | Final model-written ledger/history and observed frame JPEGs. |
| `plan_l3.png` | L3's frozen initial map, when applicable. |
| `grounding/` | Optional mask, overlay and sanitized wire-trace evidence when localization runs. |
| `episode.lmp` | Native recording used for deterministic replay. |

## Demo and replay

Replays are generated locally from `episode.lmp` without model requests or an API key; recordings and MP4s are not included in a clone. Set `RUN_DIR` to the directory printed by your run, replacing the placeholder:

```bash
RUN_DIR="runs/REPLACE_WITH_YOUR_RUN_ID"
uv run --locked autoqa replay "$RUN_DIR"
```

The default output is a 1280×720 H.264 `replay.mp4` and matching `replay.json`. A successful full export adds a labeled half-speed recap of its final recorded second and a three-second result hold. The metadata separates recorded gameplay frames from presentation frames; the ending does not add simulated gameplay or repaint the HUD.

Existing exports are preserved. Choose a fresh filename for another presentation export or for gameplay-only footage:

```bash
uv run --locked autoqa replay "$RUN_DIR" --output-name replay-demo.mp4
uv run --locked autoqa replay "$RUN_DIR" --no-outcome-ending --output-name replay-raw.mp4
```

`--size 640x480`, `--captions`, and `--from-tic A --to-tic B` are also supported. Sliced clips are gameplay-only. On a natural win, ViZDoom can reset its terminal clock to zero; replay records a separate elapsed-tic reconstruction rather than rewriting the original telemetry.

The verified M2 armor pickup is run **`20261104T101202.353976Z_my_way_home_L3_s101`**. In the development workspace its improved export is `runs/20261104T101202.353976Z_my_way_home_L3_s101/replay-demo.mp4`; this local file is not a hosted demo or a repository asset. See the [navigation/replay report](docs/m2-navigation-review.md) for measured results and provenance.

## System architecture

| Component | Current responsibility |
|---|---|
| [Pilot](autoqa/agent/pilot.py) and [Meta client](autoqa/agent/client.py) | Bounded stateless Muse tool loop, encrypted response replay, usage accounting and request information gate. |
| [Grounder](autoqa/agent/grounder.py) | Current-JPEG-only Muse-box/SAM or direct SAM calls, official parser, masks and optical candidate geometry. |
| [Memory](autoqa/agent/memory.py), [navigation validation](autoqa/agent/navigation.py), [guards](autoqa/agent/guards.py) | Existing ledger/history, model-written guided intentions, citation/freshness checks, visual-cycle warnings and budget stops. |
| [Prompt builder](autoqa/prompts/builder.py) | Pinned L1 task, level-specific briefings/instructions, approved images and frozen L3 plan. |
| [ViZDoom driver](autoqa/doom/env.py) | Headless synchronous PLAYER mode, narrow `PlayerSession`, validated action execution and native recording. |
| [Telemetry](autoqa/doom/telemetry.py) and [plan renderer](autoqa/doom/plan_renderer.py) | Evaluation-only measurements and the once-only permitted L3 map. |
| [Replay exporter](autoqa/doom/replay.py) | Native recording playback, verified H.264 exports and labeled presentation endings. |

| Technology | Use |
|---|---|
| `muse-spark-1.3` | Default Pilot and optional bounding-box proposal inference through Meta. |
| `sam-3.1` / `meta-sam-parser` | Candidate segmentation and actual wire/mask decoding. |
| `openai` Python package | HTTP/Responses client pointed at `https://api.meta.ai/v1`; **no non-Meta runtime model is used**. |
| Python 3.12, `uv`, ViZDoom | Locked environment, CLI and game simulation. |
| Pillow, NumPy, imageio/FFmpeg | Frame processing, evidence images and H.264 replay. |

The planned multi-scenario sweep, QA reviewer, trace verifier, HTML dossier and V-JEPA analysis are not implemented. Inline issue reports are unverified candidates, not automatically established level defects. The current engine integration is ViZDoom; Meta Horizon support has not been implemented.

## Milestone status

| Milestone | Repository status | Validation limits |
|---|---|---|
| M0 — Feasibility/calibration | Implemented and merged. | API/engine access, controls and live mask decoding were measured; text-guided Doom localization failed and account balance was not verified. |
| M1 — Pilot, telemetry, native recording/replay | Implemented and merged. | Real L1 gameplay and replay checks ran; those recorded attempts did not win. |
| M2 — Grounding and information levels | Implemented and merged. | One verified L3 win; L2 navigation regressed, grounding remains unreliable, and some map-design targets remain incomplete. |
| M3 — Sweep/metrics | Not implemented. | Next planned milestone; no multi-seed performance estimate yet. |
| M4 — Review/verification/reporting | Not implemented. | No completed trace-verified QA pipeline or human-playability validation. |
| M5 — Ablations/offline analysis | Not implemented. | No map-withheld causal test or V-JEPA study. |

Merged implementation does not mean every original acceptance target or reliability claim has been established.

## Key findings

- **Grounding:** M0 verified nonempty box-guided SAM decoding, while 100 text requests on sampled Doom frames returned no masks. M2's paired comparison found 0/21 positive hits for text SAM versus 10/21 for Muse-box/SAM, with 2/7 negative false positives. Successful segmentation does not verify the proposed object's identity. See [M0](docs/spike-results.md) and [M2](docs/m2-results.md).
- **Navigation:** one seed-101 L3 retest completed `my_way_home` in 42 decisions, with pickup verified independently in native replay. L2's controlled retest did not win and cost more while exploring fewer coarse cells. These are single-run observations, not reliable completion rates or proof that map access caused the improvement. See [the current navigation report](docs/m2-navigation-review.md).
- **Interpretation:** candidate detections, map delivery, navigation annotations and low-level action execution are audited separately. None alone establishes effective planning, human playability or a game defect. Detailed costs, failures and regressions remain in the linked reports.

## Validation

Local tests exclude the explicitly marked live-API tests; they include native engine checks:

```bash
make test
make lint
```

The M2 presentation update recorded **92 passing tests**, with four live-API tests deselected. `make test-live` runs paid M0 API checks and requires credentials; it is not necessary to inspect the reports or run the local suite.

## Reports and specifications

- [Milestone progress](docs/progress.md).
- [M0 spike results](docs/spike-results.md) and [empty-SAM investigation](docs/sam-investigation.md).
- [M1 Pilot and replay results](docs/m1-results.md).
- [M2 original grounding/boundary results](docs/m2-results.md), [ledger/cycle retest](docs/m2-ledger-retest.md), and [explicit navigation/replay results](docs/m2-navigation-review.md).
- [Compact M2 evidence](docs/m2-evidence.json), [paired grounding cases](docs/m2-grounding-cases.csv), and [model-written navigation updates](docs/m2-navigation-intentions.csv).
- Design documents: [foundations](docs/01-foundations.md), [agent/information boundaries](docs/02-agent.md), [experiments](docs/03-experiments.md), and [implementation/milestone plan](docs/04-implementation.md). These include future design targets; consult the status above and implementation notes before treating them as shipped features.

## Research motivation

The original authored motivation and cited research context are retained below. Repository measurements and current implementation status are described above and in `docs/`.

<details>
<summary>Read the research motivation, background and research questions</summary>

## The Problem: AI Has a Reputation Problem

The creative industries are experiencing growing resistance to generative AI. Artists are challenging the unauthorized use of their work in model training. Writers are discovering their work reproduced and monetized without permission. Game developers are facing consumer backlash over AI-generated assets, while publishers are withdrawing books amid allegations of AI-assisted writing.

The consequences extend beyond companies adopting generative AI. Human artists increasingly face accusations of using AI and demands to demonstrate their creative processes.

**AI's reputation has deteriorated so badly that even genuinely human-created work is becoming suspect.**

The technology industry continues to promote generative AI through promises of faster production and lower costs. These calculations frequently overlook the commercial consequences of replacing creative labor and disregarding consumer expectations.

Executives can decide how their products are developed. Consumers decide whether those products deserve their money.

**Consumer resistance has become a commercial constraint on AI adoption, influencing development decisions, publishing contracts, crowdfunding campaigns, and purchasing behavior.**

Meanwhile, the games industry faces persistent technical challenges. Major releases continue to suffer from poor optimization, unstable frame rates, crashes, progression bugs, and inadequate testing. These problems affect the quality of games, the experience of players, and the commercial prospects of developers.

AI offers substantial opportunities to address these challenges through automated testing, performance analysis, debugging, and gameplay evaluation.

**The games industry has an opportunity to direct AI investment toward improving the quality, reliability, and playability of human-created games.**

This project investigates that opportunity through autonomous gameplay testing in ViZDoom using Meta AI models. It examines whether agents can navigate unfamiliar environments, recognize gameplay failures, improve through structured feedback, and produce reproducible observations that developers can use to investigate technical problems.

A central challenge is determining how effectively AI gameplay observations reflect the experiences of human players. An agent may successfully complete an environment that human players find confusing, or struggle with interactions that humans understand intuitively. Completion rates alone provide limited evidence of human playability.

The research therefore examines both autonomous agent performance and the usefulness of its observations for evaluating gameplay.

**The objective is to establish whether AI agents can contribute meaningful, actionable evidence that helps developers build better games.**

## 1. The Creative Industries Are Rejecting AI

### 1.1. The consent problem

Generative AI's reputation crisis begins with how the technology was developed and commercialized.

Large-scale collection of online writing, artwork, and other creative material has allowed companies to train systems capable of producing competing content. Creators have challenged the use of their work without permission, attribution, or compensation.

The result is an industry selling automated creative production while many of the people whose work helped enable that automation object to the arrangement.

The backlash is therefore not reducible to a dislike of new technology. It concerns who owns creative work, who profits from it, and who gets replaced.

**A technology that promises to eliminate the cost of human creativity should not be surprised when creative communities reject it.**

### 1.2. AO3: scraping, unauthorized reproduction, and AI monetization

The November 2026 Archive of Our Own controversy illustrates what happens when creative work is treated as freely extractable material.

While AO3 was unavailable following a security incident, unauthorized mirror sites surfaced containing millions of fanfiction works.

One site, **ao3.life**, reproduced authors' stories without their permission and attempted to monetize them through AI-generated audiobooks. Reports described deleted and restricted works appearing on mirror sites, along with concerns about authors being asked to provide personal information to request removal.

The site reportedly claimed that audiobook revenue would be shared with authors—despite those authors never having authorized the commercial use of their writing.

The site's operator eventually took it offline following backlash.

The underlying issue is unmistakable: writers contributed their work to a particular community under particular conditions. Someone else copied that work, repackaged it using AI, and attempted to turn it into a commercial product.

AI made the proposed monetization scalable. It did not provide consent.

The incident also exposed a broader problem: authors were expected to discover unauthorized copies of their own work and request removal individually.

**Creative ownership was effectively reversed. The person copying the work acted first; the creator was left to object afterward.**

References:
- [The Mary Sue, November 3, 2026 — AO3 clone sites repost authors' work without consent](https://www.themarysue.com/while-ao3-is-down-fanfiction-clone-sites-are-reposting-authors-work-without-consent-and-one-was-even-selling-ai-audiobooks/)
- [Kotaku — AO3 outage and unauthorized AI audiobooks](https://kotaku.com/a-scam-website-is-trying-to-cash-in-on-ao3-outage-by-selling-audiobooks-of-fanfics-2000752419)

### 1.3. Publishing: AI allegations now destroy commercial value

The publishing industry provides several examples of AI suspicion carrying immediate financial and reputational consequences.

**Mia Ballard — *Shy Girl***

In March 2026, Hachette cancelled the US publication of *Shy Girl* and withdrew the UK edition following allegations of AI-generated writing.

The allegations had spread through Goodreads, Reddit, and YouTube. Ballard denied personally using AI to write the book and attributed any AI involvement to someone she had hired to work on an earlier edition.

Regardless of that dispute, the commercial outcome was decisive: the publisher withdrew the book.

Reference: [The Guardian, March 20, 2026 — Hachette pulls *Shy Girl* after suspected AI use](https://www.theguardian.com/books/2026/mar/20/hachette-horror-novel-shy-girl-suspected-ai-use-mia-ballard)

**H. M. Wolfe — *Daggermouth***

*Daggermouth* became a commercial success and attracted a major publishing deal before allegations of AI-assisted writing became a prominent part of its public discussion.

Research reported by *The Atlantic* identified substantial indicators of AI involvement. Wolfe denied using generative AI.

The controversy demonstrates that even a commercially successful novel can become defined by questions about how it was produced rather than what readers experienced.

Reference: [The Atlantic, July 2026 — Is This What Comes After AI Slop?](https://www.theatlantic.com/technology/2026/07/daggermouth-novel-bestseller-ai/688067/)

**Thélyson Orélien — *C'était ça ou mourir***

In October 2026, Canadian publisher Boréal ended its relationship with Orélien after an investigation found AI-related metadata in manuscript documents.

Orélien denied using AI to write the novel. The publisher said it could not establish the precise extent of AI involvement, but concluded that AI had been used during the writing process.

The dispute followed the novel's literary recognition and generated significant public controversy.

Reference: [The Guardian, October 22, 2026 — Canadian publisher ends relationship with novelist over alleged AI use](https://www.theguardian.com/culture/2026/oct/22/canadian-publisher-boreal-ends-relationship-author-thelyson-orelien-over-ai)

These are not identical cases. They involve different evidence, allegations, responses, and outcomes.

But collectively, they demonstrate a substantial change in the market.

**AI involvement is no longer automatically perceived as an efficiency advantage. It can become a reason for publishers to withdraw support and consumers to question the legitimacy of a product.**

And suspicion alone can have consequences before authorship is conclusively established.

### 1.4. Human creators are now paying for AI's reputation

The backlash has reached a particularly damaging stage: creators who do not use generative AI are being accused of doing so.

In June 2025, *The Guardian* documented independent game developers facing allegations that their human-created artwork was AI-generated.

The developer of *Little Droid*, Stamina Zero, responded by obtaining preliminary sketches and Photoshop files from the artist and publishing evidence of the artwork's creation.

Some critics remained unconvinced.

Another developer faced similar accusations over commissioned artwork.

This is an extraordinary burden to place on creators. An artist completes original work, and instead of discussing its artistic merit, the audience demands proof that a human made it.

The reputation damage caused by irresponsible AI adoption has spread to people who never adopted it.

**When human artists must defend their work against accusations of being AI-generated, the technology has become a liability for the entire creative market.**

Reference: [The Guardian, June 26, 2025 — Game developers accused of using AI even when they aren't](https://www.theguardian.com/games/2025/jun/26/video-game-developers-using-ai-even-when-they-arent-stamina-zero)

### 1.5. Scarlett Johansson, creative consent and the right to refuse

In May 2024, Scarlett Johansson publicly challenged OpenAI over its *Sky* voice for ChatGPT.

Johansson stated that OpenAI CEO Sam Altman had previously approached her about providing a voice for the product. She declined. When OpenAI subsequently demonstrated Sky, Johansson believed the voice sounded strikingly similar to her own. Altman's contemporaneous reference to *Her*, the film in which Johansson voiced an artificial intelligence, intensified the controversy.

Johansson sought legal advice and demanded an explanation. OpenAI suspended Sky.

OpenAI denied imitating Johansson and maintained that Sky belonged to a different voice actress. Subsequent reporting indicated that the actress had been selected before OpenAI approached Johansson. **Deliberate voice cloning was therefore not established.**

Nevertheless, the commercial consequence was clear: a prominent creator publicly challenged an AI product over consent and resemblance, and the company withdrew the contested feature.

The episode demonstrates why creative consent cannot be treated as an inconvenience to be resolved after deployment. Even where infringement is disputed, ignoring objections can produce reputational damage, legal scrutiny, and product reversals.

**References**

- [Reuters — Scarlett Johansson says OpenAI chatbot voice is eerily similar to hers (May 2024)](https://www.reuters.com/technology/scarlett-johansson-says-openai-chatbot-voice-eerily-similar-hers-2024-05-21/)
- [TechCrunch — Scarlett Johansson says OpenAI approached her to use her voice (May 2024)](https://techcrunch.com/2024/05/20/scarlett-johansson-says-that-openai-approached-her-to-use-her-voice/)
- [OpenAI — How the voices for ChatGPT were chosen](https://openai.com/index/how-the-voices-for-chatgpt-were-chosen/)

## 2. Consumers Are Forcing Game Studios to Reverse AI Decisions

Game development is an especially important battleground because it combines visual art, writing, music, voice acting, animation, and interactive design.

Replacing any of these disciplines with generated content affects the product players are being asked to purchase.

The industry has already produced concrete examples of consumer resistance changing development decisions.

### 2.1. *1666: Amsterdam*: production efficiency meets consumer rejection

In June 2026, Panache Digital Games faced backlash after players identified AI-generated artwork in the prologue of *1666: Amsterdam*.

The studio confirmed that AI-generated assets had entered the public demo and marketing materials.

Creative director Patrice Désilets subsequently explained the production advantage: AI could generate 75 visual variations in seconds, something that could not be achieved through equivalent human illustration work in the same time.

That statement exposes the economic attraction of generative AI to studio leadership.

Human creative labor takes time. Generative AI promises an inexpensive shortcut.

But players were not impressed by the efficiency argument.

Following the backlash, Panache apologized and committed to replacing the generated artwork with human-created assets.

By August 2026, Désilets confirmed that the studio had removed generative AI from its development process and would stop using it.

**The studio adopted AI because it was convenient. Consumers objected. The studio reversed course.**

That is the commercial reality AI adoption strategies must confront.

The people controlling production budgets can decide that replacing creative work is efficient. The people purchasing the finished product can decide that the resulting game is no longer worth supporting.

And when the studio must commission replacement artwork, revise its pipeline, and repair public trust, the original cost savings become considerably less convincing.

References:
- [GamesRadar+, June 2026 — Panache apologizes for AI-generated assets](https://www.gamesradar.com/games/action/1666-amsterdam-dev-apologizes-for-ai-in-prologue-demo-says-full-game-will-not-include-any-assets-generated-by-ai/)
- [GamesRadar+, August 2026 — Désilets explains AI production efficiency and asset replacement](https://www.gamesradar.com/games/action/with-gen-ai-assets-cut-from-assassins-creed-creators-new-game-demo-lead-admits-we-didnt-even-realize-it-was-ai-anymore-because-we-were-used-to-seeing-it/)
- [GamesRadar+, August 2026 — Studio confirms it will stop using AI](https://www.gamesradar.com/games/action/after-ai-backlash-assassins-creed-creator-says-1666-amsterdam-wont-be-using-any-ai-anymore-and-only-used-it-in-conception-and-pre-production/)

### 2.2. Larian Studios: even a respected developer was forced to reconsider

Larian Studios, developer of *Baldur's Gate 3*, encountered a similar backlash after CEO Swen Vincke discussed using generative AI to explore ideas, prepare presentations, produce concept-art references, and generate placeholder text during development of *Divinity*.

Players and developers objected.

In January 2026, Larian publicly committed to excluding generative AI from concept-art development.

Vincke stated that *Divinity* would not contain generative AI artwork and that the studio would refrain from using it during concept-art development to eliminate doubt.

This was a significant reversal because the studio had already argued that AI could improve internal workflows.

Larian retained an interest in exploring AI for other development tasks, but consumer pressure established a boundary around creative production.

**Even a highly respected studio could not simply declare its AI workflow acceptable and expect its audience to agree.**

References:
- [Video Games Chronicle, December 17, 2025 — Larian responds to AI backlash](https://www.videogameschronicle.com/news/holy-f-guys-were-not-replacing-artists-larian-boss-responds-to-ai-backlash/)
- [PC Gamer, January 9, 2026 — Larian abandons generative AI concept art](https://www.pcgamer.com/games/rpg/larian-swears-off-gen-ai-concept-art-tools-and-says-there-is-not-going-to-be-any-genai-art-in-divinity-but-its-still-trying-ai-things-out-across-departments/)
- [GameSpot, January 9, 2026 — Larian clarifies its AI policy during Reddit AMA](https://www.gamespot.com/articles/larian-studios-draws-harder-line-on-ai-clarifies-stance-in-reddit-ama/1100-6537296/)

### 2.3. *Crimson Desert*: generated assets become a release problem

Pearl Abyss faced criticism in March 2026 after AI-generated artwork appeared in *Crimson Desert*.

The developer acknowledged that experimental assets had survived into the released game, apologized, and announced a broader audit.

Once again, a tool intended to simplify production created additional work and reputational damage.

The problem was not merely that generated artwork appeared. It was that the studio's production and review processes failed to prevent material intended for replacement from reaching consumers.

Reference: [Video Games Chronicle — *Crimson Desert* developer apologizes for AI artwork](https://www.videogameschronicle.com/news/crimson-desert-developer-apologizes-for-accidental-inclusion-of-ai-art-promises-comprehensive-audit/)

### 2.4. Crowdfunding: consumers can reject AI before funding it

Kickstarter requires disclosure of AI involvement, and other crowdfunding platforms have introduced policies addressing AI-generated content. Prospective backers can evaluate a project's creative practices before committing money.

Consumers therefore exercise influence at multiple stages: funding, wishlisting, purchasing, reviewing, and publicly challenging development decisions.

**The decision to replace creative contributors with generative AI may belong to studio management. The decision to finance and purchase the resulting game belongs to consumers.**

**References**

- [Kickstarter — Introducing our new AI policy (2023)](https://updates.kickstarter.com/introducing-our-new-ai-policy/)
- [Kickstarter — Project rules](https://www.kickstarter.com/rules)
- [BoardGameWire — Awaken Realms removes AI art from its Puerto Rico crowdfunding campaign (2024)](https://boardgamewire.com/index.php/2024/03/02/awaken-realms-pulls-ai-art-from-deluxe-puerto-rico-kickstarter-after-ravensburger-steps-in/)

### 2.5. Epic, Valve, and the cost of AI-accelerated development

In October 2026, Epic Games proudly announced that it had used generative AI to build a faster version of the Epic Games Launcher. 
The company described the new launcher as *vibe coded* and publicly challenged Steam on performance. Epic claimed its new launcher started in 5.47 seconds, compared with Steam's 10.07 seconds in Epic's testing.

But Valve had released its own Steam client update the previous day, including startup performance improvements. Independent comparisons subsequently challenged Epic's performance advantage, with some testers reporting Steam launching in approximately five seconds.

The episode illustrates an important distinction: **AI-assisted development may accelerate software production, but the resulting software must still compete on actual technical performance.**

Two weeks later, Valve addressed a considerably more serious consequence of AI-accelerated production.

At Steam Indie Connect in South Korea, Valve representatives Tom Giardino and Marc Wilkinson acknowledged the growing problem of AI-generated game clones and low-effort releases flooding Steam.

Developers described increasingly serious concerns about releasing demos and revealing original game mechanics.

One reported an extortion threat: pay the would-be copier, or have an AI-generated clone released before the original game.

This is particularly damaging for independent developers, who may spend years creating an original concept only to see another developer reproduce its most visible features in a fraction of that time.

Steam Next Fest, intended to help upcoming games find an audience, has also become increasingly crowded. The October 2026 event featured more than 8,000 demos, creating substantial discoverability challenges for smaller studios.

Valve acknowledged these problems but declined to impose broad restrictions on developers' choice of tools.

Instead, Giardino suggested that original developers should build communities and continually add distinctive features that competitors could not easily reproduce.

**The burden is therefore falling on the creators whose work is being copied, rather than on the systems making copying easier.**

Developers must invest more effort in protecting their ideas, maintaining visibility, building communities, and differentiating their products—while competing against imitators that can reproduce substantial portions of their work much faster.

This is the contradiction at the center of AI-driven game production.

Epic celebrates generative AI for accelerating development. Valve acknowledges that the same acceleration is making imitation and marketplace saturation increasingly difficult to manage.

Meanwhile, the original creators bear the commercial consequences.

**Faster software production is not inherently beneficial when it also makes it easier to overwhelm marketplaces with copies of other people's work.**

The gaming industry does not need AI merely to produce more software, more quickly.

It needs technical tools that improve the quality, reliability, and playability of the software developers have already created.

#### References

- [Epic Games — New Epic Games Launcher Preview](https://launcher.epicgames.com/) — Official launcher performance claims and comparisons.
- [Video Games Chronicle, October 14, 2026 — Epic says it vibe-coded its launcher with AI](https://www.videogameschronicle.com/news/epic-says-its-vibe-coded-its-new-epic-games-launcher-with-ai-because-programming-is-hard/)
- [GamesRadar+, October 2026 — Valve updates Steam startup performance ahead of Epic's comparison](https://www.gamesradar.com/games/valve-beats-epic-to-the-punch-updates-steam-launcher-1-day-before-its-rival-claims-to-be-faster-thanks-to-ai-powered-vibe-coding/)
- [PC Gamer, October 28, 2026 — Valve acknowledges Steam's AI cloning problem](https://tech.yahoo.com/gaming/articles/valve-admits-steam-ai-slop-173724029.html)
- [IGN, October 28, 2026 — Valve discusses AI clones and Steam Next Fest saturation](https://tech.yahoo.com/gaming/articles/valve-thinking-solve-steams-ai-125920047.html)
- [Kotaku, October 26, 2026 — Epic CEO Tim Sweeney attacks Steam's AI disclosure requirements](https://kotaku.com/epic-steam-valve-tim-sweeney-gen-ai-declaration-political-abhorrent-2000749706)


## 3. Generating Content Is Not the Same as Owning It

The commercial argument for generative AI usually begins with cost.

Why commission artwork if a model can generate it? Why employ writers if dialogue can be produced automatically? Why spend months building assets if AI tools promise to create them in minutes?

But a commercially successful game requires more than content.

It requires defensible intellectual property, distinctive creative direction, technical reliability, and customers willing to pay.

### 3.1. AI-generated content creates ownership problems

In January 2025, the US Copyright Office reaffirmed that copyright protection depends on sufficient human authorship.

Material generated entirely by AI without qualifying human creative contribution generally cannot receive copyright protection under US law. Human-authored expressive contributions remain protectable.

This creates a direct commercial problem for companies building products around automatically generated creative content.

A studio may be able to generate thousands of assets. That does not mean it owns exclusive rights over every expressive element those systems produce.

**Cheap generation is not equivalent to valuable intellectual property.**

Reference: [US Copyright Office — Copyright and Artificial Intelligence, Part 2: Copyrightability](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf)

### 3.2. *Needle in a Haystack*: what happens when code is copied?

In October 2026, the developers of *Needle In A Haystack* publicly accused another Steam release, *A Game About Needle In A Haystack*, of copying their implementation.

Their statement identified 13 matching classes, extensive function similarities, and identical error messages, including a reference to a shader absent from the accused game's project.

The allegation concerned source-code copying, not merely similar gameplay mechanics.

Reference: [Steam Community — Needle In A Haystack: Another game stealing our code](https://steamcommunity.com/app/5085740/)

This dispute raises a further question for increasingly AI-generated game-development pipelines.

What happens when substantial portions of a game's code are generated by models, and another developer produces similar code?

Which elements qualify for copyright protection? How is human authorship established? How is independent generation distinguished from copying?

The actual *Needle In A Haystack* dispute does not establish that the original code was AI-generated. It demonstrates why ownership and evidence of copying matter.

As AI-generated code becomes more common, these questions become harder to ignore.

## 3.3. AI-assisted coding, game cloning, and platform responsibility

AI-assisted programming presents a different question from AI-generated creative content.

Using an AI assistant to identify performance bottlenecks, write tests, diagnose crashes, or improve software reliability can support developers without replacing the creative work that gives a game its identity.

But the same tools can also accelerate imitation.

**The distinction is not whether AI generated code. It is whether the technology contributes original engineering value or makes it easier to appropriate another developer's work.**


### Valve acknowledges the problem of AI-generated game clones

Later in October 2026, Valve representatives discussed the increasing difficulty of managing AI-assisted game imitation and discoverability on Steam.

Developers described the possibility of original games being copied and competing versions reaching the market before the originals. Reports also described threats of cloning directed at developers.

The implications for independent studios are serious.

A developer may spend years building a distinctive game, refining its mechanics, commissioning artwork, and preparing a public demo. If another party can rapidly reproduce its most recognizable features, the original developer bears the cost of both innovation and defending its market position.

This creates a particularly damaging incentive: developers may become reluctant to demonstrate unfinished games publicly.

For independent studios, demos and festivals are essential discovery mechanisms. A marketplace in which revealing original work increases the risk of premature imitation undermines those mechanisms.

Valve has emphasized developers' freedom to choose their tools rather than imposing a blanket prohibition on AI-assisted development.

That position leaves an unresolved problem: **how can platforms preserve legitimate AI-assisted engineering while protecting original developers from rapid, low-effort imitation?**

### Anthropic's game-cloning restrictions

In late October 2026, reports indicated that Anthropic had updated Claude's instructions to discourage recreating specific existing video games, including attempts to reproduce them under different names.

The restriction is notable because it addresses game imitation directly rather than treating it solely as a question of copying source-code text.

Recreating a game does not necessarily require access to its source code. An imitation can reproduce visible mechanics, progression, interface patterns, audiovisual presentation, and other distinctive features through newly generated code.

Anthropic's intervention acknowledges that general-purpose coding assistants can be used for this purpose.

However, restrictions imposed by one provider do not solve the broader problem. Other models, development tools, and locally hosted systems remain available.

The responsibility cannot rest exclusively on individual model providers. Game marketplaces, publishers, and developers also need effective ways to distinguish legitimate inspiration and competition from deceptive or infringing imitation.

**References**

- [Game Developer — Anthropic appears to have ordered Claude to stop remaking video games (October 2026)](https://www.gamedeveloper.com/programming/anthropic-appears-to-have-ordered-ai-tool-claude-to-stop-remaking-video-games)
- [Ars Technica — Claude's new rules against game cloning won't stop the AI copying boom (November 2026)](https://arstechnica.com/ai/2026/11/claudes-new-rules-against-game-cloning-wont-stop-the-ai-copying-boom/)

### GitHub Copilot and the limitations of code-matching safeguards

GitHub Copilot approaches copying through a different mechanism.

GitHub provides controls intended to reduce suggestions matching publicly available code, alongside reference information that can help developers identify matches.

These safeguards address an important intellectual-property concern: generated code may reproduce existing code, potentially creating attribution and licensing obligations.

The legal dispute in *Doe v. GitHub*, involving GitHub, Microsoft, and OpenAI, further demonstrates that AI-assisted code generation has raised serious questions about reproducing open-source material and preserving license information.

But source-code matching cannot solve game cloning by itself.

Two implementations can contain entirely different code while producing substantially similar gameplay. A coding assistant can reconstruct a game from screenshots, descriptions, videos, or interaction traces without reproducing the original source files.

A filter designed to detect matching code therefore addresses only part of the problem.

**References**

- [GitHub Docs — Managing GitHub Copilot policies](https://docs.github.com/en/copilot/how-tos/configure-personal-settings/manage-policies)
- [GitHub Docs — Finding public code that matches GitHub Copilot suggestions](https://docs.github.com/en/copilot/using-github-copilot/finding-public-code-that-matches-github-copilot-suggestions)
- [GitHub — Copilot Trust Center](https://copilot.github.trust.page/)

### What this means for AI in game development

These incidents expose two competing applications of AI-assisted programming.

One application improves existing games: finding crashes, testing navigation, identifying inaccessible areas, reproducing bugs, measuring performance, and helping developers validate their design decisions.

The other accelerates the production of imitations, placing additional commercial pressure on developers who created the original work.

Both may involve similar underlying technologies. Their contributions to the industry are fundamentally different.

This project deliberately investigates the first application.

Rather than generating game artwork, writing narratives, or reproducing an existing title, it evaluates whether AI agents can interact with a game as testers, identify gameplay failures, and produce actionable feedback.

The research question is not simply whether an AI agent can complete a level. It is whether its observations can help human developers understand and improve a player's experience.

**The objective is to use AI to improve the quality of games, not to increase the speed at which existing games can be copied.**

### 3.4. More games do not mean more customers

AI-native development tools promise increasingly automated asset creation, environment generation, and game production.

But lowering the cost of producing games does not increase the number of hours consumers have to play them.

A marketplace saturated with inexpensive generated products becomes harder to navigate. Original developers compete for attention against increasingly large volumes of similar content.

If generated products are difficult to distinguish, poorly maintained, or commercially unattractive because of their AI involvement, production efficiency alone will not rescue them.

**The industry does not need an unlimited supply of cheaply generated games. It needs games that players actually want to buy and can reliably play.**

## 4. Game Development Has a Technical Problem That AI Is Not Solving

While the industry experiments with automating creative production, players continue to encounter broken releases, unstable frame rates, stuttering, poor optimization, and excessive hardware requirements.

These are not minor inconveniences. They determine whether a purchased game can be played properly.

### 4.1. *Oblivion Remastered*: visual ambition without adequate performance

Digital Foundry's April 2025 investigation of *The Elder Scrolls IV: Oblivion Remastered* identified serious CPU limitations, excessive GPU demands, and persistent stuttering.

Further testing found performance deterioration during longer play sessions, inconsistent frame pacing, and crashes across console platforms.

These problems persisted through subsequent patches.

The game demonstrates a basic industry failure: a visually impressive product can still ship with serious technical defects.

References:
- [Digital Foundry, April 26, 2025 — Oblivion Remastered PC: Impressive Remastering, Dire Performance Problems](https://www.youtube.com/watch?v=p0rCA1vpgSw)
- [Digital Foundry, May 10, 2025 — All Consoles Have Big Problems](https://www.youtube.com/watch?v=wxtlCgLyKpY)
- [Push Square, July 2025 — Performance problems persist after patch 1.2](https://www.pushsquare.com/news/2025/07/oblivion-remastered-still-broken-and-buggy-on-ps5-even-after-patch-1-2)

### 4.2. *Skyblivion*: the value of sustained human development

*Skyblivion*, the long-running volunteer project rebuilding *Oblivion* using Skyrim's engine, offers a useful contrast.

Its development represents years of deliberate reconstruction, asset creation, integration, and testing by human contributors.

The contrast is not proof that one engine or development model is inherently superior.

It demonstrates that producing and maintaining a complex game requires sustained engineering and creative effort that cannot be reduced to generating more assets.

### 4.3. AI risks accelerating the wrong part of production

The emerging promise of AI-native game development is speed: faster coding, faster asset generation, faster prototyping, faster world creation.

But what happens when the rate of production exceeds the capacity to test and optimize what has been produced?

More generated systems create more interactions to verify.

More generated environments create more opportunities for navigation defects, performance bottlenecks, and inaccessible gameplay.

More generated code creates more integration and maintenance work.

Without equivalent investment in testing, debugging, and optimization, faster production can simply produce larger quantities of unfinished work.

**Automating creation while neglecting verification is not progress. It is a faster route to technical debt.**

If the industry is determined to invest heavily in AI, why prioritize replacing creative workers when engineering quality already requires substantial additional attention?

## 5. A Better Use of AI: Test Human-Created Games

The preceding examples establish two problems.

First, AI-generated creative content faces serious resistance from the people expected to create, purchase, and support it.

Second, game development continues to struggle with technical quality.

These problems point toward a different application of AI.

**Instead of asking AI to make the art, write the story, design the world, or replace the developer, ask whether it can help determine if the game actually works.**

Vision-language models can interpret visual input and produce actions or structured observations. That makes them candidates for investigating automated gameplay testing.

A gameplay-testing agent could examine navigation failures, repeated unsuccessful interactions, unreachable objectives, unexpected behavior, and failures to complete scenarios.

The objective is not to automate artistic judgment.

It is to produce reproducible technical evidence that human developers can inspect and use.

Such a system would contribute to game development without requiring developers to surrender creative control.

## 6. Existing Research Benchmarks AI on Gameplay. But Does That Measure Human Playability?

AI game-playing research already exists.

Models are evaluated on whether they can perceive game states, choose actions, plan routes, defeat enemies, solve puzzles, and complete objectives.

These benchmarks are useful for measuring AI capabilities.

**But the ability of a machine to complete a game does not establish that the game is well designed for humans.**

### 6.1. LMGame-Bench

[LMGame-Bench: How Good Are LLMs at Playing Games? — ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/hash/83a4ea71b13bc86308a2bd0b5e07fb61-Abstract-Conference.html)

LMGame-Bench evaluates 13 models across six games, investigating perception, memory, reasoning, and planning.

It identifies weaknesses in visual-state extraction, reflection, and spatiotemporal reasoning.

The benchmark measures the capabilities and limitations of AI models through gameplay.

It does not establish that those capabilities predict the quality of human gameplay experiences.

### 6.2. DoomVLM

[DoomVLM — GitHub](https://github.com/Felliks/DoomVLM)

DoomVLM investigates vision-language models controlling agents in VizDoom environments.

It provides a direct technical precedent for screenshot-based observation, model-driven action selection, and gameplay evaluation.

Its central concern is how successfully models play Doom scenarios.

Our concern is what their gameplay can reveal about the environment being tested.

### 6.3. SegDoom

SegDoom investigates visual representations, including semantic segmentation, for agents operating in Doom environments.

Its work is relevant to visual perception and generalization in gameplay agents.

But performance improvements in machine-controlled gameplay do not automatically translate into better evaluation of human player experience.

### 6.4. The human-playability gap

Consider a level containing an unmarked doorway.

An AI agent repeatedly fails to recognize the entrance.

A human player identifies it immediately.

The agent's failure tells us something about the agent's perception. It does not establish that the level is poorly designed.

Now reverse the situation.

The agent identifies the doorway immediately, while human players repeatedly overlook it.

The agent completes the level successfully. But the human experience reveals a navigation problem.

**Which result should matter to the developer?**

If the objective is human playability, the answer cannot be determined by the AI agent's completion rate alone.

This distinction becomes especially important when interpreting automated testing results.

An agent may fail because of its own perceptual or reasoning limitations.

It may succeed using patterns or information that humans would not naturally recognize.

It may complete an objective without experiencing the confusion, frustration, or satisfaction that makes gameplay meaningful to a person.

### 6.5. The yellow-paint controversy and *Marvel's Wolverine*: when accessibility becomes excessive hand-holding

The controversy surrounding yellow paint in video games has become a broader criticism of modern AAA design: **in attempting to prevent players from becoming confused, developers risk removing the exploration, problem-solving, and agency that make games engaging.**

Brightly coloured ledges, painted climbing routes, glowing objective markers, and constant companion hints are intended to make games more readable. But when every interactive surface is highlighted and every puzzle is explained, players are no longer necessarily discovering solutions. They are following instructions.

The controversy gained attention around *Resident Evil 4 Remake* and *Final Fantasy VII Rebirth*. Players criticized the conspicuous markings for disrupting immersion and making exploration unnecessarily obvious. *Final Fantasy VII Rebirth* director Naoki Hamaguchi acknowledged the criticism, explaining that different players require different levels of guidance and that there was no universally correct solution.

[Source: AUTOMATON WEST — Final Fantasy VII Rebirth director responds to yellow paint criticism (March 2024)](https://automaton-media.com/en/news/final-fantasy-vii-rebirth-director-responds-to-yellow-paint-issue/)

#### Marvel's Wolverine: the controversy escalates

The September 2026 release of Insomniac Games' *Marvel's Wolverine* brought this criticism into sharper focus.

The game featured a prominent Scent Trail system that visually directed players toward objectives. Players mocked the glowing trails, while viral gameplay clips highlighted sequences that appeared to require little or no meaningful player input.

Criticism extended beyond navigation. Videos circulated showing quick-time events apparently succeeding without the required button presses and chase sequences that seemed to progress without controller input.

The controversy was not simply that *Wolverine* made its objectives easy to find. **Players questioned why an interactive game appeared so determined to eliminate the possibility of failure, uncertainty, or independent decision-making.**

In his September 2026 review, Skill Up criticized the game's excessive hand-holding. Clips from the review and other gameplay footage spread across X and Reddit, turning *Wolverine* into a prominent example of what critics considered overly automated AAA game design.

The Guardian subsequently examined the backlash in an article titled *Why fans are digging their claws into Wolverine over its patronising gameplay*. Its analysis centered on the importance of friction: the effort, uncertainty, and decision-making through which players experience challenge and accomplishment.

Insomniac responded to criticism by releasing an update that reduced the opacity of Scent Trails and Audio Sense indicators. The studio also stated that it was exploring options to let players toggle these systems.

**A navigation system designed to help players became a source of dissatisfaction significant enough for the developer to modify it after release.**

References:

- [The Guardian — Why fans are digging their claws into Wolverine over its patronising gameplay (September 23, 2026)](https://www.theguardian.com/games/2026/sep/23/why-are-gamers-ripping-into-the-new-wolverine-game)
- [Know Your Meme — Why everyone is mocking Marvel's Wolverine (September 16, 2026)](https://trending.knowyourmeme.com/editorials/guides/why-is-everyone-mocking-marvels-wolverine-the-controversy-and-memes-surrounding-the-new-wolverine-game-explained)
- [GamesRadar+ — Insomniac reduces Wolverine's Scent Trail intensity following criticism](https://www.gamesradar.com/games/action/new-marvels-wolverine-update-makes-the-games-infamous-scent-trails-less-intense-while-insomniac-explores-toggleable-options/)

#### Developers disagree about how much guidance players need

The backlash has also exposed competing design philosophies.

Naoki Hamaguchi defended the need for visual guidance in *Final Fantasy VII Rebirth*, arguing that developers must account for players with different preferences and abilities.

By contrast, *Control Resonant* director Mikael Kasurinen explained that Remedy deliberately avoids excessive environmental signposting because discovering how to progress is part of the intended experience.

Kasurinen acknowledged that this approach can initially alienate players, but argued that allowing players to make their own decisions is fundamental to the game's design.

Neither philosophy can be evaluated solely through level-completion statistics. The intended player experience differs.

References:

- [PC Gamer — Final Fantasy VII Rebirth director discusses the need for yellow paint (October 2025)](https://www.pcgamer.com/games/rpg/ff7-rebirth-director-knows-a-whole-heap-of-people-hate-yellow-paint-on-ledges-but-reckons-there-is-definitely-a-need-for-that-kind-of-thing/)
- [Dexerto — Control Resonant director explains avoiding yellow paint and Wolverine-style hand-holding (September 2026)](https://www.dexerto.com/gaming/control-resonant-director-explains-why-they-avoided-yellow-paint-and-wolverine-style-hand-holding-3413394/)

#### Why this matters for AI-assisted game testing

These controversies expose a fundamental problem with treating autonomous game completion as a measure of game quality.

An AI agent optimized to complete objectives will generally benefit from explicit navigation cues, highlighted interactive objects, and reduced uncertainty. Such features can make the environment easier for the agent to interpret and navigate.

But *Marvel's Wolverine* demonstrates why those same features cannot automatically be treated as improvements to the human experience.

**A game can become easier to complete while becoming less satisfying to play.**

An AI agent that successfully follows a glowing trail cannot establish whether the trail was necessary, whether it undermined exploration, or whether players would have preferred to discover the route themselves.

Similarly, an agent completing a quick-time event or scripted chase cannot establish whether that sequence offers meaningful interaction.

The problem is not that AI agents are incapable of navigating games. It is that **navigation success and gameplay quality are different measurements**.

This project therefore distinguishes between:

1. **Agent performance:** Can the AI identify routes, interact with the environment, and complete objectives?
2. **Game usability:** Do observed failures indicate genuine problems with navigation, interaction, or progression?
3. **Human experience:** Do the agent's observations correspond to difficulties experienced by human players, rather than limitations specific to the model?


A navigation failure should not automatically produce a recommendation to add brighter markers. A successful traversal should not automatically validate the level's design.

Instead, agent observations must be evaluated against human playtesting evidence and the developer's intended experience.

**The purpose of AI-assisted game testing should not be to make games easier for AI agents to finish. It should be to help developers identify genuine problems without sacrificing the challenge, discovery, and player agency that make games worth playing.**

## 7. Research Questions

This project investigates whether Meta AI models can contribute to gameplay testing through interaction with VizDoom.

**RQ1 — Can Meta models navigate and complete unfamiliar gameplay scenarios?**

Evaluate autonomous behavior using visual observations and a constrained action space, without a custom reinforcement-learning policy.

**RQ2 — Can the models recognize and document gameplay failures?**

Investigate repeated navigation failures, unsuccessful interactions, missed objectives, and other observable difficulties.

**RQ3 — Does structured feedback improve subsequent attempts?**

Compare gameplay without feedback against gameplay incorporating observations from previous attempts.

**RQ4 — Can the resulting observations contribute to technical game testing?**

Evaluate whether the agent produces reproducible, actionable information beyond a simple success-or-failure score.

**RQ5 — How closely do AI gameplay difficulties correspond to human gameplay difficulties?**

Investigate the distinction between agent performance and human playability, using human reference observations where available.

</details>
