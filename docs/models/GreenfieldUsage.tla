--------------------------- MODULE GreenfieldUsage ---------------------------
EXTENDS Integers
\* Focused obligation, not a billing service: two checks share 100 example points.
\* A request reserves 40. Only an observed settlement (0 or 20) replaces it.
\* The environment may never supply acceptance/settlement. No fairness assumed.
\* Missing old records invalidate the exact balance; they do not imply zero cost.
CONSTANT ReleaseUnknown
VARIABLES phase, held, charged, remote, incomplete
vars == <<phase, held, charged, remote, incomplete>>
Checks == 1..2
Init == /\ phase = [c \in Checks |-> "none"]
        /\ held = [c \in Checks |-> 0]
        /\ charged = [c \in Checks |-> 0]
        /\ remote = [c \in Checks |-> "pending"]
        /\ incomplete = FALSE
Balance == 100 - held[1] - held[2] - charged[1] - charged[2]
Start(c) == /\ phase[c] \in {"none", "absent"} /\ ~incomplete /\ Balance >= 40
            /\ phase' = [phase EXCEPT ![c] = "unknown"]
            /\ held' = [held EXCEPT ![c] = 40]
            /\ remote' = [remote EXCEPT ![c] = "pending"]
            /\ UNCHANGED <<charged, incomplete>>
Remote(c) == /\ phase[c] = "unknown" /\ remote[c] = "pending"
             /\ \E outcome \in {"accepted", "absent"} : remote' = [remote EXCEPT ![c] = outcome]
             /\ UNCHANGED <<phase, held, charged, incomplete>>
Observe(c) == /\ phase[c] = "unknown" /\ remote[c] # "pending"
              /\ phase' = [phase EXCEPT ![c] = remote[c]]
              /\ held' = [held EXCEPT ![c] = IF remote[c] = "absent" THEN 0 ELSE 40]
              /\ UNCHANGED <<charged, remote, incomplete>>
Finish(c) == /\ phase[c] = "accepted"
             /\ phase' = [phase EXCEPT ![c] = "pendingSettlement"]
             /\ UNCHANGED <<held, charged, remote, incomplete>>
ReadFailure(c) == /\ phase[c] \in {"unknown", "pendingSettlement"}
                  /\ held' = [held EXCEPT ![c] = IF ReleaseUnknown THEN 0 ELSE @]
                  /\ UNCHANGED <<phase, charged, remote, incomplete>>
Settle(c, amount) == /\ phase[c] = "pendingSettlement" /\ amount \in {0, 20}
                     /\ phase' = [phase EXCEPT ![c] = "settled"]
                     /\ charged' = [charged EXCEPT ![c] = amount]
                     /\ held' = [held EXCEPT ![c] = 0]
                     /\ UNCHANGED <<remote, incomplete>>
MissingRecord == /\ incomplete' = TRUE
                 /\ UNCHANGED <<phase, held, charged, remote>>
Next == MissingRecord \/ (\E c \in Checks : Start(c) \/ Remote(c) \/ Observe(c)
        \/ Finish(c) \/ ReadFailure(c) \/ (\E a \in {0, 20} : Settle(c, a)))
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in [Checks -> {"none", "unknown", "absent", "accepted", "pendingSettlement", "settled"}]
          /\ held \in [Checks -> {0, 40}] /\ charged \in [Checks -> {0, 20}]
          /\ remote \in [Checks -> {"pending", "accepted", "absent"}] /\ incomplete \in BOOLEAN
KeepUnresolved == \A c \in Checks : phase[c] \in {"unknown", "accepted", "pendingSettlement"} => held[c] = 40
OneContribution == \A c \in Checks : ~(held[c] > 0 /\ charged[c] > 0)
ReceiptRequired == \A c \in Checks : phase[c] = "settled" => remote[c] = "accepted"
Nonnegative == Balance >= 0
=============================================================================
