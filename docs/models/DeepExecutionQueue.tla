----------------------- MODULE DeepExecutionQueue -----------------------
EXTENDS Naturals

(* Focused #152/#156 obligation: a pending caller can return timeout/cancel
   without the running guest completing. Not an end-to-end time or cleanup
   proof. One holder and one waiter preserve the observed contention. The
   clock may reach the deadline and a guest may never release its slot.
   Poll fairness assumes the waiting thread receives CPU time; the native
   controlled test separately checks this scheduling boundary. *)
CONSTANT WaitRequiresRelease
VARIABLES held, state, expired, cancelled
vars == <<held, state, expired, cancelled>>

Init == /\ held = TRUE
        /\ state = "idle"
        /\ expired = FALSE
        /\ cancelled = FALSE

Begin == /\ state = "idle"
         /\ state' = "waiting"
         /\ UNCHANGED <<held, expired, cancelled>>
Clock == /\ state = "waiting" /\ ~expired
         /\ expired' = TRUE
         /\ UNCHANGED <<held, state, cancelled>>
Cancel == /\ state = "waiting" /\ ~cancelled
          /\ cancelled' = TRUE
          /\ UNCHANGED <<held, state, expired>>
Release == /\ held /\ held' = FALSE
           /\ UNCHANGED <<state, expired, cancelled>>
Start == /\ state = "waiting" /\ ~held /\ ~expired /\ ~cancelled
         /\ state' = "executing"
         /\ UNCHANGED <<held, expired, cancelled>>
Poll == /\ state = "waiting" /\ (expired \/ cancelled)
        /\ (~WaitRequiresRelease \/ ~held)
        /\ state' = IF cancelled THEN "cancelled" ELSE "timeout"
        /\ UNCHANGED <<held, expired, cancelled>>
Next == Begin \/ Clock \/ Cancel \/ Release \/ Start \/ Poll
Spec == Init /\ [][Next]_vars /\ WF_vars(Clock) /\ WF_vars(Poll)

TypeOK == /\ held \in BOOLEAN /\ expired \in BOOLEAN /\ cancelled \in BOOLEAN
          /\ state \in {"idle", "waiting", "executing", "timeout", "cancelled"}
InvalidationIndependent == (state = "waiting" /\ (expired \/ cancelled))
                           => ENABLED Poll
WaitingTerminates == (state = "waiting" /\ (expired \/ cancelled))
                     ~> (state \in {"timeout", "cancelled"})
=============================================================================
