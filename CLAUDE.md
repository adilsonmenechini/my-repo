# CLAUDE.md

Persistent instructions for the agent in this repository. Read at the start of every session.

Convention: **NEVER** and **ALWAYS** appear only in non-negotiable rules. All other rules are guidelines that require judgment.

---

## 1. Principles

- **Simplicity first:** the smallest change that solves the problem. Do not refactor, rename, or "improve" code outside the requested scope.
- **Root cause:** no band-aids. Reproduce the problem, find its origin, and fix it there. If only a workaround is possible, say so explicitly and record the pending item.
- **Evidence before claims:** nothing is "done" without proof (passing test, log, command output). If you did not run it, say you did not run it.
- **A change without a test is not complete:** every behavior change comes with a test that fails before and passes after.
- **No hidden loose ends:** no `TODO`/`FIXME` without a reference to an issue or an item in `plan/tasks/`.

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

## 3. When to use the full flow

### Trivial task (fast path)

Applies when **all** of the following are true:

- touches at most 2 files;
- does not change a public interface, data schema, infrastructure, or CI;
- the cause is obvious and the expected behavior is unambiguous.

In that case: make the change, write or adjust the test, run the full verification, and go on to Git. Do **not** create artifacts in `plan/`.

### Non-trivial task (full flow)

Applies when **any** of the following is true:

- touches more than 2 files;
- changes a public interface, data schema, infrastructure, dependencies, or CI;
- involves an architectural decision;
- the cause of a bug is not obvious;
- you have real doubt about the scope.

In that case, follow section 4.

### Autonomy versus confirmation

- **Autonomous:** bug with a clear cause and contained scope; broken CI; failing tests. Fix without asking for guidance.
- **Confirm first:** scope change, architectural decision, new dependency, CI change, any destructive operation (see section 9).
- If something goes off-plan midway, **stop and re-plan** instead of pushing on.

---

## 4. Full flow

Mandatory sequence for non-trivial tasks:

**SPEC → TASK → TDD → CODING → REFACTOR → LINTER → SAFETY → REVIEW → SESSION**

| Step | What to do | Artifact |
|---|---|---|
| 1. SPEC | Requirements, constraints, assumptions, acceptance criteria, out of scope. **Ask the user to confirm before moving on.** | `plan/sdd/spec-<TS>.md` |
| 2. TASK | Break into small, verifiable items (checklist). Mark them as you go. | `plan/tasks/todo-<TS>.md` |
| 3. TDD | Write or update tests for the expected behavior and edge cases **before** implementing. Confirm they fail for the right reason. | tests in the repository |
| 4. CODING | Minimal implementation that satisfies the spec and tests. | code |
| 5. REFACTOR | Improve structure, duplication, and readability **without changing behavior**. Tests stay green. | code |
| 6. LINTER | Run formatter, linter, and type checker. Fix issues. Never suppress a warning without a justification in the code. | code |
| 7. SAFETY | Audit input validation, permissions, secret handling, sensitive data, and destructive operations. | notes in REVIEW |
| 8. REVIEW | Critically evaluate against the spec, acceptance criteria, and edge cases. Attach the full verification output. Verdict: PASS, FAIL-IMPL, or FAIL-SPEC. | `plan/reviews/review-<TS>.md` |
| 9. SESSION | Summary of what changed, test results, decisions, and pending items. Also save when switching tasks or ending the session. | `plan/sessions/session-<TS>.md` |

Before step 1 and before step 4, **check the available skills** (section 10).
For SPEC, TASK, REVIEW, and SESSION, **copy the template** from `plan/templates/` and fill it in. Do not invent another format.

During the flow:

- Explain changes at a high level at each step. Do not dump diffs.
- Before presenting non-trivial work, ask yourself whether a simpler or clearer solution exists. For simple, obvious fixes, skip this.
- When concluding REVIEW with PASS, write a **verification checklist** in the review with the commands run and their results.

---

## 5. Handling REVIEW failures

The verdict determines where to go back to. Do not restart everything over a small error.

| Verdict | Situation | Action |
|---|---|---|
| **PASS** | Acceptance criteria met, full verification green, SAFETY with no open items | Save SESSION, then commit, push, and open a PR (section 6). **Do not** merge. |
| **FAIL-IMPL** | Implementation defect: broken test, lint, uncovered edge case, bug in the code | Go back to **CODING** (or to TDD if a test is missing). Keep the same timestamp. |
| **FAIL-SPEC** | Wrong, incomplete, or ambiguous requirement; architectural failure | Go back to **SPEC** with a **new timestamp**. Record in the new spec what the previous one got wrong. |

**Iteration limit:** after 3 FAIL cycles on the same task, **stop and escalate to the user** with a summary of what was tried, what failed, and the remaining hypotheses. Do not keep looping.

---

## 6. Git and Pull Requests

Model: Gitflow.

1. **Branches** always from `develop`, with a prefix:
   - `feature/<slug>`: new functionality
   - `fix/<slug>`: fix
   - `bug/<slug>`: fix for a reported bug
   - `chore/<slug>`: maintenance, dependencies, configuration
2. **Commits** follow Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`). One commit per logical change. Pre-commit must be green.
3. **Push and PR** against `develop`: `gh pr create --base develop`.
4. **PR description** includes: change summary, path to the spec (`plan/sdd/spec-<TS>.md`), path to the review, and how it was verified.
5. **Small, focused PRs:** if the change grows beyond the spec's scope, split it into separate PRs.
6. **NEVER merge** via the CLI or the API. Create the PR and leave the merge to the user, so that human review is guaranteed.
7. The PR is only ready when CI (`lint` and `test`) is green.

---

## 7. Memory: AI Memory and `plan/`

### Division of responsibilities

| Where | What to store | Role |
|---|---|---|
| **AI Memory** | Architectural decisions, reusable lessons, discoveries, and conventions that matter for future sessions | **Source of truth** for persistent knowledge |
| **`plan/`** | Specs, tasks, reviews, and sessions of the iteration in progress | Work log and traceability |
| **`plan/tasks/lessons-<TS>.md`** | Raw lesson after a user correction | Draft. Promote to AI Memory and keep only the reference. |

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

## 8. Subagents and file naming

### When to use subagents

**Use** for:

- research or exploration across many files;
- independent analyses that can run in parallel;
- tasks that would pollute the main context with bulky output.

**Avoid** for:

- small or sequential edits where each step depends on the previous one;
- tasks where the cost of passing context outweighs the gain.

One task per subagent, with a defined goal and return format.

### Naming

- Timestamp: `YYYYMMDDHHmm`, 24h (example: `202610011234`).
- **SPEC, TASK, REVIEW, and SESSION of the same iteration share the same timestamp.** A new timestamp is only created with a new SPEC (FAIL-SPEC or a new initiative).
- `lessons-<TS>` uses the timestamp of the moment of the correction.
- **Forbidden:** generic names such as `todo.md`, `spec.md`, `review.md`, or `session.md`. The `*.template.md` files in `plan/templates/` are the only exception.

### Structure of `plan/`

```
plan/
├── templates/  *.template.md (models; not iteration artifacts)
├── sdd/        spec-<TS>.md
├── tasks/      todo-<TS>.md, lessons-<TS>.md
├── reviews/    review-<TS>.md
└── sessions/   session-<TS>.md
```

### Retention

`plan/` is **versioned** in the repository, since it gives the PR traceability. Files from iterations completed more than 90 days ago may be moved to `plan/archive/` with the user's confirmation. Never delete artifacts without confirming.

---

## 9. Security prohibitions

**NEVER**, without explicit authorization from the user in the current conversation:

- `git push --force` (including `--force-with-lease`) on `develop` or `main`;
- rewrite already published history (`rebase`, `reset --hard`, `commit --amend` on pushed commits);
- `rm -rf` outside temporary directories created by the task itself;
- change `.github/workflows/`, CI configuration, hooks, or permissions without warning and explaining why;
- commit `.env`, keys, tokens, credentials, or any secret. If you find a secret in the repository, warn the user instead of fixing it silently;
- print secrets in logs, output, or PR descriptions;
- install new dependencies without confirming;
- run migrations or destructive commands on databases or environments that are not local test ones;
- disable tests, lint, or CI checks to make something pass.

When in doubt about whether an operation is destructive or irreversible, **ask first**.

Part of these rules is enforced by `.claude/settings.json` and `.claude/hooks/guard-bash.sh`. If a command is blocked, do not try to work around it: explain to the user what you needed to do and ask for authorization.

---

## 10. Skills

Skills are specialized instructions in `.claude/skills/<name>/SKILL.md` (project) and `~/.claude/skills/<name>/SKILL.md` (personal). Each has a `description` that says when it applies.

### When to check

- **Before SPEC:** list the available skills and identify the ones that apply to the task.
- **Before CODING:** reread the applicable skills. If the scope changed during SPEC or TASK, repeat the search.
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
- If a skill contradicts a prohibition in section 9, section 9 prevails. Warn the user.
- If you identify a repetitive task that deserves to become a skill, suggest it to the user instead of creating it yourself.

---

## 11. Definition of done

A non-trivial task is only "done" when **all** items are true. Check each one in the REVIEW.

- [ ] Full verification (section 2) executed and green, with the output attached.
- [ ] Every new or changed behavior has a test that failed before and passes now.
- [ ] SPEC acceptance criteria met, one by one.
- [ ] No `TODO`/`FIXME` without a reference to an issue or a `plan/tasks/` item.
- [ ] SAFETY completed, with no secrets, unvalidated inputs, or destructive operations.
- [ ] Documentation updated if the public interface, configuration, or usage changed.
- [ ] SPEC, TASK, REVIEW, and SESSION exist with the same timestamp.
- [ ] PR describes the change and links the SPEC and REVIEW.
- [ ] Reusable lessons and decisions recorded in AI Memory.

For trivial tasks, only items 1, 2, 4, and 5 apply.

---

## 12. Communication

When reporting results to the user:

- **Start with the real state:** done, partial, or blocked. If any test failed or was not run, say so in the first line.
- **Separate fact from assumption:** "verified" only for what was executed or read. The rest is "assumed" or "not verified".
- **Report failures without softening:** include the exact error and what was already tried.
- **Be short:** high-level summary, artifact paths, and pending items. Do not paste diffs or long logs; point to where they are.
- **Ask for decisions with options:** when escalating, present alternatives with pros and cons and your recommendation.
- **No excessive praise or apologies.** Fix and move on.

---

## 13. Maintaining this file

- A lesson that repeats **3 times** becomes a rule here. Record the promotion in AI Memory.
- A rule that never influences behavior, or that has become a habit of the agent, should be removed.
- Detail for a step that goes beyond a few lines becomes a skill or slash command, and only the summary and link stay here.
- Keep the file under roughly 300 lines.
