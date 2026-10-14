-------------------- MODULE GreenfieldRoundContinuation --------------------
EXTENDS Integers

\* Focused requirement: two rounds, one registered reply per round.
\* Input epochs abstract file versions plus explanations, not semantic truth.
\* A user's new-change confirmation freezes the previous input and starts
\* from its latest registered epoch. An unregistered draft must be handled first.
\* No storage, multi-window atomicity, check execution, approval or liveness claim.
CONSTANTS UseInitialInput, IgnorePendingReply, EditHistorical
VARIABLES currentRound, epochs, pendingReply, baseEpoch, frozenOld
vars == <<currentRound, epochs, pendingReply, baseEpoch, frozenOld>>

Init == /\ currentRound = 1
        /\ epochs = [r \in 1..2 |-> 0]
        /\ pendingReply = 0 /\ baseEpoch = -1 /\ frozenOld = -1
Upload(r) == /\ r \in 1..currentRound /\ pendingReply = 0 /\ epochs[r] = 0
             /\ (r = currentRound \/ EditHistorical)
             /\ pendingReply' = r
             /\ UNCHANGED <<currentRound, epochs, baseEpoch, frozenOld>>
Discard == /\ pendingReply # 0 /\ pendingReply' = 0
           /\ UNCHANGED <<currentRound, epochs, baseEpoch, frozenOld>>
Register == /\ pendingReply # 0
            /\ (pendingReply = currentRound \/ EditHistorical)
            /\ epochs' = [epochs EXCEPT ![pendingReply] = @ + 1]
            /\ pendingReply' = 0
            /\ UNCHANGED <<currentRound, baseEpoch, frozenOld>>
StartNext == /\ currentRound = 1
             /\ (pendingReply = 0 \/ IgnorePendingReply)
             /\ currentRound' = 2
             /\ frozenOld' = epochs[1]
             /\ baseEpoch' = IF UseInitialInput THEN 0 ELSE epochs[1]
             /\ UNCHANGED <<epochs, pendingReply>>
Next == (\E r \in 1..2: Upload(r)) \/ Discard \/ Register \/ StartNext
Spec == Init /\ [][Next]_vars
LatestRegisteredBase == currentRound = 2 => baseEpoch = frozenOld
NoInheritedDraft == currentRound = 2 => pendingReply # 1
HistoricalInputFrozen == currentRound = 2 => epochs[1] = frozenOld
=============================================================================
