# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json


class GrantMilestoneReleaser(gl.Contract):
    # ----------------------------------------------------------------
    # Storage
    # ----------------------------------------------------------------
    owner: Address                    # grant program admin
    grants: TreeMap[str, str]         # grant_id -> JSON grant record
    milestones: TreeMap[str, str]     # milestone_id -> JSON milestone record
    submissions: TreeMap[str, str]    # submission_id -> JSON submission record

    def __init__(self):
        self.owner = gl.message.sender_address

    # ----------------------------------------------------------------
    # Write: owner creates a grant with tranches
    # ----------------------------------------------------------------

    @gl.public.write
    def create_grant(
        self,
        grant_id: str,
        grantee_address: str,
        title: str,
        total_usd: int,
    ) -> None:
        """
        Create a new grant record.

        grant_id        : unique identifier, e.g. "grant-001"
        grantee_address : wallet address of the recipient
        title           : plain-language project title
        total_usd       : total grant amount in whole US dollars
        """
        if gl.message.sender_address != self.owner:
            raise Exception("Only the owner can create grants")
        if grant_id in self.grants:
            raise Exception("Grant ID already exists")
        if total_usd <= 0:
            raise Exception("total_usd must be positive")

        self.grants[grant_id] = json.dumps({
            "grant_id": grant_id,
            "grantee_address": grantee_address,
            "title": title,
            "total_usd": total_usd,
            "committed_usd": 0,     # sum of all milestone tranches added so far
            "released_usd": 0,      # sum of tranches for passed milestones
            "milestone_count": 0,
            "active": True,
        })

    # ----------------------------------------------------------------
    # Write: owner adds a milestone to a grant
    # ----------------------------------------------------------------

    @gl.public.write
    def add_milestone(
        self,
        milestone_id: str,
        grant_id: str,
        title: str,
        spec: str,
        tranche_usd: int,
        sequence: int,
    ) -> None:
        """
        Add a milestone to an existing grant.

        milestone_id : unique identifier, e.g. "grant-001-m1"
        grant_id     : must match an existing grant
        title        : short milestone name
        spec         : plain-language description of what must be delivered
        tranche_usd  : amount released in whole US dollars when this milestone passes
        sequence     : order of this milestone (1, 2, 3...)
        """
        if gl.message.sender_address != self.owner:
            raise Exception("Only the owner can add milestones")
        if grant_id not in self.grants:
            raise Exception("Grant not found")
        if milestone_id in self.milestones:
            raise Exception("Milestone ID already exists")
        if tranche_usd <= 0:
            raise Exception("tranche_usd must be positive")

        grant = json.loads(self.grants[grant_id])
        if not grant["active"]:
            raise Exception("Grant is no longer active")

        # Budget enforcement: total committed tranches may never exceed total_usd
        committed = int(grant.get("committed_usd", 0))
        if committed + tranche_usd > int(grant["total_usd"]):
            raise Exception("Milestone tranche would exceed the grant total budget")

        self.milestones[milestone_id] = json.dumps({
            "milestone_id": milestone_id,
            "grant_id": grant_id,
            "title": title,
            "spec": spec,
            "tranche_usd": tranche_usd,
            "sequence": sequence,
            "status": "pending",       # pending | passed | failed
            "released": False,
            "ai_reasoning": "",
        })

        grant["committed_usd"] = committed + tranche_usd
        grant["milestone_count"] = grant["milestone_count"] + 1
        self.grants[grant_id] = json.dumps(grant)

    # ----------------------------------------------------------------
    # Write: grantee submits evidence for a milestone
    # ----------------------------------------------------------------

    @gl.public.write
    def submit_milestone(
        self,
        submission_id: str,
        milestone_id: str,
        evidence_urls: str,
        notes: str,
    ) -> None:
        """
        Submit evidence for a milestone. The contract fetches each URL,
        evaluates the evidence against the milestone spec via AI consensus,
        and marks the milestone passed or failed. On passing, the tranche
        is marked as released.

        submission_id : unique identifier for this submission attempt
                        (must not already exist)
        milestone_id  : must match an existing milestone
        evidence_urls : comma-separated list of URLs (PR links, demo URLs, report links)
        notes         : optional plain-language context from the grantee
        """
        # Never overwrite a prior submission record
        if submission_id in self.submissions:
            raise Exception("Submission ID already exists")
        if milestone_id not in self.milestones:
            raise Exception("Milestone not found")

        milestone = json.loads(self.milestones[milestone_id])
        grant_id = milestone["grant_id"]

        if grant_id not in self.grants:
            raise Exception("Grant not found")

        grant = json.loads(self.grants[grant_id])

        if not grant["active"]:
            raise Exception("Grant is no longer active")
        if milestone["status"] == "passed":
            raise Exception("Milestone already passed")

        grantee_addr = Address(grant["grantee_address"])
        if gl.message.sender_address != grantee_addr:
            raise Exception("Only the grantee can submit milestone evidence")

        # Budget enforcement: a release may never push released_usd past total_usd
        if int(grant["released_usd"]) + int(milestone["tranche_usd"]) > int(grant["total_usd"]):
            raise Exception("Release would exceed the grant total budget")

        spec = milestone["spec"]
        title = milestone["title"]
        urls = [u.strip() for u in evidence_urls.split(",") if u.strip()]

        if len(urls) == 0:
            raise Exception("At least one evidence URL is required")

        # ---- non-deterministic: fetch evidence + AI evaluation ----

        def evaluate():
            fetched = []
            for url in urls[:5]:   # cap at 5 URLs to keep prompt size reasonable
                try:
                    content = gl.nondet.web.get(url)
                    fetched.append("URL: " + url + "\n" + str(content)[:1500])
                except Exception:
                    fetched.append("URL: " + url + "\n[could not fetch]")

            evidence_text = "\n\n---\n\n".join(fetched)

            prompt = (
                "You are an independent grant reviewer evaluating whether a grantee has completed a milestone.\n\n"
                "MILESTONE TITLE:\n" + title + "\n\n"
                "MILESTONE SPEC (what must be delivered):\n" + spec + "\n\n"
                "GRANTEE NOTES:\n" + (notes if notes else "none") + "\n\n"
                "EVIDENCE FETCHED FROM SUBMITTED URLS:\n" + evidence_text + "\n\n"
                "Evaluate strictly against the spec. Ask yourself:\n"
                "1. Does the evidence demonstrate the deliverable described in the spec?\n"
                "2. Is the work substantive and original, not placeholder or incomplete?\n"
                "3. Are there any clear gaps between what was promised and what was delivered?\n\n"
                "Respond with ONLY valid JSON and no extra text:\n"
                "{\"passed\": <true or false>, "
                "\"confidence\": <integer 0-100, how confident you are in this decision>, "
                "\"reasoning\": \"<two to three sentences explaining the decision>\"}"
            )

            raw = gl.nondet.exec_prompt(prompt)
            backticks = "```"
            raw = raw.replace(backticks + "json", "").replace(backticks, "").strip()
            parsed = json.loads(raw)

            passed = bool(parsed.get("passed", False))
            confidence = max(0, min(100, int(parsed.get("confidence") or 0)))
            reasoning = str(parsed.get("reasoning") or "")

            return json.dumps({
                "passed": passed,
                "confidence": confidence,
                "reasoning": reasoning,
            })

        def validate_evaluation(leader_result) -> bool:
            # Validators re-run independently and must agree on pass/fail.
            # Confidence may differ; only the binary decision must match.
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                leader = json.loads(leader_result.calldata)
                mine = json.loads(evaluate())
            except Exception:
                return False
            return mine["passed"] == leader["passed"]

        result_json = gl.vm.run_nondet_unsafe(evaluate, validate_evaluation)
        result = json.loads(result_json)

        passed = bool(result["passed"])
        confidence = int(result["confidence"])
        reasoning = str(result["reasoning"])

        # ---- deterministic: update state ----

        # record the submission (ID uniqueness was checked up front)
        self.submissions[submission_id] = json.dumps({
            "submission_id": submission_id,
            "milestone_id": milestone_id,
            "grant_id": grant_id,
            "evidence_urls": evidence_urls,
            "notes": notes,
            "passed": passed,
            "confidence": confidence,
            "reasoning": reasoning,
        })

        # update milestone status
        milestone["status"] = "passed" if passed else "failed"
        milestone["ai_reasoning"] = reasoning
        if passed:
            milestone["released"] = True
        self.milestones[milestone_id] = json.dumps(milestone)

        # if passed, record the tranche release on the grant
        if passed:
            grant["released_usd"] = int(grant["released_usd"]) + int(milestone["tranche_usd"])
            self.grants[grant_id] = json.dumps(grant)

    # ----------------------------------------------------------------
    # Write: owner closes a grant
    # ----------------------------------------------------------------

    @gl.public.write
    def close_grant(self, grant_id: str) -> None:
        """Mark a grant as inactive. No further milestones can be submitted."""
        if gl.message.sender_address != self.owner:
            raise Exception("Only the owner can close grants")
        if grant_id not in self.grants:
            raise Exception("Grant not found")
        grant = json.loads(self.grants[grant_id])
        grant["active"] = False
        self.grants[grant_id] = json.dumps(grant)

    # ----------------------------------------------------------------
    # View: read grant and milestone data
    # ----------------------------------------------------------------

    @gl.public.view
    def get_grant(self, grant_id: str) -> str:
        """Return the full grant record as JSON."""
        if grant_id not in self.grants:
            return json.dumps({"error": "Grant not found"})
        return self.grants[grant_id]

    @gl.public.view
    def get_milestone(self, milestone_id: str) -> str:
        """Return the full milestone record including AI reasoning as JSON."""
        if milestone_id not in self.milestones:
            return json.dumps({"error": "Milestone not found"})
        return self.milestones[milestone_id]

    @gl.public.view
    def get_submission(self, submission_id: str) -> str:
        """Return the full submission record as JSON."""
        if submission_id not in self.submissions:
            return json.dumps({"error": "Submission not found"})
        return self.submissions[submission_id]

    @gl.public.view
    def get_released_usd(self, grant_id: str) -> int:
        """Return total USD released so far for a grant."""
        if grant_id not in self.grants:
            return 0
        grant = json.loads(self.grants[grant_id])
        return int(grant["released_usd"])

    @gl.public.view
    def get_committed_usd(self, grant_id: str) -> int:
        """Return total USD committed across all milestones of a grant."""
        if grant_id not in self.grants:
            return 0
        grant = json.loads(self.grants[grant_id])
        return int(grant.get("committed_usd", 0))

    @gl.public.view
    def get_milestone_status(self, milestone_id: str) -> str:
        """Return pending, passed, or failed for a milestone."""
        if milestone_id not in self.milestones:
            return "not found"
        milestone = json.loads(self.milestones[milestone_id])
        return str(milestone["status"])

    @gl.public.view
    def get_owner(self) -> str:
        """Return the contract owner address."""
        return self.owner.as_hex
