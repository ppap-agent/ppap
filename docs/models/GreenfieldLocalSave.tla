------------------------- MODULE GreenfieldLocalSave -------------------------
EXTENDS Naturals
\* Focused obligation: one draft, a local writer and one competing change.
\* Storage reads/writes are synchronous individual operations, not transactions.
\* A read failure or invalid record must not authorize a fallback autosave.
\* No fairness: storage may stay unavailable. Restore/clear and multi-key crash
\* atomicity are not modeled; controlled implementation checks cover that seam.
CONSTANT UnsafeFallback
VARIABLES disk, observed, input, state, changedElsewhere, badWrite, acknowledged
vars == <<disk, observed, input, state, changedElsewhere, badWrite, acknowledged>>
Init == /\ disk \in {"absent", "old", "broken"}
        /\ observed = "unread" /\ input = "empty" /\ state = "initial"
        /\ changedElsewhere = FALSE /\ badWrite = FALSE /\ acknowledged = FALSE
Read == /\ state = "initial"
        /\ \E outcome \in {"normal", "failed"} :
             /\ observed' = IF outcome = "failed" THEN "unknown" ELSE disk
             /\ state' = IF outcome = "failed" \/ disk = "broken" THEN "blocked" ELSE "ready"
             /\ input' = IF outcome = "normal" /\ disk = "old" THEN "old" ELSE "empty"
        /\ UNCHANGED <<disk, changedElsewhere, badWrite, acknowledged>>
Edit == /\ state # "initial" /\ input # "edited"
        /\ input' = "edited" /\ acknowledged' = FALSE
        /\ UNCHANGED <<disk, observed, state, changedElsewhere, badWrite>>
ExternalChange == /\ ~changedElsewhere /\ state # "initial"
                  /\ disk' = "external" /\ changedElsewhere' = TRUE
                  /\ UNCHANGED <<observed, input, state, badWrite, acknowledged>>
MayWrite == state # "initial" /\ (UnsafeFallback \/ (state # "blocked" /\ observed = disk))
Save == /\ MayWrite
        /\ \E outcome \in {"normal", "failed"} :
             /\ disk' = IF outcome = "normal" THEN input ELSE disk
             /\ observed' = IF outcome = "normal" THEN input ELSE observed
             /\ state' = IF outcome = "normal" THEN "saved" ELSE "writeFailed"
             /\ acknowledged' = (outcome = "normal")
             /\ badWrite' = (badWrite \/ (outcome = "normal" /\ (observed \in {"unknown", "broken"} \/ observed # disk)))
        /\ UNCHANGED <<input, changedElsewhere>>
Next == Read \/ Edit \/ ExternalChange \/ Save
Spec == Init /\ [][Next]_vars
TypeOK == /\ disk \in {"absent", "old", "broken", "empty", "edited", "external"}
          /\ observed \in {"unread", "unknown", "absent", "old", "broken", "empty", "edited", "external"}
          /\ input \in {"empty", "old", "edited"}
          /\ state \in {"initial", "blocked", "ready", "saved", "writeFailed"}
          /\ changedElsewhere \in BOOLEAN /\ badWrite \in BOOLEAN /\ acknowledged \in BOOLEAN
PreserveUnreadOrChanged == ~badWrite
FailureIsNotSaved == state = "writeFailed" => ~acknowledged
=============================================================================
