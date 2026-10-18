---------------------- MODULE RuleExtractionRecovery ----------------------
EXTENDS Naturals, FiniteSets

\* Focused #171 recovery obligation, not full-system refinement. One frozen
\* source, two parse identities, and delayed GET/POST replies. The first
\* request has an unknown outcome. Refresh and reload only observe that ID;
\* only explicit confirmation can issue the second request. Replacing the
\* active request does not cancel the first server job or refund its cost.
\* Manual entry and a ready candidate reject later parse replies.
\*
\* A response capture and its delivery are separate because the user can
\* confirm another request or enter clauses between them. Queued/leased are
\* one observed running state; unknown is distinct. No fairness or liveness
\* claim: calls and server jobs may remain unresolved forever. No SQL, model
\* accuracy, cross-tab storage, authorization, billing or latency guarantee.
\* No constraints, symmetry or overrides. Quiescence is not a deadlock.

CONSTANT Fault
ASSUME Fault \in {"none", "unknown_running", "blind_retry", "late_result"}
Ids == {1, 2}
States == {"unknown", "running", "succeeded", "failed"}
Packets == [id: Ids, state: States]

VARIABLES current, issued, sends, confirmed, observation, packets,
          manual, applied, running
vars == <<current, issued, sends, confirmed, observation, packets,
          manual, applied, running>>

Init == /\ current = 1 /\ issued = {1}
        /\ sends = [r \in Ids |-> IF r = 1 THEN 1 ELSE 0]
        /\ confirmed = FALSE
        /\ observation = [id |-> 1, state |-> "unknown"]
        /\ packets = {} /\ manual = FALSE /\ applied = 0
        /\ running = (Fault = "unknown_running")

\* Refresh/reload can only capture a read, not issue execution work.
Capture(r, state) ==
    /\ r \in issued /\ state \in States
    /\ packets' = packets \cup {[id |-> r, state |-> state]}
    /\ UNCHANGED <<current, issued, sends, confirmed, observation,
                   manual, applied, running>>

Deliver(packet) ==
    /\ packet \in packets /\ packets' = packets \ {packet}
    /\ LET eligible == (~manual \/ (Fault = "late_result" /\ packet.state = "succeeded")) /\ applied = 0
           accepts == eligible /\ (packet.id = current \/ Fault = "late_result")
       IN /\ observation' = IF accepts THEN packet ELSE observation
          /\ applied' = IF accepts /\ packet.state = "succeeded"
                         THEN packet.id ELSE applied
          /\ running' = IF accepts
                        THEN packet.state = "running" \/
                             (Fault = "unknown_running" /\ packet.state = "unknown")
                        ELSE running
    /\ UNCHANGED <<current, issued, sends, confirmed, manual>>

ConfirmRerun ==
    /\ current = 1 /\ observation.id = 1
    /\ observation.state = "unknown" /\ ~manual /\ applied = 0
    /\ current' = 2 /\ issued' = {1, 2} /\ confirmed' = TRUE
    /\ sends' = [sends EXCEPT ![2] = 1]
    /\ observation' = [id |-> 2, state |-> "unknown"]
    /\ running' = FALSE
    /\ UNCHANGED <<packets, manual, applied>>

EnterManually ==
    /\ ~manual /\ applied = 0 /\ manual' = TRUE /\ running' = FALSE
    /\ UNCHANGED <<current, issued, sends, confirmed, observation,
                   packets, applied>>

BlindRetry ==
    /\ Fault = "blind_retry" /\ sends[1] = 1
    /\ observation.state = "unknown"
    /\ sends' = [sends EXCEPT ![1] = 2]
    /\ UNCHANGED <<current, issued, confirmed, observation, packets,
                   manual, applied, running>>

Next == ConfirmRerun \/ EnterManually \/ BlindRetry
        \/ (\E r \in Ids, state \in States: Capture(r, state))
        \/ (\E packet \in packets: Deliver(packet))
Spec == Init /\ [][Next]_vars

TypeOK == /\ current \in Ids /\ issued \subseteq Ids
          /\ sends \in [Ids -> 0..2] /\ confirmed \in BOOLEAN
          /\ observation \in Packets /\ packets \subseteq Packets
          /\ manual \in BOOLEAN /\ applied \in {0, 1, 2}
          /\ running \in BOOLEAN
ReadDoesNotResubmit == sends[1] = 1 /\ (sends[2] = 1 => confirmed)
TruthfulRunning == running => observation.id = current /\ observation.state = "running"
CurrentResultOnly == applied = 0 \/ applied = current
ManualWins == manual => applied = 0 /\ ~running
=============================================================================
