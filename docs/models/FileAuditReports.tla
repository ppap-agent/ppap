--------------------------- MODULE FileAuditReports ---------------------------
EXTENDS Naturals, FiniteSets

\* Focused obligation for #169 / approved D1,D3, not a full-system refinement.
\* Three frozen report inputs distinguish two eligible files and one unknown.
\* Audit completion and initial enqueue share one DB transaction (Init).
\* Lease expiry/reclaim is abstracted by another Claim; object I/O may never
\* respond. No fairness, real-time bound, eventual completion or cleanup claim.
\* Failed jobs may retain private attempt objects. Explicit recovery after
\* task completion preserves that completion and the same frozen input.
\* Request receipts, sharing, billing, PDF layout and eligibility calculation
\* remain outside this focused model; controlled API tests cover receipts.
CONSTANTS FaultyLateCommit, FaultyBinding, FaultyEligibility, FaultyEarlyFinish,
          FaultyReopenTask
Reports == {"a", "b", "unknown"}
Eligible == {"a", "b"}
Attempts == 1..2
VARIABLES phase, epoch, captured, objects, published, taskDone, recovered
vars == <<phase, epoch, captured, objects, published, taskDone, recovered>>

Init == /\ phase = [r \in Reports |-> IF r \in Eligible \/ FaultyEligibility THEN "queued" ELSE "absent"]
        /\ epoch = [r \in Reports |-> 0]
        /\ captured = [r \in Reports |-> [a \in Attempts |-> "none"]]
        /\ objects = {}
        /\ published = [r \in Reports |-> 0]
        /\ taskDone = FALSE
        /\ recovered = {}

Claim(r) == /\ phase[r] \in {"queued", "leased"} /\ epoch[r] < 2
            /\ LET a == epoch[r] + 1 IN
                 /\ epoch' = [epoch EXCEPT ![r] = a]
                 /\ captured' = [captured EXCEPT ![r][a] = IF FaultyBinding THEN (IF r = "a" THEN "b" ELSE "a") ELSE r]
            /\ phase' = [phase EXCEPT ![r] = "leased"]
            /\ UNCHANGED <<objects, published, taskDone, recovered>>

Write(r,a) == /\ a <= epoch[r] /\ captured[r][a] # "none"
              /\ objects' = objects \cup {<<r,a,captured[r][a]>>}
              /\ UNCHANGED <<phase, epoch, captured, published, taskDone, recovered>>

Commit(r,a) == /\ phase[r] = "leased"
               /\ (a = epoch[r] \/ FaultyLateCommit)
               /\ <<r,a,captured[r][a]>> \in objects
               /\ published' = [published EXCEPT ![r] = a]
               /\ phase' = [phase EXCEPT ![r] = "ready"]
               /\ UNCHANGED <<epoch, captured, objects, taskDone, recovered>>

Fail(r) == /\ phase[r] = "leased"
           /\ phase' = [phase EXCEPT ![r] = "failed"]
           /\ UNCHANGED <<epoch, captured, objects, published, taskDone, recovered>>

Recover(r) == /\ taskDone /\ r \in Eligible
              /\ phase[r] = "failed" /\ epoch[r] < 2
              /\ phase' = [phase EXCEPT ![r] = "queued"]
              /\ recovered' = recovered \cup {r}
              /\ taskDone' = IF FaultyReopenTask THEN FALSE ELSE taskDone
              /\ UNCHANGED <<epoch, captured, objects, published>>

Terminal == \A r \in Reports : phase[r] \in {"absent", "ready", "failed"}
Finish == /\ ~taskDone /\ (Terminal \/ FaultyEarlyFinish)
          /\ taskDone' = TRUE
          /\ UNCHANGED <<phase, epoch, captured, objects, published, recovered>>
Next == Finish \/ (\E r \in Reports : Claim(r) \/ Fail(r) \/ Recover(r) \/ (\E a \in Attempts : Write(r,a) \/ Commit(r,a)))
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in [Reports -> {"absent", "queued", "leased", "ready", "failed"}]
          /\ epoch \in [Reports -> 0..2]
          /\ captured \in [Reports -> [Attempts -> Reports \cup {"none"}]]
          /\ objects \subseteq Reports \X Attempts \X Reports
          /\ published \in [Reports -> 0..2] /\ taskDone \in BOOLEAN
          /\ recovered \subseteq Eligible
EligibleOnly == \A r \in Reports : r \notin Eligible => phase[r] = "absent"
FrozenBinding == \A r \in Reports : published[r] > 0 => captured[r][published[r]] = r
CurrentAttemptWins == \A r \in Reports : published[r] > 0 => published[r] = epoch[r]
ReadyRequiresObject == \A r \in Reports : phase[r] = "ready" => <<r,published[r],r>> \in objects
CompletionWaits == taskDone => \A r \in Reports : phase[r] \in {"absent", "ready", "failed"} \/ r \in recovered
RecoveryKeepsCompletion == recovered # {} => taskDone
=============================================================================
