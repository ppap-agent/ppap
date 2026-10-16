-------------------------- MODULE AuditUploadDrafts --------------------------
EXTENDS Naturals
\* Focused UI obligation under #165: two in-memory upload drafts and their
\* history entries. Draft payloads 1/2 stand for distinct files and choices.
\* A successful match binds an entry to its actual Task; revisiting cannot
\* create a second Task or expose an old editable draft instead of that Task.
\* Task payload edits and acceptance are outside this navigation obligation.
\* One pending upload/read may never answer, or answer after leaving and
\* returning to the same draft. Navigation invalidates its commit authority,
\* not the external request. Retained request/readback semantics are outside
\* this model and require controlled upload-adapter tests.
\* No fairness, deadlines, persistence, authorization or external-effect proof.
CONSTANT Fault
Drafts == {1, 2}
VARIABLES current, screen, shown, stored, creations, taskPayload,
          pending, eligible, lateCommit
vars == <<current, screen, shown, stored, creations, taskPayload,
          pending, eligible, lateCommit>>
Init == /\ current = 1 /\ screen = "draft" /\ shown = 1
        /\ stored = [d \in Drafts |-> d]
        /\ creations = [d \in Drafts |-> 0]
        /\ taskPayload = [d \in Drafts |-> 0]
        /\ pending = 0 /\ eligible = FALSE /\ lateCommit = FALSE
Navigate(d) ==
    /\ (d # current \/ screen = "task")
    /\ LET key == IF Fault = "shared" THEN 1 ELSE current
           saved == IF screen = "draft"
                    THEN [stored EXCEPT ![key] = IF Fault = "drop" THEN 0 ELSE shown]
                    ELSE stored
           opensTask == creations[d] > 0 /\ Fault # "duplicate"
       IN /\ current' = d /\ stored' = saved
          /\ screen' = IF opensTask THEN "task" ELSE "draft"
          /\ shown' = IF opensTask THEN taskPayload[d] ELSE saved[d]
          /\ eligible' = FALSE
          /\ UNCHANGED <<creations, taskPayload, pending, lateCommit>>
CreateTask == /\ screen = "draft" /\ creations[current] < 2
              /\ creations' = [creations EXCEPT ![current] = @ + 1]
              /\ taskPayload' = [taskPayload EXCEPT ![current] = shown]
              /\ screen' = "task" /\ eligible' = FALSE
              /\ UNCHANGED <<current, shown, stored, pending, lateCommit>>
StartRead == /\ screen = "draft" /\ pending = 0
             /\ pending' = current /\ eligible' = TRUE
             /\ UNCHANGED <<current, screen, shown, stored, creations, taskPayload, lateCommit>>
CompleteRead == /\ pending # 0
                /\ lateCommit' = (lateCommit \/ (Fault = "late" /\ ~eligible))
                /\ pending' = 0 /\ eligible' = FALSE
                /\ UNCHANGED <<current, screen, shown, stored, creations, taskPayload>>
Next == (\E d \in Drafts: Navigate(d)) \/ CreateTask \/ StartRead \/ CompleteRead
Spec == Init /\ [][Next]_vars
TypeOK == /\ current \in Drafts /\ screen \in {"draft", "task"}
          /\ shown \in 0..2 /\ stored \in [Drafts -> 0..2]
          /\ creations \in [Drafts -> 0..2] /\ taskPayload \in [Drafts -> 0..2]
          /\ pending \in Drafts \cup {0} /\ eligible \in BOOLEAN /\ lateCommit \in BOOLEAN
OwnDraftOnly == screen = "draft" => shown = current
OneTaskPerDraft == \A d \in Drafts: creations[d] <= 1
ExistingTaskSourceOnly == screen = "task" => shown = taskPayload[current] /\ taskPayload[current] = current
NoLateCommit == ~lateCommit
=============================================================================
