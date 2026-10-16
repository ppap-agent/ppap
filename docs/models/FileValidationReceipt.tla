------------------------ MODULE FileValidationReceipt ------------------------
EXTENDS Naturals
\* Focused #165 obligation: one precheck request, two source versions and two
\* workers. Submit linearizes at the existing job INSERT/conflict check; result
\* publication is a separate fenced UPDATE. Lease loss is not proof of failure.
\* Parsing, file/object authorization, persisted bytes and transport delivery
\* are outside this model and require API/DB/Worker tests. No audit or fee effect.
\* No fairness or liveness claim: a lost worker may leave a request unknown.
CONSTANT Fault
Sources == {1, 2}
Workers == {1, 2}
VARIABLES phase, source, original, owner, attempt, resultSource, foreignCommit
vars == <<phase, source, original, owner, attempt, resultSource, foreignCommit>>
Init == /\ phase = "absent" /\ source = 0 /\ original = 0
        /\ owner = 0 /\ attempt = 0 /\ resultSource = 0
        /\ foreignCommit = FALSE
Submit(s) == /\ phase = "absent"
             /\ phase' = "queued" /\ source' = s /\ original' = s
             /\ UNCHANGED <<owner, attempt, resultSource, foreignCommit>>
Replay(s) == /\ phase # "absent"
             /\ source' = IF Fault = "replace" THEN s ELSE source
             /\ UNCHANGED <<phase, original, owner, attempt, resultSource, foreignCommit>>
Claim(w) == /\ phase = "queued"
            /\ phase' = "leased" /\ owner' = w /\ attempt' = attempt + 1
            /\ UNCHANGED <<source, original, resultSource, foreignCommit>>
LoseLease == /\ phase = "leased" /\ phase' = "unknown"
             /\ UNCHANGED <<source, original, owner, attempt, resultSource, foreignCommit>>
Reclaim(w) == /\ Fault = "reclaim" /\ phase = "unknown" /\ attempt < 2
              /\ phase' = "leased" /\ owner' = w /\ attempt' = attempt + 1
              /\ UNCHANGED <<source, original, resultSource, foreignCommit>>
Complete(w) == /\ phase \in {"leased", "unknown"}
               /\ (w = owner \/ Fault = "owner")
               /\ phase' = "completed" /\ resultSource' = source
               /\ foreignCommit' = (w # owner)
               /\ UNCHANGED <<source, original, owner, attempt>>
Next == (\E s \in Sources: Submit(s) \/ Replay(s))
        \/ (\E w \in Workers: Claim(w) \/ Reclaim(w) \/ Complete(w))
        \/ LoseLease
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in {"absent", "queued", "leased", "unknown", "completed"}
          /\ source \in Sources \cup {0} /\ original \in Sources \cup {0}
          /\ owner \in Workers \cup {0} /\ attempt \in 0..2
          /\ resultSource \in Sources \cup {0} /\ foreignCommit \in BOOLEAN
Binding == phase # "absent" => source = original
StableResult == phase = "completed" => resultSource = original
OneAttempt == attempt <= 1
ValidCommit == ~foreignCommit
=============================================================================
