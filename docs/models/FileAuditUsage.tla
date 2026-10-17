---------------------------- MODULE FileAuditUsage ----------------------------
EXTENDS Integers, FiniteSets

\* Focused #168 / #163 D4 obligation, not an external billing proof.
\* Each committed Round has one 500-point obligation per frozen matched target.
\* Files 1 and 2 are matched targets; file 3 is unmatched/unselected and excluded.
\* Two distinct Rounds reuse the same file versions: explicit re-audit is new
\* consumption, unlike duplicate confirmation or a technical execution retry.
\* A DB transaction atomically commits acceptance and file obligations. Replies
\* can be lost; replay reads committed state rather than adding consumption.
\* Amounts are business obligations, NOT confirmed Feishu debit receipts.
\* Free/paid allocation, quotas, authorization, rule freshness, provider orders,
\* settlement, crashes inside PostgreSQL and liveness are outside this model.
\* NativeTaskAcceptance owns key/Task/actor/revision binding. Here requests have
\* already passed that gate; the focus is only the atomic file-fee boundary.
\* Two confirmations can overlap; each Round also permits one technical retry.
\* No fairness, constraints, symmetry or overrides. Terminal quiescence is valid.
CONSTANT Fault
ASSUME Fault \in {"none", "duplicate", "split", "excluded"}
Rounds == 1..2
Files == 1..3
Targets == {1, 2}
VARIABLES accepted, obligations, replies, attempts
vars == <<accepted, obligations, replies, attempts>>

Init == /\ accepted = {}
        /\ obligations = [r \in Rounds |-> [f \in Files |-> 0]]
        /\ replies = [r \in Rounds |-> "pending"]
        /\ attempts = [r \in Rounds |-> 0]

Confirm(r) ==
    /\ r \notin accepted
    /\ accepted' = accepted \cup {r}
    /\ obligations' = [obligations EXCEPT ![r] =
          [f \in Files |-> IF Fault = "split" THEN 0
             ELSE IF f \in Targets \/ Fault = "excluded" THEN 500 ELSE 0]]
    /\ attempts' = [attempts EXCEPT ![r] = 1]
    /\ UNCHANGED replies

LoseReply(r) == /\ replies[r] = "pending"
                /\ replies' = [replies EXCEPT ![r] = "unknown"]
                /\ UNCHANGED <<accepted, obligations, attempts>>

Replay(r) == /\ r \in accepted
             /\ replies[r] # "accepted"
             /\ replies' = [replies EXCEPT ![r] = "accepted"]
             /\ obligations' = IF Fault = "duplicate"
                  THEN [obligations EXCEPT ![r] =
                      [f \in Files |-> IF f \in Targets THEN 1000 ELSE 0]]
                  ELSE obligations
             /\ UNCHANGED <<accepted, attempts>>

RetryExecution(r) == /\ r \in accepted /\ attempts[r] = 1
                     /\ attempts' = [attempts EXCEPT ![r] = 2]
                     /\ UNCHANGED <<accepted, obligations, replies>>

Next == \E r \in Rounds : Confirm(r) \/ LoseReply(r) \/ Replay(r) \/ RetryExecution(r)
Spec == Init /\ [][Next]_vars
TypeOK == /\ accepted \subseteq Rounds
          /\ obligations \in [Rounds -> [Files -> {0, 500, 1000}]]
          /\ replies \in [Rounds -> {"pending", "unknown", "accepted"}]
          /\ attempts \in [Rounds -> 0..2]
ExactObligation == \A r \in Rounds, f \in Files :
    obligations[r][f] = IF r \in accepted /\ f \in Targets THEN 500 ELSE 0
ReceiptNeedsAcceptance == \A r \in Rounds : replies[r] = "accepted" => r \in accepted
=============================================================================
