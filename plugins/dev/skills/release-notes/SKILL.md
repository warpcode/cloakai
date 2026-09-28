---
name: release-notes
description: Turn a range of commits into release notes a user can act on — grouped by kind, with breaking changes and migrations called out first.
---

# Release notes

Write release notes for the range of commits given to you. The reader is deciding whether to upgrade
and what will break, so lead with that.

## Structure

1. **Breaking changes.** First, always, even when empty (then say "None"). For each: what broke, who
   is affected, the exact migration.
2. **Added / Changed / Fixed / Deprecated / Removed.** Grouped, most user-visible first. Omit empty
   groups.
3. **Upgrade notes.** Only when something must be done to upgrade — config keys to add, commands to
   re-run, caches to clear.

## Per-entry discipline

- One line per entry. The line is a claim about user-visible behaviour, not a description of the diff.
- Link the PR or issue when you have it.
- No commit-hash dumps, no "various bug fixes", no restating the changelog in prose.

## Judgement calls

- A commit that changes only tests, formatting, or internal comments is not a user-visible change. It
  does not appear.
- A rename with no behavioural difference goes under Changed, not Breaking.
- A default value that flips is Breaking, whatever the intent.
- If a change is ambiguous about user impact, include it under Changed with an explicit note that the
  impact is unclear, rather than dropping it silently.

## Never

Invent a version number, a date, or a contributor. If they were not given to you, leave a `TODO`
rather than guessing.
