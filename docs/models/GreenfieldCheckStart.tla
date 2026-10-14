------------------------ MODULE GreenfieldCheckStart -------------------------
EXTENDS Naturals
\* Focused obligation: one registered receipt, one start intent, two attempts.
\* Two plans distinguish a frozen request from an edited selection. Two attempts
\* expose duplicate acceptance after a lost observation. Confirmed absence closes
\* an attempt; a timeout cannot do so. No eventual network response is assumed.
CONSTANT FaultyRetry
VARIABLES phase, chosen, bound, attempt, payload, remote, accepted, quota
vars == <<phase, chosen, bound, attempt, payload, remote, accepted, quota>>
Attempts == 1..2
Init == /\ phase = "ready" /\ chosen = 1 /\ bound = 0 /\ attempt = 0
        /\ payload = [a \in Attempts |-> 0]
        /\ remote = [a \in Attempts |-> "none"] /\ accepted = 0
        /\ quota = "enough"
Choose == /\ phase = "ready" /\ chosen' \in 1..2
          /\ UNCHANGED <<phase, bound, attempt, payload, remote, accepted, quota>>
ReadQuota == /\ phase \in {"ready", "absent"}
             /\ quota' \in {"enough", "low", "unknown"}
             /\ UNCHANGED <<phase, chosen, bound, attempt, payload, remote, accepted>>
Start == /\ phase \in {"ready", "absent"} /\ quota = "enough" /\ attempt < 2
         /\ LET n == attempt + 1 IN
              /\ attempt' = n /\ phase' = "requesting"
              /\ bound' = IF attempt = 0 THEN chosen ELSE bound
              /\ payload' = [payload EXCEPT ![n] = IF attempt = 0 THEN chosen ELSE bound]
              /\ remote' = [remote EXCEPT ![n] = "pending"]
         /\ UNCHANGED <<chosen, accepted, quota>>
Accept(a) == /\ remote[a] = "pending"
             /\ remote' = [remote EXCEPT ![a] = "accepted"]
             /\ accepted' = accepted + 1
             /\ UNCHANGED <<phase, chosen, bound, attempt, payload, quota>>
Reject(a) == /\ remote[a] = "pending"
             /\ remote' = [remote EXCEPT ![a] = "absent"]
             /\ UNCHANGED <<phase, chosen, bound, attempt, payload, accepted, quota>>
Observe == /\ phase \in {"requesting", "unknown"}
           /\ remote[attempt] \in {"accepted", "absent"}
           /\ phase' = remote[attempt]
           /\ UNCHANGED <<chosen, bound, attempt, payload, remote, accepted, quota>>
LoseOrLeave == /\ phase = "requesting" /\ phase' = "unknown"
               /\ UNCHANGED <<chosen, bound, attempt, payload, remote, accepted, quota>>
BlindRetry == /\ FaultyRetry /\ phase = "unknown" /\ phase' = "absent"
              /\ UNCHANGED <<chosen, bound, attempt, payload, remote, accepted, quota>>
Next == Choose \/ ReadQuota \/ Start \/ Observe \/ LoseOrLeave \/ BlindRetry
        \/ (\E a \in Attempts : Accept(a) \/ Reject(a))
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in {"ready", "requesting", "unknown", "accepted", "absent"}
          /\ chosen \in 1..2 /\ bound \in 0..2 /\ attempt \in 0..2
          /\ payload \in [Attempts -> 0..2]
          /\ remote \in [Attempts -> {"none", "pending", "accepted", "absent"}]
          /\ accepted \in 0..2 /\ quota \in {"enough", "low", "unknown"}
OneAcceptance == accepted <= 1
FrozenPlan == \A a \in 1..attempt : payload[a] = bound
ReceiptRequired == phase = "accepted" => remote[attempt] = "accepted"
=============================================================================
