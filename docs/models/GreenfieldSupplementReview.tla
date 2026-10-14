--------------------- MODULE GreenfieldSupplementReview ---------------------
EXTENDS Integers, FiniteSets

\* Focused obligation, not a refinement proof: two independent requests,
\* at most two receipts. Receiving a partial reply is not a human judgment.
\* Verification represents an explicit user's file/version/association check;
\* A check can finish before its result enters human review. Both boundaries
\* block a decision. Applying a result invalidates changed judgments only.
\* Semantic comparison, attempt fencing, storage, authorization, external
\* sending and liveness are omitted; this is not an implementation proof.
CONSTANTS AutoResolve, KeepOldReview, IgnoreDraft, IgnoreResult
Requests == {"dimensions", "calibration"}
VARIABLES epoch, draft, targets, received, satisfied, reviewedAt, approved, check
vars == <<epoch, draft, targets, received, satisfied, reviewedAt, approved, check>>
Init == /\ epoch = 0 /\ draft = "none" /\ targets = {}
        /\ received = {} /\ satisfied = {}
        /\ reviewedAt = [r \in Requests |-> -1]
        /\ approved = FALSE /\ check = "idle"

Upload == /\ ~approved /\ draft = "none" /\ epoch < 2 /\ check = "idle"
          /\ draft' = "uploaded"
          /\ UNCHANGED <<epoch, targets, received, satisfied, reviewedAt, approved, check>>
Verify(rs) == /\ draft = "uploaded" /\ rs \in SUBSET Requests /\ rs # {}
              /\ draft' = "verified" /\ targets' = rs
              /\ UNCHANGED <<epoch, received, satisfied, reviewedAt, approved, check>>
Discard == /\ draft # "none" /\ draft' = "none" /\ targets' = {}
           /\ UNCHANGED <<epoch, received, satisfied, reviewedAt, approved, check>>
Receive == /\ draft = "verified" /\ ~approved /\ epoch < 2
           /\ epoch' = epoch + 1 /\ draft' = "none" /\ targets' = {}
           /\ received' = received \cup targets
           /\ satisfied' = IF AutoResolve THEN targets
                           ELSE IF KeepOldReview THEN satisfied ELSE {}
           /\ UNCHANGED <<reviewedAt, approved, check>>
Judge(r) == /\ ~approved /\ draft = "none" /\ r \in Requests /\ check = "idle"
            /\ satisfied' = satisfied \cup {r}
            /\ reviewedAt' = [reviewedAt EXCEPT ![r] = epoch]
            /\ UNCHANGED <<epoch, draft, targets, received, approved, check>>
StartCheck == /\ ~approved /\ draft = "none" /\ check = "idle"
              /\ check' = "running"
              /\ UNCHANGED <<epoch, draft, targets, received, satisfied, reviewedAt, approved>>
FinishCheck == /\ check = "running" /\ check' = "ready"
               /\ UNCHANGED <<epoch, draft, targets, received, satisfied, reviewedAt, approved>>
UseResult(changed) == /\ ~approved /\ check = "ready" /\ changed \in SUBSET Requests
                     /\ check' = "idle" /\ satisfied' = satisfied \ changed
                     /\ UNCHANGED <<epoch, draft, targets, received, reviewedAt, approved>>
Approve == /\ ~approved /\ satisfied = Requests
           /\ (check = "idle" \/ (IgnoreResult /\ check = "ready"))
           /\ (IgnoreDraft \/ draft = "none") /\ approved' = TRUE
           /\ UNCHANGED <<epoch, draft, targets, received, satisfied, reviewedAt, check>>
Next == Upload \/ Discard \/ Receive \/ Approve \/ StartCheck \/ FinishCheck
        \/ (\E changed \in SUBSET Requests: UseResult(changed))
        \/ (\E rs \in SUBSET Requests: Verify(rs))
        \/ (\E r \in Requests: Judge(r))
Spec == Init /\ [][Next]_vars
CurrentHumanJudgment == \A r \in satisfied: reviewedAt[r] = epoch
NoDecisionWithDraft == approved => draft = "none"
NoDecisionWithPendingCheck == approved => check = "idle"
=============================================================================
