----------------------- MODULE DeepExecutionReplay -----------------------
EXTENDS Naturals
CONSTANTS NoLock, RepeatAttempt, SkipSavedResult

(* Focused #152/#156 Safety obligation: two callers of one frozen execution.
   Claim/inspect, durable attempt, external model effect and saved result are
   separate cut points. Failure after the effect can be known or unconfirmed;
   neither authorizes automatic replay. Object recreation after a clean
   terminal preserves attempt/result; mid-run crash recovery is outside scope.
   "saved" is a runtime output package, not validated business PASS/NG.
   No fairness: a guest may never complete. New accepted executions, input
   identity validation, filesystem durability and cleanup need separate tests. *)
Calls == {"a", "b"}
VARIABLES phase, owner, attempted, saved, effects
vars == <<phase, owner, attempted, saved, effects>>

Init == /\ phase = [c \in Calls |-> "idle"] /\ owner = "none"
        /\ attempted = FALSE /\ saved = FALSE /\ effects = 0
Invoke(c) == /\ phase[c] = "idle"
             /\ phase' = [phase EXCEPT ![c] = "waiting"]
             /\ UNCHANGED <<owner, attempted, saved, effects>>
Claim(c) == /\ phase[c] = "waiting" /\ (owner = "none" \/ NoLock)
            /\ owner' = c /\ phase' = [phase EXCEPT ![c] = "inspecting"]
            /\ UNCHANGED <<attempted, saved, effects>>
ReadFresh(c) == /\ phase[c] = "inspecting" /\ ~saved
                /\ (~attempted \/ RepeatAttempt)
                /\ phase' = [phase EXCEPT ![c] = "fresh"]
                /\ UNCHANGED <<owner, attempted, saved, effects>>
ReadSaved(c) == /\ phase[c] = "inspecting" /\ saved /\ ~SkipSavedResult
                /\ phase' = [phase EXCEPT ![c] = "done"]
                /\ owner' = IF owner = c THEN "none" ELSE owner
                /\ UNCHANGED <<attempted, saved, effects>>
Reject(c) == /\ phase[c] = "inspecting" /\ attempted /\ ~RepeatAttempt
             /\ (~saved \/ SkipSavedResult)
             /\ phase' = [phase EXCEPT ![c] = "rejected"]
             /\ owner' = IF owner = c THEN "none" ELSE owner
             /\ UNCHANGED <<attempted, saved, effects>>
MarkAttempt(c) == /\ phase[c] = "fresh" /\ attempted' = TRUE
                  /\ phase' = [phase EXCEPT ![c] = "prepared"]
                  /\ UNCHANGED <<owner, saved, effects>>
StartModel(c) == /\ phase[c] = "prepared" /\ effects' = effects + 1
                 /\ phase' = [phase EXCEPT ![c] = "running"]
                 /\ UNCHANGED <<owner, attempted, saved>>
Complete(c) == /\ phase[c] = "running" /\ saved' = TRUE
               /\ phase' = [phase EXCEPT ![c] = "done"]
               /\ owner' = IF owner = c THEN "none" ELSE owner
               /\ UNCHANGED <<attempted, effects>>
Fail(c) == /\ phase[c] = "running"
           /\ phase' = [phase EXCEPT ![c] = "failed"]
           /\ owner' = IF owner = c THEN "none" ELSE owner
           /\ UNCHANGED <<attempted, saved, effects>>
Next == \E c \in Calls : Invoke(c) \/ Claim(c) \/ ReadFresh(c) \/ ReadSaved(c)
                         \/ Reject(c) \/ MarkAttempt(c) \/ StartModel(c)
                         \/ Complete(c) \/ Fail(c)
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in [Calls -> {"idle", "waiting", "inspecting", "fresh",
                                  "prepared", "running", "done", "failed", "rejected"}]
          /\ owner \in Calls \cup {"none"}
          /\ attempted \in BOOLEAN /\ saved \in BOOLEAN /\ effects \in 0..2
OneModelEffect == effects <= 1
SavedResultNotRejected == \A c \in Calls : phase[c] = "rejected" => ~saved
=============================================================================
