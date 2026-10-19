--------------------- MODULE TemplatePermissionChange ---------------------
EXTENDS Naturals, FiniteSets

CONSTANTS FaultReplay, FaultReadback, FaultGuard
Requests == {"first", "second"}
Phases == {"idle", "preparing", "committed", "waiting", "unknown", "applied", "rejected"}
VARIABLES phase, guards, calls, acknowledged, effects, aclMatches, reserved, unsafeCommit
vars == <<phase, guards, calls, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

\* Focused safety obligation, not a refinement proof or a provider liveness
\* claim. Two fixed requests compete for one configured folder. The database
\* guard and durable dispatch fence are separate from the external call and
\* receipt. Provider authorization, tenant binding and CAS against human edits
\* are outside this abstraction and require adapter tests / external evidence.
\* Unknown retains responsibility and prevents another product mutation.
Init == /\ phase = [r \in Requests |-> "idle"]
        /\ guards = {}
        /\ calls = [r \in Requests |-> 0]
        /\ acknowledged = {}
        /\ effects = {}
        /\ aclMatches = FALSE
        /\ reserved = {}
        /\ unsafeCommit = FALSE

Begin(r) == /\ phase[r] = "idle"
            /\ \A other \in Requests : phase[other] \notin {"preparing", "committed", "waiting", "unknown"}
            /\ phase' = [phase EXCEPT ![r] = "preparing"]
            /\ guards' = guards \cup {r}
            /\ UNCHANGED <<calls, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

Commit(r) == /\ phase[r] = "preparing" /\ (r \in guards \/ FaultGuard)
             /\ phase' = [phase EXCEPT ![r] = "committed"]
             /\ reserved' = reserved \cup {r}
             /\ unsafeCommit' = (unsafeCommit \/ r \notin guards)
             /\ UNCHANGED <<guards, calls, acknowledged, effects, aclMatches>>

\* A committed request can reach Feishu after the connection is lost and a
\* reader has recovered it as unknown. The external call is not cancellable
\* by losing the database lock. Its consumed reservation still blocks replay.
Call(r) == /\ phase[r] \in {"committed", "unknown"} /\ r \in reserved /\ calls[r] = 0
           /\ phase' = [phase EXCEPT ![r] = IF @ = "unknown" THEN "unknown" ELSE "waiting"]
           /\ calls' = [calls EXCEPT ![r] = @ + 1]
           /\ UNCHANGED <<guards, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

ProviderEffect(r) == /\ phase[r] \in {"waiting", "unknown"} /\ calls[r] > 0
                    /\ r \notin effects
                    /\ effects' = effects \cup {r}
                    /\ aclMatches' = TRUE
                    /\ UNCHANGED <<phase, guards, calls, acknowledged, reserved, unsafeCommit>>

Receipt(r) == /\ phase[r] = "waiting" /\ r \in effects /\ r \in guards
              /\ phase' = [phase EXCEPT ![r] = "applied"]
              /\ acknowledged' = acknowledged \cup {r}
              /\ guards' = guards \ {r}
              /\ UNCHANGED <<calls, effects, aclMatches, reserved, unsafeCommit>>

Reject(r) == /\ phase[r] \in {"preparing", "waiting"} /\ r \in guards
             /\ r \notin effects
             /\ phase' = [phase EXCEPT ![r] = "rejected"]
             /\ guards' = guards \ {r}
             /\ UNCHANGED <<calls, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

LoseGuard(r) == /\ r \in guards
                /\ guards' = guards \ {r}
                /\ UNCHANGED <<phase, calls, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

Recover(r) == /\ r \notin guards /\ phase[r] \in {"preparing", "committed", "waiting"}
              /\ phase' = [phase EXCEPT ![r] = IF phase[r] = "preparing" THEN "rejected" ELSE "unknown"]
              /\ UNCHANGED <<guards, calls, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

Timeout(r) == /\ phase[r] = "waiting" /\ r \in guards
              /\ phase' = [phase EXCEPT ![r] = "unknown"]
              /\ guards' = guards \ {r}
              /\ UNCHANGED <<calls, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

\* Another application or person can make the ACL match. This is not a
\* receipt for either fixed request, even when the intended final role matches.
ObserveACL == /\ ~aclMatches /\ aclMatches' = TRUE
              /\ UNCHANGED <<phase, guards, calls, acknowledged, effects, reserved, unsafeCommit>>

Replay(r) == /\ FaultReplay /\ phase[r] = "unknown" /\ calls[r] = 1
             /\ calls' = [calls EXCEPT ![r] = 2]
             /\ UNCHANGED <<phase, guards, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

ReadbackSuccess(r) == /\ FaultReadback /\ phase[r] = "unknown" /\ aclMatches
                      /\ phase' = [phase EXCEPT ![r] = "applied"]
                      /\ UNCHANGED <<guards, calls, acknowledged, effects, aclMatches, reserved, unsafeCommit>>

Next == \/ ObserveACL
        \/ \E r \in Requests : Begin(r) \/ Commit(r) \/ Call(r) \/ ProviderEffect(r)
              \/ Receipt(r) \/ Reject(r) \/ LoseGuard(r) \/ Recover(r)
              \/ Timeout(r) \/ Replay(r) \/ ReadbackSuccess(r)

TypeOK == /\ phase \in [Requests -> Phases] /\ guards \subseteq Requests
          /\ calls \in [Requests -> 0..2] /\ acknowledged \subseteq Requests
          /\ effects \subseteq Requests /\ aclMatches \in BOOLEAN
          /\ reserved \subseteq Requests /\ unsafeCommit \in BOOLEAN
NoReplay == \A r \in Requests : calls[r] <= 1
TruthfulReceipt == \A r \in Requests : phase[r] = "applied" => r \in acknowledged
\* Authority is consumed at the durable dispatch fence, not an impossible
\* cross-system atomic transaction between PostgreSQL and Feishu.
GuardedDispatch == ~unsafeCommit
Spec == Init /\ [][Next]_vars
=============================================================================
