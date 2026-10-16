--------------------------- MODULE FileAuditReports ---------------------------
EXTENDS Naturals, FiniteSets

\* Focused obligation for #169 / approved D1,D3, not a full-system refinement.
\* Three frozen report inputs distinguish two eligible files and one unknown.
\* Audit completion and initial enqueue share one DB transaction (Init).
\* Lease expiry/reclaim is abstracted by another Claim; object I/O may never
\* respond. No fairness, real-time bound, eventual completion or cleanup claim.
\* Failed jobs may retain private attempt objects. D4 explicit retry, sharing,
\* billing, PDF layout and the eligibility algorithm are outside this model.
CONSTANTS FaultyLateCommit, FaultyBinding, FaultyEligibility, FaultyEarlyFinish
Reports == {"a", "b", "unknown"}
Eligible == {"a", "b"}
Attempts == 1..2
VARIABLES phase, epoch, captured, objects, published, taskDone
vars == <<phase, epoch, captured, objects, published, taskDone>>

Init == /\ phase = [r \in Reports |-> IF r \in Eligible \/ FaultyEligibility THEN "queued" ELSE "absent"]
        /\ epoch = [r \in Reports |-> 0]
        /\ captured = [r \in Reports |-> [a \in Attempts |-> "none"]]
        /\ objects = {}
        /\ published = [r \in Reports |-> 0]
        /\ taskDone = FALSE

Claim(r) == /\ phase[r] \in {"queued", "leased"} /\ epoch[r] < 2
            /\ LET a == epoch[r] + 1 IN
                 /\ epoch' = [epoch EXCEPT ![r] = a]
                 /\ captured' = [captured EXCEPT ![r][a] = IF FaultyBinding THEN (IF r = "a" THEN "b" ELSE "a") ELSE r]
            /\ phase' = [phase EXCEPT ![r] = "leased"]
            /\ UNCHANGED <<objects, published, taskDone>>

Write(r,a) == /\ a <= epoch[r] /\ captured[r][a] # "none"
              /\ objects' = objects \cup {<<r,a,captured[r][a]>>}
              /\ UNCHANGED <<phase, epoch, captured, published, taskDone>>

Commit(r,a) == /\ phase[r] = "leased"
               /\ (a = epoch[r] \/ FaultyLateCommit)
               /\ <<r,a,captured[r][a]>> \in objects
               /\ published' = [published EXCEPT ![r] = a]
               /\ phase' = [phase EXCEPT ![r] = "ready"]
               /\ UNCHANGED <<epoch, captured, objects, taskDone>>

Fail(r) == /\ phase[r] = "leased"
           /\ phase' = [phase EXCEPT ![r] = "failed"]
           /\ UNCHANGED <<epoch, captured, objects, published, taskDone>>

Terminal == \A r \in Reports : phase[r] \in {"absent", "ready", "failed"}
Finish == /\ ~taskDone /\ (Terminal \/ FaultyEarlyFinish)
          /\ taskDone' = TRUE
          /\ UNCHANGED <<phase, epoch, captured, objects, published>>
Next == Finish \/ (\E r \in Reports : Claim(r) \/ Fail(r) \/ (\E a \in Attempts : Write(r,a) \/ Commit(r,a)))
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in [Reports -> {"absent", "queued", "leased", "ready", "failed"}]
          /\ epoch \in [Reports -> 0..2]
          /\ captured \in [Reports -> [Attempts -> Reports \cup {"none"}]]
          /\ objects \subseteq Reports \X Attempts \X Reports
          /\ published \in [Reports -> 0..2] /\ taskDone \in BOOLEAN
EligibleOnly == \A r \in Reports : r \notin Eligible => phase[r] = "absent"
FrozenBinding == \A r \in Reports : published[r] > 0 => captured[r][published[r]] = r
CurrentAttemptWins == \A r \in Reports : published[r] > 0 => published[r] = epoch[r]
ReadyRequiresObject == \A r \in Reports : phase[r] = "ready" => <<r,published[r],r>> \in objects
CompletionWaits == taskDone => Terminal
=============================================================================
