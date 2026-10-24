-------------------- MODULE StandardExecutionPolicy --------------------
EXTENDS Naturals
CONSTANTS SkipGuard, RewriteReplay
(* Focused #164 Safety obligation, not whole-system refinement.
   Two rounds, two process configurations. Accept is the existing atomic
   Round/input/job transaction; start captures one process-lifetime policy.
   Config changes replace a process, not an in-flight client's configuration.
   External output quality, cache lineage, crashes/fencing, DB durability and
   secrets are outside this model. No liveness/environment fairness claim. *)
Rounds == {1, 2}
Policies == {"a", "b"}
VARIABLES current, phase, accepted, frozen, executed
vars == <<current, phase, accepted, frozen, executed>>
Init == /\ current = "a"
        /\ phase = [r \in Rounds |-> "idle"]
        /\ accepted = [r \in Rounds |-> "none"]
        /\ frozen = accepted /\ executed = accepted
Change == /\ current' \in Policies \ {current}
          /\ UNCHANGED <<phase, accepted, frozen, executed>>
Accept(r) == /\ phase[r] = "idle"
             /\ phase' = [phase EXCEPT ![r] = "queued"]
             /\ accepted' = [accepted EXCEPT ![r] = current]
             /\ frozen' = [frozen EXCEPT ![r] = current]
             /\ UNCHANGED <<current, executed>>
Replay(r) == /\ phase[r] # "idle"
             /\ frozen' = IF RewriteReplay THEN [frozen EXCEPT ![r] = current] ELSE frozen
             /\ UNCHANGED <<current, phase, accepted, executed>>
Start(r) == /\ phase[r] = "queued"
            /\ frozen[r] = current \/ SkipGuard
            /\ phase' = [phase EXCEPT ![r] = "running"]
            /\ executed' = [executed EXCEPT ![r] = current]
            /\ UNCHANGED <<current, accepted, frozen>>
Reject(r) == /\ phase[r] = "queued" /\ frozen[r] # current /\ ~SkipGuard
             /\ phase' = [phase EXCEPT ![r] = "failed"]
             /\ UNCHANGED <<current, accepted, frozen, executed>>
Complete(r) == /\ phase[r] = "running"
               /\ phase' = [phase EXCEPT ![r] = "completed"]
               /\ UNCHANGED <<current, accepted, frozen, executed>>
Next == Change \/ (\E r \in Rounds : Accept(r) \/ Replay(r) \/ Start(r) \/ Reject(r) \/ Complete(r))
Spec == Init /\ [][Next]_vars
FrozenHistory == frozen = accepted
ExecutedAcceptedPolicy == \A r \in Rounds : executed[r] # "none" => executed[r] = accepted[r]
=============================================================================
