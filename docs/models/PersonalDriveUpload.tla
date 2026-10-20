------------------------ MODULE PersonalDriveUpload ------------------------
EXTENDS Naturals, FiniteSets

\* #163 D5 / #165 / #174: one private upload, one fixed byte version and
\* one user-selected destination. This focused Safety model is not a code
\* refinement or a real Feishu guarantee. Parts/prepare cannot publish a file;
\* the durable finish intent is separate from its remote effect and reply.
\* Two finish slots/tokens expose a duplicate after lock loss or lost reply.
\* A read or cancellation cannot prove the remote effect absent. A lost reply
\* may remain unknown forever: no fairness, timeout or liveness is claimed.
\* Atomic SQL intent, caller/tenant authorization, fixed bytes (covered by
\* NativeUploadReceipts), multipart semantics, directory write permission and
\* provider token authenticity require implementation/external evidence.
\* UI removal/navigation remain in AuditUploadDrafts, not this model.
\* No constraints, symmetry or overrides; open-system deadlocks are allowed.

CONSTANT Fault
ASSUME Fault \in {"none", "repeat_finish", "rebind_folder", "foreign_source"}
Folders == {1, 2}
Calls == {1, 2}

VARIABLES folder, originalFolder, issued, effects, knownToken, source, ready
vars == <<folder, originalFolder, issued, effects, knownToken, source, ready>>

Init == /\ folder = 0 /\ originalFolder = 0
        /\ issued = {} /\ effects = {} /\ knownToken = 0
        /\ source = 0 /\ ready = FALSE

Bind(d) == /\ folder = 0
           /\ folder' = d /\ originalFolder' = d
           /\ UNCHANGED <<issued, effects, knownToken, source, ready>>

\* Intent is committed before sending the non-idempotent finish request.
Issue(c) == /\ folder # 0 /\ c \notin issued /\ ~ready
            /\ issued = {} \/ Fault = "repeat_finish"
            /\ issued' = issued \cup {c}
            /\ UNCHANGED <<folder, originalFolder, effects, knownToken, source, ready>>

RemoteEffect(c) == /\ c \in issued /\ c \notin effects
                   /\ effects' = effects \cup {c}
                   /\ UNCHANGED <<folder, originalFolder, issued, knownToken, source, ready>>

Reply(c) == /\ c \in effects /\ knownToken = 0
            /\ knownToken' = IF Fault = "foreign_source" THEN 3 - c ELSE c
            /\ UNCHANGED <<folder, originalFolder, issued, effects, source, ready>>

Attach == /\ knownToken # 0 /\ ~ready
          /\ source' = knownToken /\ ready' = TRUE
          /\ UNCHANGED <<folder, originalFolder, issued, effects, knownToken>>

Retarget(d) == /\ Fault = "rebind_folder" /\ folder # 0 /\ d # folder
               /\ folder' = d
               /\ UNCHANGED <<originalFolder, issued, effects, knownToken, source, ready>>

Next == (\E d \in Folders: Bind(d) \/ Retarget(d))
        \/ (\E c \in Calls: Issue(c) \/ RemoteEffect(c) \/ Reply(c))
        \/ Attach
Spec == Init /\ [][Next]_vars

TypeOK == /\ folder \in Folders \cup {0} /\ originalFolder \in Folders \cup {0}
          /\ issued \subseteq Calls /\ effects \subseteq issued
          /\ knownToken \in Calls \cup {0} /\ source \in Calls \cup {0}
          /\ ready \in BOOLEAN
DestinationBound == folder = originalFolder
OneCloudFile == Cardinality(effects) <= 1
ReadyHasSource == ready => source \in effects /\ source = knownToken /\ folder # 0
=============================================================================
