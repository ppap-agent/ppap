-------------------------- MODULE McpDelegation --------------------------
EXTENDS Naturals, FiniteSets
CONSTANTS AllowReplay, IgnoreBinding, IgnoreRevocation
VARIABLES active, generation, retired, approved, phase, effects, startedWithAuthority
vars == <<active, generation, retired, approved, phase, effects, startedWithAuthority>>

\* Focused safety obligation for #206. One grant, two refresh generations,
\* one durable operation identity, and two different payloads. PostgreSQL owns
\* refresh rotation/revocation and the operation claim. HTTP can lose any reply.
\* A revoked grant cannot start another effect; already-started effects may finish.
\* Crypto, identity lookup, business ACLs and external delivery are separate tests.
\* No fairness/liveness claim: human approval and external responses may never arrive.
Init == /\ active = TRUE /\ generation = 0 /\ retired = {}
        /\ approved = 2 /\ phase = "pending" /\ effects = 0
        /\ startedWithAuthority = TRUE
Rotate == /\ active /\ generation = 0
          /\ generation' = 1 /\ retired' = {0}
          /\ UNCHANGED <<active, approved, phase, effects, startedWithAuthority>>
Reuse == /\ 0 \in retired /\ active /\ active' = FALSE
         /\ UNCHANGED <<generation, retired, approved, phase, effects, startedWithAuthority>>
Revoke == /\ active /\ active' = FALSE
          /\ UNCHANGED <<generation, retired, approved, phase, effects, startedWithAuthority>>
Approve == /\ active /\ phase = "pending" /\ approved = 2
           /\ approved' = 0
           /\ UNCHANGED <<active, generation, retired, phase, effects, startedWithAuthority>>
Start(payload) == /\ (active \/ IgnoreRevocation)
                  /\ (phase = "pending" \/ (AllowReplay /\ phase = "unknown"))
                  /\ approved # 2 /\ (approved = payload \/ IgnoreBinding)
                  /\ phase' = "running" /\ effects' = effects + 1
                  /\ startedWithAuthority' = (startedWithAuthority /\ active /\ approved = payload)
                  /\ UNCHANGED <<active, generation, retired, approved>>
Finish == /\ phase = "running" /\ phase' \in {"done", "unknown"}
          /\ UNCHANGED <<active, generation, retired, approved, effects, startedWithAuthority>>
Next == Rotate \/ Reuse \/ Revoke \/ Approve \/ (\E p \in {0,1}: Start(p)) \/ Finish
Spec == Init /\ [][Next]_vars
SingleEffect == effects <= 1
AuthorizedEffect == startedWithAuthority
=============================================================================
