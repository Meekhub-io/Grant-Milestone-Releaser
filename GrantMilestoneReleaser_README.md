# GrantMilestoneReleaser

A [GenLayer](https://genlayer.com) intelligent contract that manages milestone-based grants. A grantee submits evidence (PRs, demos, reports) for a milestone, and validators use an LLM to judge whether the evidence satisfies the milestone's spec. If it does, the milestone's tranche is recorded as released.

## How It Works

1. **Owner creates a grant** with a grantee address, title, and total amount.
2. **Owner adds milestones** to the grant, each with a plain-language spec, a tranche amount, and a sequence number.
3. **Grantee submits evidence** for a milestone as a list of URLs plus optional notes.
4. The contract fetches each URL (up to 5, first 1,500 characters each) using `gl.nondet.web.get` and asks an LLM to evaluate the evidence strictly against the spec. The model returns:
   - `passed` (true/false)
   - `confidence` (0 to 100)
   - `reasoning` (2 to 3 sentences)
5. Validators independently re-run the evaluation. Consensus requires only that the **`passed` decision matches** the leader's. Confidence and reasoning may differ.
6. The result is written on-chain:
   - The submission is stored.
   - The milestone status becomes `passed` or `failed`, with the AI reasoning attached.
   - On a pass, the milestone is marked `released` and the grant's `released_usd` increases by the tranche amount.
7. **Owner can close** a grant at any time to stop further milestones and submissions.

## Important: Accounting Only

This contract **records** tranche releases; it does not hold or transfer funds. `released_usd` and `released: true` are bookkeeping values. To pay out, integrate it with a treasury or escrow that reads these results, or extend the contract with a token transfer.

## Data Model

All records are stored as JSON strings in `TreeMap`s.

### Grant (key: `grant_id`)

| Field | Description |
|---|---|
| `grant_id` | Unique ID, e.g. `grant-001` |
| `grantee_address` | Wallet address of the recipient |
| `title` | Project title |
| `total_usd` | Total grant amount (whole USD) |
| `released_usd` | Sum of tranches released so far |
| `milestone_count` | Number of milestones added |
| `active` | `false` once the grant is closed |

### Milestone (key: `milestone_id`)

| Field | Description |
|---|---|
| `milestone_id` | Unique ID, e.g. `grant-001-m1` |
| `grant_id` | Parent grant |
| `title` | Short name |
| `spec` | What must be delivered |
| `tranche_usd` | Amount released on pass (whole USD) |
| `sequence` | Ordering number (1, 2, 3...) |
| `status` | `pending`, `passed`, or `failed` |
| `released` | `true` once passed |
| `ai_reasoning` | Latest AI explanation |

### Submission (key: `submission_id`)

| Field | Description |
|---|---|
| `submission_id` | Unique ID for the attempt |
| `milestone_id`, `grant_id` | What it relates to |
| `evidence_urls` | Comma-separated URLs as submitted |
| `notes` | Grantee notes |
| `passed` | AI decision |
| `confidence` | AI confidence (0 to 100) |
| `reasoning` | AI explanation |

## Contract API

### Write methods

| Method | Caller | Description |
|---|---|---|
| `create_grant(grant_id, grantee_address, title, total_usd)` | Owner | Creates a grant. Reverts if the ID exists or `total_usd <= 0`. |
| `add_milestone(milestone_id, grant_id, title, spec, tranche_usd, sequence)` | Owner | Adds a milestone to an active grant. Reverts if the grant is missing/inactive, the ID exists, or `tranche_usd <= 0`. |
| `submit_milestone(submission_id, milestone_id, evidence_urls, notes)` | Grantee | Submits evidence and triggers AI evaluation. |
| `close_grant(grant_id)` | Owner | Marks the grant inactive. |

`submit_milestone` reverts if the milestone or grant is not found, the grant is inactive, the milestone has already passed, the caller is not the grantee, or no evidence URL is provided.

A milestone that **failed** can be resubmitted (with a new `submission_id`) until it passes.

### View methods

| Method | Returns |
|---|---|
| `get_grant(grant_id)` | Grant JSON, or `{"error": "Grant not found"}` |
| `get_milestone(milestone_id)` | Milestone JSON, or `{"error": "Milestone not found"}` |
| `get_submission(submission_id)` | Submission JSON, or `{"error": "Submission not found"}` |
| `get_released_usd(grant_id)` | Total USD released (int), `0` if not found |
| `get_milestone_status(milestone_id)` | `pending`, `passed`, `failed`, or `not found` |
| `get_owner()` | Owner address (hex string) |

## Usage Example

Using the GenLayer CLI (adjust to your tooling):

```bash
# Deploy
genlayer deploy --contract Grantmilestoneresaler.py

# Owner: create a grant and a milestone
genlayer write <CONTRACT_ADDRESS> create_grant \
  --args "grant-001" "0xGranteeAddress" "Open-source indexer" 10000

genlayer write <CONTRACT_ADDRESS> add_milestone \
  --args "grant-001-m1" "grant-001" "Alpha release" \
  "Public GitHub repo with a working indexer and README, plus a tagged v0.1.0 release" 3000 1

# Grantee: submit evidence
genlayer write <CONTRACT_ADDRESS> submit_milestone \
  --args "sub-001" "grant-001-m1" \
  "https://github.com/org/indexer,https://github.com/org/indexer/releases/tag/v0.1.0" \
  "Tagged release is live."

# Check results
genlayer call <CONTRACT_ADDRESS> get_milestone_status --args "grant-001-m1"
genlayer call <CONTRACT_ADDRESS> get_released_usd --args "grant-001"
```

## Writing Good Milestone Specs

The LLM judges strictly against `spec`, so specs should be concrete and checkable from public web pages. For example, "A public repo containing X, with a tagged release and a passing CI badge" works better than "Build the backend".

## Limitations and Things to Watch

- **No fund transfers.** See the accounting-only note above.
- **No budget check.** The contract does not verify that milestone tranches sum to `total_usd`, or that `released_usd` stays within it. The owner should keep tranches consistent with the grant total.
- **`sequence` is not enforced.** Milestones can be submitted in any order, and sequence values are not checked for uniqueness.
- **`confidence` is informational only.** Approval depends solely on `passed`; there is no minimum confidence threshold.
- **Evidence is limited to what validators can fetch.** Only the first 5 URLs and the first 1,500 characters of each are used. Private, login-gated, or JavaScript-rendered pages may fail to fetch (recorded as `[could not fetch]`).
- **Prompt injection risk.** Fetched pages and grantee notes are inserted into the prompt, so a malicious page could try to steer the model. Treat AI approval as a strong signal, not proof, for high-value grants.
- **`submission_id` is not checked for uniqueness**, so reusing an ID overwrites the earlier submission record.
- **`close_grant` does not undo anything.** Already released tranches stay recorded.
- **No owner transfer or dispute process.** There is no appeal or manual override if the AI decision is disputed; consider adding one.
- **Public data.** All records, including AI reasoning and notes, are readable on-chain.

## Requirements

- GenLayer runtime with `py-genlayer` (pinned in the contract header dependency hash)
- Validator network access to the evidence URLs

## License

Add your preferred license here.
