# CLAUDE.md

Persistent instructions for the agent in this repository. Read at the start of every session.

Convention: **NEVER** and **ALWAYS** appear only in non-negotiable rules. All other rules are guidelines that require judgment.

Terminology: **TDD** in this file means **Technical Design Doc** (upstream document, section 3). Test-driven development is always written **test-first**.

---

## 1. Principles

- **Simplicity first:** the smallest change that solves the problem. Do not refactor, rename, or "improve" code outside the requested scope.
- **Root cause:** no band-aids. Reproduce the problem, find its origin, and fix it there. If only a workaround is possible, say so explicitly and record the pending item.
- **Evidence before claims:** nothing is "done" without proof (passing test, log, command output). If you did not run it, say you did not run it.
- **A change without a test is not complete:** every behavior change comes with a test that fails before and passes after.
- **No hidden loose ends:** no `TODO`/`FIXME` without a reference to an issue or an item in `plan/tasks/`.
- **Traceability:** every change traces back to a Linear task, and through it to a Project, a TDD, and a PRD. Do not implement work that has no task.

---

## 2. Project commands

Always use the commands below. Do not discover them by trial and error. Fill in once per project.

| Action | Command |
|---|---|
| Lint | `<fill in>` |
| Check formatting | `<fill in>` |
| Type check (if any) | `<fill in>` |
| Tests | `<fill in>` |
| **Full verification** (equivalent to CI) | `<fill in>` |

If this table is not filled in, find the commands by reading `Makefile`, `justfile`, `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, or `.github/workflows/`, fill in the table, and ask the user to confirm.

CI (`.github/workflows/`) runs lint and tests on every PR to `develop` and `main`. Local pre-commit does **not** replace CI.

---

## 3. Delivery flow: from business to code

Work reaches the agent through a chain of upstream artifacts. The agent's own flow (section 5) starts only when a **Linear task** exists.

```
Business:   PRD
              │
Research ──▶ TDD (Technical Design Doc) ◀── RFC, ADR (attached)
                          │
                          ▼
Linear:     Project ──▶ TASK 1, TASK 2, ...
                          │   (one task at a time)
                          ▼
Agent:      SPEC ─▶ DESIGN ─▶ TASKS ─▶ test-first ─▶ CODING ─▶ ... ─▶ PR
```

### Upstream artifacts

| Artifact | Owner | Purpose | Agent's role |
|---|---|---|---|
| **PRD** | Business / product | What and why. Business requirements. | **Read only.** Never edit. |
| **Research** | Human or agent | Investigation that feeds the TDD (options, constraints, prior art). | May produce, with user confirmation. |
| **TDD** (Technical Design Doc) | Human, agent may draft | How the solution is built at system level. | Read. May draft only when asked. |
| **RFC** | Human, agent may draft | Proposal for a change that needs discussion. Attached to the TDD. | Read. May draft only when asked. |
| **ADR** | Human approves | Record of an architectural decision and its rationale. Attached to the TDD. | Read. **Propose** new ADRs; never mark one as accepted. |
| **Linear Project** | Human | Groups the tasks that deliver a TDD. Links to the TDD. | Read. |
| **Linear Task** | Human, agent may draft | Unit of work. One task becomes one iteration of the agent flow. | Read, comment, update status. |

Where the documents live (Linear, repository `docs/`, or elsewhere) is defined per project. If you cannot find an upstream artifact, ask the user for the link. Do not guess its content.

### Entry gate (check before SPEC)

Before starting a non-trivial task, confirm all of the following:

1. The Linear task is identified (ID and link).
2. The task belongs to a Project, and the Project links a TDD.
3. The TDD's relevant RFCs and ADRs were read.
4. The PRD requirement the task serves is identified.

If any item is missing, **stop and ask the user**. Do not fill the gap with assumptions. If a Linear integration is available, use it to read the task, Project, and linked documents.

### Upstream conflicts

- If the task, TDD, ADR, and PRD disagree with each other, or the task cannot be done within the TDD/ADR constraints, **stop and escalate**. Present the conflict with options and your recommendation.
- Never edit upstream artifacts silently. A deviation from an accepted ADR needs the user's explicit approval and a new ADR proposal.
- If the correct fix is in an upstream artifact, say so and let the user decide who changes it.

---

## 4. When to use the full flow

### Trivial task (fast path)

Applies when **all** of the following are true:

- touches at most 2 files;
- does not change a public interface, data schema, infrastructure, or CI;
- the cause is obvious and the expected behavior is unambiguous;
- does not contradict or require a change to a TDD, RFC, or ADR.

In that case: make the change, write or adjust the test, run the full verification, and go on to Git. Do **not** create artifacts in `plan/`. If a Linear task exists, still reference it in the branch, commit, and PR.

### Non-trivial task (full flow)

Applies when **any** of the following is true:

- touches more than 2 files;
- changes a public interface, data schema, infrastructure, dependencies, or CI;
- involves an architectural decision;
- the cause of a bug is not obvious;
- you have real doubt about the scope.

In that case, pass the entry gate (section 3) and follow section 5.

### Autonomy versus confirmation

- **Autonomous:** bug with a clear cause and contained scope; broken CI; failing tests. Fix without asking for guidance.
- **Confirm first:** scope change, architectural decision, new dependency, CI change, any deviation from a TDD/RFC/ADR, any destructive operation (see section 10).
- If something goes off-plan midway, **stop and re-plan** instead of pushing on.

---

## 5. Full flow (per Linear task)

Mandatory sequence for non-trivial tasks:

**SPEC → DESIGN → TASKS → TEST-FIRST → CODING → REFACTOR → LINTER → SAFETY → REVIEW → SESSION**

| Step | What to do | Artifact |
|---|---|---|
| 1. SPEC | Requirements, constraints, assumptions, acceptance criteria, out of scope. Link the Linear task, the PRD requirement, and the TDD/ADR/RFC that apply. **Ask the user to confirm before moving on.** | `plan/sdd/spec-<TS>.md` |
| 2. DESIGN | How this task will be built, within the TDD's boundaries: approach, components touched, interfaces and data, alternatives considered, risks. Must be consistent with the accepted ADRs. Any new architectural decision is proposed as an ADR, not buried here. **Ask the user to confirm before moving on** when the design is non-obvious or touches a public interface. | `plan/design/design-<TS>.md` |
| 3. TASKS | Break the design into small, verifiable items (checklist), covering every component listed in the design. Also list the planned tests, each tied to an acceptance criterion. Mark items as you go. These are the agent's work items, not Linear tasks. If the breakdown shows the Linear task is too big, propose splitting it in Linear. | `plan/tasks/todo-<TS>.md` |
| 4. TEST-FIRST | Write or update tests for the expected behavior and edge cases **before** implementing. Confirm they fail for the right reason. | tests in the repository |
| 5. CODING | Minimal implementation that satisfies the spec, design, and tests. | code |
| 6. REFACTOR | Improve structure, duplication, and readability **without changing behavior**. Tests stay green. | code |
| 7. LINTER | Run formatter, linter, and type checker. Fix issues. Never suppress a warning without a justification in the code. | code |
| 8. SAFETY | Audit input validation, permissions, secret handling, sensitive data, and destructive operations. | notes in REVIEW |
| 9. REVIEW | Critically evaluate against the spec, design, acceptance criteria, and edge cases. Check conformance with the TDD and ADRs. Attach the full verification output. Verdict: PASS, FAIL-IMPL, FAIL-DESIGN, or FAIL-SPEC. | `plan/reviews/review-<TS>.md` |
| 10. SESSION | Summary of what changed, test results, decisions, and pending items. Also save when switching tasks or ending the session. | `plan/sessions/session-<TS>.md` |

Before step 1 and before step 5, **check the available skills** (section 11).
For SPEC, DESIGN, TASKS, REVIEW, and SESSION, **copy the template** from `plan/templates/` and fill it in. Do not invent another format.

During the flow:

- Explain changes at a high level at each step. Do not dump diffs.
- Before presenting non-trivial work, ask yourself whether a simpler or clearer solution exists. For simple, obvious fixes, skip this.
- When concluding REVIEW with PASS, write a **verification checklist** in the review with the commands run and their results.
- When a step finishes, fill in the **Result** section of its artifact (what was done, deviations from the plan, and why).
- Keep the Linear task updated: move it to in progress at SPEC, and comment with the PR link at the end. Do not mark it as done; that happens on merge, by the user.

---

## 6. Handling REVIEW failures

The verdict determines where to go back to. Do not restart everything over a small error.

| Verdict | Situation | Action |
|---|---|---|
| **PASS** | Acceptance criteria met, full verification green, SAFETY with no open items | Save SESSION, then commit, push, and open a PR (section 7). **Do not** merge. |
| **FAIL-IMPL** | Implementation defect: broken test, lint, uncovered edge case, bug in the code | Go back to **CODING** (or to TEST-FIRST if a test is missing). Keep the same timestamp. |
| **FAIL-DESIGN** | The design does not hold up (wrong boundary, violates an ADR, missing component) but the requirements are right | Go back to **DESIGN**. Keep the same timestamp. Note what changed in the design's Result section. |
| **FAIL-SPEC** | Wrong, incomplete, or ambiguous requirement; architectural failure | Go back to **SPEC** with a **new timestamp**. Record in the new spec what the previous one got wrong. |

If the root of the failure is in the TDD, an RFC, an ADR, or the Linear task itself, **escalate to the user** (section 3, "Upstream conflicts") instead of working around it.

**Iteration limit:** after 3 FAIL cycles on the same task, **stop and escalate to the user** with a summary of what was tried, what failed, and the remaining hypotheses. Do not keep looping.

---

## 7. Git and Pull Requests

Model: Gitflow.

1. **Branches** always from `develop`, with a prefix and the Linear issue ID when there is one:
   - `feature/<ISSUE-ID>-<slug>`: new functionality
   - `fix/<ISSUE-ID>-<slug>`: fix
   - `bug/<ISSUE-ID>-<slug>`: fix for a reported bug
   - `chore/<ISSUE-ID>-<slug>`: maintenance, dependencies, configuration
2. **Commits** follow Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`). One commit per logical change. Reference the Linear issue ID in the commit footer. Pre-commit must be green.
3. **Push and PR** against `develop`: `gh pr create --base develop`.
4. **PR description** includes: change summary, Linear task link, path to the spec (`plan/sdd/spec-<TS>.md`), path to the design, path to the review, applicable TDD/ADR/RFC references, and how it was verified.
5. **Small, focused PRs:** one Linear task per PR. If the change grows beyond the spec's scope, split it into separate PRs.
6. **NEVER merge** via the CLI or the API. Create the PR and leave the merge to the user, so that human review is guaranteed.
7. The PR is only ready when CI (`lint` and `test`) is green.

---

## 8. Memory: AI Memory and `plan/`

### Division of responsibilities

| Where | What to store | Role |
|---|---|---|
| **AI Memory** | Architectural decisions, reusable lessons, discoveries, and conventions that matter for future sessions | **Source of truth** for persistent knowledge |
| **`plan/`** | Specs, designs, tasks, reviews, and sessions of the iteration in progress | Work log and traceability |
| **`plan/tasks/lessons-<TS>.md`** | Raw lesson after a user correction | Draft. Promote to AI Memory and keep only the reference. |
| **ADRs / TDD (upstream)** | Formal architectural decisions and system design | Source of truth for architecture. AI Memory only keeps a pointer and the practical consequence. |

### Usage rules

- **Before** starting relevant work, query AI Memory. Do not assume earlier decisions or rediscover what was already recorded.
- **After** a user correction, architectural decision, discovery, or reusable lesson: record it in AI Memory and create `plan/tasks/lessons-<TS>.md` with the error pattern and the rule to avoid it.
- Be concise. Record only what will help in future sessions. Do not record logs, command output, or details the code already shows.
- At session start, review the lessons relevant to the project.
- Reference documentation: https://github.com/akitaonrails/ai-memory/tree/main/docs

### Fallback if AI Memory is unavailable

1. Tell the user in one line.
2. Record what would have been saved in `plan/memory-pending-<TS>.md`.
3. Continue working normally.
4. When the service is back, sync the pending content and delete the file.

---

## 9. Subagents, templates, and file naming

### When to use subagents

**Use** for:

- research or exploration across many files (including reading a TDD with many RFCs/ADRs);
- independent analyses that can run in parallel;
- tasks that would pollute the main context with bulky output.

**Avoid** for:

- small or sequential edits where each step depends on the previous one;
- tasks where the cost of passing context outweighs the gain.

One task per subagent, with a defined goal and return format.

### Naming

- Timestamp: `YYYYMMDDHHmm`, 24h (example: `202610011234`).
- **SPEC, DESIGN, TASKS, REVIEW, and SESSION of the same iteration share the same timestamp.** A new timestamp is only created with a new SPEC (FAIL-SPEC or a new initiative).
- `lessons-<TS>` uses the timestamp of the moment of the correction.
- **Forbidden:** generic names such as `todo.md`, `spec.md`, `design.md`, `review.md`, or `session.md`. The `*.template.md` files in `plan/templates/` are the only exception.

### Structure of `plan/`

```
plan/
├── templates/  spec / design / todo / review / session  (*.template.md)
├── sdd/        spec-<TS>.md
├── design/     design-<TS>.md
├── tasks/      todo-<TS>.md, lessons-<TS>.md
├── reviews/    review-<TS>.md
└── sessions/   session-<TS>.md
```

### Template conventions

- Templates are models, not iteration artifacts. Never fill them in place: copy to the right folder with the iteration's timestamp.
- Every artifact starts with a header linking back to the previous one (`SPEC: plan/sdd/spec-<TS>.md`, and `DESIGN: plan/design/design-<TS>.md` from TASKS onward) plus the Linear task.
- `design.template.md`: Approach, Components touched, Interfaces and data, Alternatives considered, Risks and edge cases, Conformance (TDD, ADRs, new ADR needed?), Result.
- `todo.template.md`: Items (small, verifiable, covering the design's components), Planned tests (test-first, each tied to an acceptance criterion), Result.
- Every template ends with a **Result** section, filled in when the step is done.

### Retention

`plan/` is **versioned** in the repository, since it gives the PR traceability. Files from iterations completed more than 90 days ago may be moved to `plan/archive/` with the user's confirmation. Never delete artifacts without confirming.

---

## 10. Security prohibitions

**NEVER**, without explicit authorization from the user in the current conversation:

- `git push --force` (including `--force-with-lease`) on `develop` or `main`;
- rewrite already published history (`rebase`, `reset --hard`, `commit --amend` on pushed commits);
- `rm -rf` outside temporary directories created by the task itself;
- change `.github/workflows/`, CI configuration, hooks, or permissions without warning and explaining why;
- commit `.env`, keys, tokens, credentials, or any secret. If you find a secret in the repository, warn the user instead of fixing it silently;
- print secrets in logs, output, PR descriptions, or Linear comments;
- install new dependencies without confirming;
- run migrations or destructive commands on databases or environments that are not local test ones;
- disable tests, lint, or CI checks to make something pass;
- edit the PRD, or mark an ADR as accepted;
- delete or restructure Linear Projects or Tasks, or mark a task as done.

When in doubt about whether an operation is destructive or irreversible, **ask first**.

Part of these rules is enforced by `.claude/settings.json` and `.claude/hooks/guard-bash.sh`. If a command is blocked, do not try to work around it: explain to the user what you needed to do and ask for authorization.

---

## 11. Skills

Skills are specialized instructions in `.claude/skills/<name>/SKILL.md` (project) and `~/.claude/skills/<name>/SKILL.md` (personal). Each has a `description` that says when it applies.

### When to check

- **Before SPEC:** list the available skills and identify the ones that apply to the task.
- **Before CODING:** reread the applicable skills. If the scope changed during SPEC, DESIGN, or TASKS, repeat the search.
- **In REVIEW:** confirm that the applicable skills were followed.

### How to check

1. List the directories in `.claude/skills/` and `~/.claude/skills/`.
2. Read each `SKILL.md`'s `description`. Do not read the body of all of them.
3. For each skill whose `description` matches the task, read the full `SKILL.md` and follow its instructions **before** acting.
4. If more than one applies, read all of them. On conflict, the project skill prevails over the personal one, and this CLAUDE.md prevails over both.

### Recording

- In the SPEC, include the **Applicable skills** section with the names of the skills used (or "none").
- In the REVIEW, mark whether each listed skill was followed. If any was ignored, justify it.

### Rules

- Do not invent skills. If none applies, move on without comment.
- Do not load irrelevant skills just in case: that wastes context.
- If a skill contradicts a prohibition in section 10, section 10 prevails. Warn the user.
- If you identify a repetitive task that deserves to become a skill, suggest it to the user instead of creating it yourself.

---

## 12. Definition of done

A non-trivial task is only "done" when **all** items are true. Check each one in the REVIEW.

- [ ] Entry gate (section 3) passed: Linear task, Project, TDD, and PRD requirement identified.
- [ ] Full verification (section 2) executed and green, with the output attached.
- [ ] Every new or changed behavior has a test that failed before and passes now.
- [ ] SPEC acceptance criteria met, one by one.
- [ ] Implementation conforms to the DESIGN, the TDD, and the accepted ADRs, or the deviation was approved by the user.
- [ ] No `TODO`/`FIXME` without a reference to an issue or a `plan/tasks/` item.
- [ ] SAFETY completed, with no secrets, unvalidated inputs, or destructive operations.
- [ ] Documentation updated if the public interface, configuration, or usage changed.
- [ ] SPEC, DESIGN, TASKS, REVIEW, and SESSION exist with the same timestamp, each with its Result section filled in.
- [ ] PR describes the change and links the Linear task, SPEC, DESIGN, and REVIEW.
- [ ] Reusable lessons and decisions recorded in AI Memory. New architectural decisions proposed as ADRs.

For trivial tasks, only the full verification, the test, the absence of loose ends, and SAFETY items apply.

---

## 13. Communication

When reporting results to the user:

- **Start with the real state:** done, partial, or blocked. If any test failed or was not run, say so in the first line.
- **Separate fact from assumption:** "verified" only for what was executed or read. The rest is "assumed" or "not verified".
- **Report failures without softening:** include the exact error and what was already tried.
- **Be short:** high-level summary, artifact paths, and pending items. Do not paste diffs or long logs; point to where they are.
- **Ask for decisions with options:** when escalating, present alternatives with pros and cons and your recommendation.
- **Name the upstream reference:** when a decision depends on a TDD, RFC, ADR, or PRD item, cite which one.
- **No excessive praise or apologies.** Fix and move on.

---

## 14. Maintaining this file

- A lesson that repeats **3 times** becomes a rule here. Record the promotion in AI Memory.
- A rule that never influences behavior, or that has become a habit of the agent, should be removed.
- Detail for a step that goes beyond a few lines becomes a skill or slash command, and only the summary and link stay here.
- Keep the file under roughly 300 lines.