# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Two groups.

**Working users (the app).** Staff of a national competition authority in Uzbekistan, using it inside a government office, on desktop mostly, sometimes on a phone. Three roles: *xodim* (officer: asks legal questions, analyses a complaint, drafts a reply), *rahbar* (manager: reads workload, overdue items, today's problems, assigns tasks), *administrator* (maintains users, the statute base, the letterhead, system health). Their job: answer a legal question correctly and quickly, with the article in hand, and produce an official letter that can go out.

**Evaluators (the public showcase).** Leadership at the authority and recruiters or engineers who open one link, usually from Telegram or LinkedIn, and decide in under a minute whether this is real. They read Uzbek or English.

## Product Purpose
A grounded legal assistant. A question goes in; the system finds the right article of the statute, writes the answer from that text only, and checks every article, clause, number, date and quote against the source before showing anything. If the check fails the model text is dropped and the statute's own passage is shown. Around that core: analysis of uploaded case documents, official response letters exported to Word with the authority's letterhead, and a manager dashboard computed from the database.

Success: an officer trusts an answer enough to act on it because the proof sits beside it; a visitor understands that in one screen.

## Positioning
Most legal AI shows what a model said. This one shows what the law says and proves the model did not stray: a fourth stage (verification) that ordinary retrieval-augmented systems do not have, plus answers that need no model at all (statutory lists verbatim, article counts, manager analytics) and answer in milliseconds.

## Operating Context
Official documents, article and clause numbers, Cyrillic statute text next to Latin-script Uzbek answers, outgoing letter numbers, letterhead, signatories. Source cards with an official link, article label and the exact passage. The public demo runs on the author's laptop behind a read-only gateway and is sometimes offline; the showcase page stays up either way.

## Capabilities and Constraints
- Uzbek (Latin) UI. Statute text is stored in Cyrillic and shown as is on source cards.
- Statute base: one law indexed, 49 articles, 115 passages. Embeddings BAAI/bge-m3, pgvector, language model gpt-oss-120b (Groq or self-hosted vLLM).
- Roles and permissions, audit log and the demo gateway are product behaviour and must not change with a redesign.
- Public demo: login `demo` only, read-and-ask, 30 AI calls per hour per visitor.
- Frontend: React 19 + TypeScript + Vite; showcase page is a single static HTML file on Cloudflare Pages.

## Brand Commitments
None that are visual. The user confirmed on 2026-10-27 that nothing visual is constant (colour, logo mark, type, layout). The product is named Raqobat AI Assistant. The user's stated failure condition: anything that reads as a template or as "AI-generated design".

## Evidence on Hand
Real screenshots of the running app (`docs/images/`, `deploy/cloudflare/site/img/`), real answers with article labels, 193 automated tests, a timed demo script (`docs/demo-script.uz.md`). No customer testimonials, no usage numbers, no production deployment: none may be invented.

## Product Principles
1. The proof is part of the answer, never a footnote: source and verification sit next to every claim.
2. Say what is not known. "No basis found" and "model offline" are honest states, designed, not hidden.
3. Speed of comprehension over decoration: an officer under deadline reads, decides, acts.
4. Official, not corporate: this is state work in Uzbek, and it should look like it belongs there without looking like a ministry website from 2009.
5. Everything the demo shows is true to what the system does today.

## Accessibility & Inclusion
Readable on phones, visible keyboard focus, text at 12.5 px or more, touch targets 44 px on phones, never colour alone for status.
