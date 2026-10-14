-------------------------- MODULE GreenfieldDelivery --------------------------
EXTENDS Naturals, FiniteSets

\* Focused obligation: one deliberate delivery, two attempts, two package versions.
\* A confirmed absence closes that attempt; an unknown outcome does not.
\* No network fairness or eventual delivery is assumed. Waiting may stutter forever.
CONSTANTS FaultyResend, FaultyBinding
VARIABLES phase, selected, bound, attempt, payload, remote, effects
vars == <<phase, selected, bound, attempt, payload, remote, effects>>
Attempts == 1..2
Init == /\ phase = "idle" /\ selected = 1 /\ bound = 0 /\ attempt = 0
        /\ payload = [a \in Attempts |-> 0]
        /\ remote = [a \in Attempts |-> "none"] /\ effects = 0
ChooseNewPackage == /\ selected = 1 /\ selected' = 2
                    /\ UNCHANGED <<phase, bound, attempt, payload, remote, effects>>
Send == /\ phase \in {"idle", "not_sent"} /\ attempt < 2
        /\ LET n == attempt + 1 IN
             /\ attempt' = n /\ phase' = "sending"
             /\ bound' = IF attempt = 0 THEN selected ELSE bound
             /\ payload' = [payload EXCEPT ![n] = IF attempt = 0 \/ FaultyBinding THEN selected ELSE bound]
             /\ remote' = [remote EXCEPT ![n] = "pending"]
        /\ UNCHANGED <<selected, effects>>
Deliver(a) == /\ a <= attempt /\ remote[a] = "pending"
              /\ remote' = [remote EXCEPT ![a] = "sent"] /\ effects' = effects + 1
              /\ UNCHANGED <<phase, selected, bound, attempt, payload>>
ConfirmAbsent(a) == /\ a <= attempt /\ remote[a] = "pending"
                    /\ remote' = [remote EXCEPT ![a] = "absent"]
                    /\ UNCHANGED <<phase, selected, bound, attempt, payload, effects>>
Observe(a) == /\ a = attempt /\ phase \in {"sending", "unknown"}
              /\ remote[a] \in {"sent", "absent"}
              /\ phase' = IF remote[a] = "sent" THEN "sent" ELSE "not_sent"
              /\ UNCHANGED <<selected, bound, attempt, payload, remote, effects>>
LoseObservation == /\ phase = "sending" /\ phase' = "unknown"
                   /\ UNCHANGED <<selected, bound, attempt, payload, remote, effects>>
BlindRetry == /\ FaultyResend /\ phase = "unknown" /\ attempt < 2
              /\ phase' = "not_sent"
              /\ UNCHANGED <<selected, bound, attempt, payload, remote, effects>>
Next == ChooseNewPackage \/ Send \/ LoseObservation \/ BlindRetry
        \/ (\E a \in Attempts : Deliver(a) \/ ConfirmAbsent(a) \/ Observe(a))
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in {"idle", "sending", "unknown", "sent", "not_sent"}
          /\ selected \in 1..2 /\ bound \in 0..2 /\ attempt \in 0..2
          /\ payload \in [Attempts -> 0..2]
          /\ remote \in [Attempts -> {"none", "pending", "sent", "absent"}]
          /\ effects \in 0..2
FrozenDeliveryBinding == \A a \in 1..attempt : payload[a] = bound
NoDuplicateDelivery == effects <= 1
SentRequiresReceipt == phase = "sent" => remote[attempt] = "sent"
=============================================================================
