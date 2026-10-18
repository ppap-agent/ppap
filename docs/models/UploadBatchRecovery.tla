------------------------- MODULE UploadBatchRecovery -------------------------
EXTENDS Naturals
\* #165 focused obligation, not refinement of the whole upload system.
\* File 1 is an already received member of a batch; file 2 is its next upload.
\* Draft 2/file 3 distinguish a different page owner. Request identity is fixed
\* before POST/PUT; NativeUploadReceipts owns the lower-level version contract.
\* PersistIntent separates durable intent from Send. Its acknowledgement can be
\* lost before any upload. Explicit continuation reads first and reuses the
\* SAME request/digest through idempotent create; no new logical request is made.
\* `sends` counts logical identities, not HTTP attempts. Same-ID create/PUT
\* idempotency is an API/storage assumption checked separately, not proved here.
\* Save failure/no response may stutter indefinitely.
\* Leaving invalidates this page instance even when returning to the same draft.
\* An in-flight upload may complete after leaving; its original saved identity
\* remains readable. A GET snapshot and its delivery are separate cut points.
\* No fairness/liveness, cross-tab serialization, SQL/storage durability,
\* authorization, byte-transfer resume, file parsing or model-quality claim.
\* Controlled HTTP browser tests map persist/send/unknown/read/leave/late reply.
\* No state/action constraints, symmetry or operator overrides are used.
CONSTANT Fault
ASSUME Fault \in {"none", "drop", "fresh", "late"}
Drafts == {1, 2}
Files == {1, 2, 3}
VARIABLES current, eligible, stage, saved, visible, remoteReady,
          sends, pendingRead, lateCommit
vars == <<current, eligible, stage, saved, visible, remoteReady,
          sends, pendingRead, lateCommit>>
Init == /\ current = 1 /\ eligible = TRUE /\ stage = "new"
        /\ saved = [d \in Drafts |-> IF d = 1 THEN {1} ELSE {3}]
        /\ visible = saved /\ remoteReady = FALSE /\ sends = 0
        /\ pendingRead = "none" /\ lateCommit = FALSE
PersistIntent ==
    /\ stage = "new" /\ current = 1 /\ eligible
    /\ saved' = [saved EXCEPT ![1] = @ \cup {2}] /\ stage' = "prepared"
    /\ UNCHANGED <<current, eligible, visible, remoteReady, sends,
                   pendingRead, lateCommit>>
LoseSaveAck ==
    /\ stage = "prepared" /\ sends = 0 /\ stage' = "unknown"
    /\ UNCHANGED <<current, eligible, saved, visible, remoteReady, sends,
                   pendingRead, lateCommit>>
Send == /\ stage = "prepared" /\ current = 1 /\ eligible
        /\ sends' = 1 /\ stage' = "sent"
        /\ UNCHANGED <<current, eligible, saved, visible, remoteReady,
                       pendingRead, lateCommit>>
CompleteRemote ==
    /\ sends > 0 /\ ~remoteReady /\ remoteReady' = TRUE
    /\ UNCHANGED <<current, eligible, stage, saved, visible, sends,
                   pendingRead, lateCommit>>
LoseReply ==
    /\ stage = "sent" /\ stage' = "unknown"
    /\ saved' = IF Fault = "drop" THEN [saved EXCEPT ![1] = {}] ELSE saved
    /\ UNCHANGED <<current, eligible, visible, remoteReady, sends,
                   pendingRead, lateCommit>>
CaptureRead ==
    /\ stage = "unknown" /\ current = 1 /\ eligible
    /\ pendingRead = "none"
    /\ pendingRead' = IF remoteReady THEN "ready" ELSE "unknown"
    /\ UNCHANGED <<current, eligible, stage, saved, visible, remoteReady,
                   sends, lateCommit>>
Deliver ==
    /\ ((stage = "sent" /\ remoteReady) \/ pendingRead = "ready")
    /\ LET allowed == eligible /\ current = 1
           wrong == ~allowed /\ Fault = "late"
       IN /\ visible' = IF allowed \/ wrong
                        THEN [visible EXCEPT ![current] = @ \cup {2}] ELSE visible
          /\ lateCommit' = (lateCommit \/ wrong)
    /\ stage' = "settled" /\ pendingRead' = "none"
    /\ UNCHANGED <<current, eligible, saved, remoteReady, sends>>
LoseRead ==
    /\ pendingRead # "none" /\ pendingRead' = "none"
    /\ UNCHANGED <<current, eligible, stage, saved, visible, remoteReady,
                   sends, lateCommit>>
ContinueOriginal ==
    /\ stage = "unknown" /\ pendingRead = "unknown"
    /\ current = 1 /\ eligible
    /\ stage' = "prepared" /\ pendingRead' = "none"
    /\ UNCHANGED <<current, eligible, saved, visible, remoteReady, sends,
                   lateCommit>>
Navigate(d) ==
    /\ current # d /\ current' = d /\ eligible' = FALSE
    /\ UNCHANGED <<stage, saved, visible, remoteReady, sends,
                   pendingRead, lateCommit>>
FreshOnUnknown ==
    /\ Fault = "fresh" /\ stage = "unknown" /\ sends = 1
    /\ sends' = 2
    /\ UNCHANGED <<current, eligible, stage, saved, visible, remoteReady,
                   pendingRead, lateCommit>>
Next == PersistIntent \/ LoseSaveAck \/ Send \/ CompleteRemote \/ LoseReply \/ CaptureRead
        \/ Deliver \/ LoseRead \/ ContinueOriginal \/ FreshOnUnknown
        \/ (\E d \in Drafts: Navigate(d))
Spec == Init /\ [][Next]_vars
TypeOK == /\ current \in Drafts /\ eligible \in BOOLEAN
          /\ stage \in {"new", "prepared", "sent", "unknown", "settled"}
          /\ saved \in [Drafts -> SUBSET Files]
          /\ visible \in [Drafts -> SUBSET Files]
          /\ remoteReady \in BOOLEAN /\ sends \in 0..2
          /\ pendingRead \in {"none", "ready", "unknown"}
          /\ lateCommit \in BOOLEAN
FirstSuccessPreserved == 1 \in saved[1]
EffectHasOwner == sends > 0 => 2 \in saved[1]
OneLogicalRequest == sends <= 1
NoLateCommit == ~lateCommit /\ 2 \notin visible[2]
=============================================================================
