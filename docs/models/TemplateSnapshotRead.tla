------------------------ MODULE TemplateSnapshotRead ------------------------
EXTENDS Naturals, FiniteSets
CONSTANT Fault
ASSUME Fault \in {"none", "cache", "rebind"}
\* #170 focused Safety obligation: two immutable versions, a mutable provider,
\* and sequential requests whose proof must not survive into the next request.
\* Check observes permission for this request; revocation after that observation
\* does not retroactively revoke bytes already read. Lost responses stutter.
\* Provider identity/permission truth, PostgreSQL/S3 and ZIP completeness are
\* implementation obligations, not proved by this model. No fairness/liveness,
\* constraints or symmetry. Deadlock off: an open reader may remain quiescent.
VARIABLES allowed, current, cache, requested, proof, replies
vars == <<allowed, current, cache, requested, proof, replies>>
Versions == {1, 2}
Init == /\ allowed = TRUE /\ current = 1 /\ cache = {}
        /\ requested = 0 /\ proof = "none" /\ replies = {}
Warm(v) == /\ cache' = cache \cup {v}
           /\ UNCHANGED <<allowed, current, requested, proof, replies>>
Change == /\ current' \in Versions /\ allowed' \in BOOLEAN
          /\ UNCHANGED <<cache, requested, proof, replies>>
Request(v) == /\ requested = 0 /\ requested' = v /\ proof' = "none"
              /\ UNCHANGED <<allowed, current, cache, replies>>
Check == /\ requested # 0 /\ proof = "none"
         /\ proof' = IF allowed THEN "allowed" ELSE "denied"
         /\ UNCHANGED <<allowed, current, cache, requested, replies>>
Serve == /\ requested \in cache /\ (proof = "allowed" \/ Fault = "cache")
         /\ replies' = replies \cup {[asked |-> requested,
                                     version |-> IF Fault = "rebind" THEN current ELSE requested,
                                     authorized |-> proof = "allowed"]}
         /\ requested' = 0 /\ proof' = "none"
         /\ UNCHANGED <<allowed, current, cache>>
Reject == /\ requested # 0 /\ proof = "denied"
          /\ requested' = 0 /\ proof' = "none"
          /\ UNCHANGED <<allowed, current, cache, replies>>
Next == (\E v \in Versions : Warm(v) \/ Request(v)) \/ Change \/ Check \/ Serve \/ Reject
Spec == Init /\ [][Next]_vars
LiveProof == \A r \in replies : r.authorized
ExactVersion == \A r \in replies : r.version = r.asked
=============================================================================
