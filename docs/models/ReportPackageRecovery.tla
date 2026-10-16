----------------------- MODULE ReportPackageRecovery -----------------------
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS FaultStale, FaultRebind, FaultLate
Requests == {"r1", "r2"}
MaxAttempt == 3
VARIABLES state, attempt, observed, receipts, sources, held, published, recoveries
vars == <<state, attempt, observed, receipts, sources, held, published, recoveries>>

\* Focused #169/D3 Safety obligation, not a system refinement or Liveness proof.
\* Start with a failed shared package, two aliases/callers, three generations.
\* Each caller observes a failed generation before its recovery transaction.
\* Receipt insertion and requeue are atomic; lost replies can only replay the
\* same receipt. Claim and external completion are separate decision cut points.
\* "ab"/"ac" distinguish frozen PDF artifact sets, not filenames or ordering.
\* Current authorization, billing policy, actual bytes/object writes, expiry,
\* private residual artifacts and eventual I/O completion are tested separately.
\* No fairness, constraints or symmetry; quiescence/nonresponse is permitted.
Init == /\ state = "failed" /\ attempt = 1
        /\ observed = [r \in Requests |-> 0]
        /\ receipts = [r \in Requests |-> 0]
        /\ sources = "ab" /\ held = {1} /\ published = 0
        /\ recoveries = <<>>
Observe(r) == /\ observed[r] = 0 /\ state = "failed"
              /\ observed' = [observed EXCEPT ![r] = attempt]
              /\ UNCHANGED <<state, attempt, receipts, sources, held, published, recoveries>>
Recover(r) == /\ observed[r] > 0 /\ receipts[r] = 0
              /\ state = "failed" /\ attempt < MaxAttempt
              /\ observed[r] = attempt \/ FaultStale
              /\ state' = "queued"
              /\ receipts' = [receipts EXCEPT ![r] = attempt]
              /\ recoveries' = Append(recoveries, [expected |-> observed[r], actual |-> attempt])
              /\ sources' = IF FaultRebind THEN "ac" ELSE sources
              /\ UNCHANGED <<attempt, observed, held, published>>
Claim == /\ state = "queued" /\ attempt < MaxAttempt
         /\ state' = "leased" /\ attempt' = attempt + 1
         /\ held' = held \cup {attempt + 1}
         /\ UNCHANGED <<observed, receipts, sources, published, recoveries>>
Fail == /\ state = "leased" /\ state' = "failed"
        /\ UNCHANGED <<attempt, observed, receipts, sources, held, published, recoveries>>
Publish(a) == /\ a \in held
              /\ (a = attempt /\ state = "leased") \/ (FaultLate /\ a < attempt)
              /\ state' = "ready" /\ published' = a
              /\ UNCHANGED <<attempt, observed, receipts, sources, held, recoveries>>
Replay(r) == /\ receipts[r] > 0 /\ UNCHANGED vars
Next == (\E r \in Requests : Observe(r) \/ Recover(r) \/ Replay(r))
        \/ Claim \/ Fail \/ (\E a \in 1..MaxAttempt : Publish(a))
Spec == Init /\ [][Next]_vars
TypeOK == /\ state \in {"failed", "queued", "leased", "ready"}
          /\ attempt \in 1..MaxAttempt /\ held \subseteq 1..MaxAttempt
          /\ observed \in [Requests -> 0..MaxAttempt]
          /\ receipts \in [Requests -> 0..MaxAttempt]
          /\ sources \in {"ab", "ac"} /\ published \in 0..MaxAttempt
          /\ Len(recoveries) <= Cardinality(Requests)
FreshRecovery == \A i \in 1..Len(recoveries) : recoveries[i].expected = recoveries[i].actual
FrozenSources == sources = "ab"
CurrentAttemptWins == published = 0 \/ published = attempt
=============================================================================
