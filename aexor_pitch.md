# Aexor — A Personal AI Agent That Actually Respects the User

**From:** Anantshree Chandola
**To:** [Hiring Manager], HighLevel
**Date:** April 7, 2026

---

Hi [Name],

I wanted to share something I've been building — **Aexor**, a personal AI agent that can plan and execute real-world tasks across your connected tools (calendars, email, messaging, and more) while keeping the human firmly in the loop.

I've recorded a walkthrough of the core functionality. I'd love for you to watch it and, if you're interested, I can share a Docker image so you can try it yourself and give me your honest feedback.

---

## What Is Aexor?

Aexor is a **self-hosted personal AI agent** that turns natural language requests into executable, auditable workflows. It connects to the SaaS tools people already use and orchestrates multi-step tasks with a safety-first approach that prioritizes user trust and control.

**The one-liner:** *"Tell Aexor what you need. It shows you what it will do. You approve. It does it. It remembers for next time."*

**Example:**
> "Book a meeting with Alice next week"

Aexor understands you prefer 30-minute meetings on Tuesdays at 10 AM (from your history), fetches both calendars in parallel, finds overlapping slots, shows you three options, waits for your pick, creates the event only after you approve, and remembers this outcome for smarter suggestions next time.

And if something goes wrong — say, no overlapping slots are found, or a calendar API is temporarily down — Aexor doesn't just fail. It reasons about the problem, adapts its plan (maybe checking adjacent days or trying an alternative approach), and presents the updated options to you for approval before taking any action. The user always stays in control, even when the agent is recovering from the unexpected.

---

## Why I Built This

There's no shortage of AI agent products in 2026 — and many of them are genuinely impressive. But as I studied what's shipping, I kept noticing the same pattern: safety and control are being added as layers on top of systems that weren't designed for them. Approval prompts bolted onto autonomous loops. Credential management delegated to third-party workarounds. Audit trails that log what happened but can't explain *why* or reproduce the decision. These aren't bad products — they're solving hard problems in real time, and they're moving fast.

But I wanted to explore what happens when you design the other way around — when safety, auditability, and user control are the *starting point*, not afterthoughts. An agent that can book flights, manage your calendar, send messages, and monitor for availability, but where every plan is reproducible, every side-effecting action requires explicit human approval, every runtime adaptation is policy-governed and auditable, and the AI never touches your raw credentials at any stage.

That's what Aexor is — my attempt to build a personal AI agent where the trust model is the architecture, not a feature.

---

## What Makes Aexor Different

### Preview-First Safety
Every task goes through a read-only preview before anything happens. You see exactly what the agent will do — which slots are available, which flights match, what the message will say. Only after you explicitly approve does execution begin. No surprises.

### Plans That Are Reproducible and Auditable
When you make a request, Aexor generates a structured plan — not a loose chain of LLM calls. The same request with the same context produces the same plan every time. At runtime, the agent can adapt to failures and new information, but every adaptation is recorded in a full audit trail.

### The AI Never Sees Your Credentials
Your API keys and OAuth tokens are encrypted at rest and isolated from the AI at every stage. The agent works with references, never with raw secrets — even during execution.

### Policy-Bounded Execution
All runtime AI decisions are governed by an explicit policy layer that asks for approval by default. If the system isn't sure an action is safe, it asks you first. Write operations (creating events, sending emails, making purchases) always require human approval — this is enforced at the system level and can't be overridden.

### It Learns and Remembers
Aexor builds a privacy-respecting memory of your preferences, past actions, and patterns. Over time, it gets better at anticipating what you need and how you like things done — without storing raw PII.

### Extensible Connector Ecosystem
Adding a new integration (say, Notion, Linear, or a custom API) doesn't require changes to the core system — just register a new connector. The architecture is designed for a growing ecosystem of tools. Currently, I've integrated Composio as the connector layer and I'm actively adding more connectors as I expand the supported use cases.

### Where It Is Today
Aexor currently uses Anthropic's Claude Sonnet for its LLM needs — planning, reasoning, and adaptive execution. I'm also exploring local model support, since Anthropic has recently raised pricing and tightened rate limits for external integrations. For a self-hosted system where data sovereignty matters, having the option to run inference locally makes sense. The architecture already abstracts the LLM layer, so plugging in a local model is a matter of adding a new adapter, not rethinking the system.

---

## What Aexor Gets Right That Others Haven't

The personal AI agent space has exploded in early 2026 — OpenClaw (346K GitHub stars), NVIDIA's NemoClaw, Perplexity's Personal Computer ($200/month), and Anthropic's Claude Cowork are all shipping real products. They're impressive in their own ways. But after studying what they do well and where they struggle, I designed Aexor around five capabilities that I believe are the hard problems no one has fully solved yet.

### 1. Multi-Agent Orchestration — Not Just One AI Doing Everything

Most agent products today run a single LLM in a loop — it reasons, acts, observes, repeats. This works for simple tasks but breaks down for complex, multi-step workflows where different parts of the task need different capabilities.

Aexor takes a different approach. When you make a request, it breaks the task into a structured plan where specialized agent roles collaborate: one fetches data, another analyzes it, another reasons about trade-offs, another handles user decisions, and another executes writes. These roles work in parallel where possible, pass typed context to each other, and each operates under its own policy rules. It's not one AI doing everything sequentially — it's a coordinated team of specialized agents executing a shared plan.

This matters because a single-loop agent booking your flight, analyzing prices, and sending a Slack notification is doing three fundamentally different things with the same context and permissions. Aexor separates these concerns — the agent analyzing prices doesn't have write access, the agent sending notifications doesn't see your payment details, and the agent booking the flight requires your explicit approval. Separation of concerns, applied to AI.

### 2. Context Awareness — It Knows You, Not Just Your Request

When you say "book a meeting with Alice," most agents start from zero — they have the words you typed and nothing else. Aexor starts with context.

It maintains a layered memory system: your stable preferences (you prefer 30-minute meetings, you work 9-5 CT), your interaction history (you usually meet Alice on Tuesdays at 10 AM), and past successful plans (last time you booked with Alice, here's what worked). Before any planning begins, Aexor assembles the relevant context — budget-limited, typed, and privacy-tiered — so the plan it generates already reflects who you are and how you like things done.

Over time, this compounds. The tenth time you ask Aexor to schedule a meeting, it's working with ten meetings' worth of learned preferences. Products like OpenClaw have memory (persistent files and semantic search), and Perplexity has cross-session recall, but neither ties memory directly into a structured planning process the way Aexor does — where your history and preferences are first-class inputs that change the plan itself, not just conversational context the LLM might or might not use.

### 3. Security and Safety — Designed In, Not Patched On

This is where the current landscape has the biggest gaps, and where I spent the most design effort.

**Credential isolation:** In Aexor, the AI never sees your raw API keys or OAuth tokens — not during planning, not during reasoning, not during execution. Credentials are encrypted at rest and resolved only at the moment of execution, held in memory briefly, and zeroed after. This is architecturally different from products where the LLM has access to tokens in its context (OpenClaw's default), or where credential management relies on third-party integrations (Perplexity's 1Password setup, which users report needing workarounds for).

**Ask-approval-by-default policy governance:** Every runtime AI decision in Aexor is governed by an explicit policy layer. If the system isn't sure an action is safe, it asks you first — approval is the default, not the exception. Write operations — creating events, sending emails, making purchases — always require human approval, enforced at the system level. It can't be overridden by a clever prompt or a misconfigured plugin. OpenClaw had nine CVEs in four days in March 2026 (including CVSS 9.9) and has dealt with malicious plugins in its ClawHub store stealing credentials. NemoClaw addresses this with kernel-level sandboxing, which is strong for infrastructure isolation — but it doesn't govern what the AI *decides* to do, only where it's allowed to run. Aexor governs both.

**Preview-first safety:** Before any side-effecting action, Aexor runs a complete read-only preview. You see the actual calendar slots, the actual flight options, the actual email draft — fetched from real APIs but with zero writes. Only after you explicitly approve does execution begin. Other products have confirmation prompts (Cowork asks before destructive actions, Perplexity requires approval for sensitive actions), but none do a full read-only preview phase where you see real outcomes before committing.

### 4. Auditability — Every Decision Has a Paper Trail

When an AI agent books a meeting or sends an email on your behalf, you should be able to answer: *What did it do? Why did it do that? Could I reproduce this? What changed at runtime?*

Aexor produces a complete audit chain. The initial plan is deterministic and reproducible — same inputs, same plan, every time. If the agent adapts at runtime (say, a calendar API is down and it tries an alternative approach), that adaptation is recorded as a versioned policy attestation: what changed, why it was allowed, and which policy rule authorized it. The full execution — every step, every decision, every adaptation — is logged and correlated.

This is different from per-session logs (Perplexity), partial task boards (OpenClaw), or opaque execution (Cowork, where VM errors can be hard to trace). Aexor's audit trail isn't just logging — it's a provenance chain that links every action back to the policy that authorized it and the plan revision that introduced it.

For a platform like HighLevel, where agents would act on behalf of agency clients (sending their emails, managing their CRM records, booking their appointments), this kind of auditability isn't a nice-to-have — it's a requirement.

### 5. Personalization That Compounds Over Time

Every product in this space has some form of memory — OpenClaw's MEMORY.md files, Perplexity's cross-session recall, Cowork's Projects feature. What's different about Aexor is how memory integrates into the planning process.

Aexor's memory isn't just conversational context that the LLM sees in its prompt window. It's structured, typed data — preferences, interaction history, past plan outcomes — that directly shapes the plan the system generates. When Aexor has learned that you prefer 30-minute meetings on Tuesdays, that preference doesn't just float in a chat context hoping the LLM picks up on it — it's an explicit evidence item that the planner uses to generate a different (better) plan than it would for a new user.

This also means personalization is auditable. You can see exactly which preferences and history items influenced a specific plan. And it's privacy-tiered — you control what the system remembers, with explicit consent tiers and TTLs on historical data.

### Where Others Are Ahead (And That's OK)

To be fair about where Aexor isn't the best option today: OpenClaw's multi-channel messaging reach (12+ platforms, 5,400+ skills) is unmatched. Perplexity's 19-model orchestration and 400+ integrations with always-on 24/7 operation is the most ambitious consumer product shipping. Cowork's VM sandboxing and Computer Use give it flexibility to interact with any desktop app, not just API-connected services. NemoClaw's kernel-level isolation is stronger than application-level sandboxing.

Aexor is purpose-built for a specific class of problems: multi-step workflows across connected SaaS tools where safety, auditability, personalization, and user control matter more than breadth. It's narrower — but deeper.

---

## Why This Matters for HighLevel

HighLevel is the operating system for agencies — CRM, marketing automation, funnels, scheduling, all under one roof. Aexor is, in many ways, the agentic counterpart to that vision: an operating system for personal task execution across SaaS tools, with the same emphasis on workflow automation but with an AI-native, safety-first approach.

The problems I've been solving — how do you let an AI agent send emails, book appointments, and manage records on behalf of a user without breaking things or leaking data? How do you make AI actions auditable and reversible? How do you build trust with users who need to hand real authority to an AI? — are the same problems at every scale, and they're directly relevant to where HighLevel is heading.

---

## The Ask

I'd love for you to:

1. **Watch the recording** — it walks through the core flow end-to-end
2. **Share your honest feedback** — what resonates, what doesn't, what you'd build differently
3. **Try it yourself** (optional) — I can share the Docker image if you'd like to spin it up and poke around

I'm not pitching Aexor as a product to HighLevel. I'm showing you how I think about systems — safety, trust, user experience, and building something that works in the real world, not just in a demo. I believe this reflects the kind of thinking that would be valuable on your team.

Looking forward to your thoughts.

Best,
**Anantshree Chandola**
anantshreechandola23@gmail.com
