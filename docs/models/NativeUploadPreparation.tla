----------------------- MODULE NativeUploadPreparation -----------------------
EXTENDS Naturals, FiniteSets

\* #165 focused Safety obligation, NOT full-system refinement. One private Draft
\* retains one original upload request/digest, plus an already ready file. Two
\* page instances can save choices or promote the SAME Draft concurrently.
\* Choices 1/2 stand for distinct selection/removal snapshots, not file bytes.
\* The server's row lock and revision comparison are represented by one atomic
\* Save/Promote step; PostgreSQL durability/authorization are assumptions and
\* need the public API/PG tests. The original upload ID/digest are held fixed.
\* NativeUploadReceipts separately owns transfer/version identity; its evidence
\* is not inherited. There are no SQL, streaming, model, or browser claims here.
\*
\* PersistIntent commits before Send. Its acknowledgement may be lost; Reload
\* clears page memory, and Read restores the saved original identity. Upload
\* completion is independent from the client reply. Unknown only permits GET;
\* no action constructs a replacement request or automatically retransmits.
\* Promote captures exactly a saved revision and choice, creates one Task and
\* no Round, and rejects all subsequent preparation writes. Read after promotion
\* opens that Task, not an editable preparation. Same promotion request replay
\* is a read/stuttering step; distinct requests cannot allocate a second Task.
\*
\* Finite instance: two actors, two choices, revisions 0..2, one upload request,
\* one Draft/Task. Two contenders expose stale save/promotion ordering. No state
\* constraints, symmetry or overrides. No fairness/liveness/latency guarantee;
\* CHECK_DEADLOCK FALSE allows unresolved effects/open-environment quiescence.
\* Fault variants are deliberate counterexamples, not product modes.

CONSTANT Fault
ASSUME Fault \in {"none", "unpersisted", "late_save"}
Actors == {1, 2}
Request == "original-upload"
Digest == "original-digest"
VARIABLES exists, revision, choice, state, persisted, sent, remoteReady,
          unknown, pageKnown, pageRevision, taskCount, frozen, frozenRevision,
          exposedTask, lateWrite
vars == <<exists, revision, choice, state, persisted, sent, remoteReady,
          unknown, pageKnown, pageRevision, taskCount, frozen, frozenRevision,
          exposedTask, lateWrite>>

Init == /\ exists = FALSE /\ revision = 0 /\ choice = 1 /\ state = "preparation"
        /\ persisted = {} /\ sent = {} /\ remoteReady = FALSE /\ unknown = FALSE
        /\ pageKnown = [a \in Actors |-> FALSE]
        /\ pageRevision = [a \in Actors |-> 0]
        /\ taskCount = 0 /\ frozen = 0 /\ frozenRevision = 0
        /\ exposedTask = [a \in Actors |-> FALSE] /\ lateWrite = FALSE

PersistIntent ==
    /\ ~exists /\ exists' = TRUE /\ persisted' = {<<Request, Digest>>}
    /\ pageKnown' = [pageKnown EXCEPT ![1] = TRUE]
    /\ UNCHANGED <<revision, choice, state, sent, remoteReady, unknown,
                   pageRevision, taskCount, frozen, frozenRevision, exposedTask, lateWrite>>

Send ==
    /\ sent = {}
    /\ (<<Request, Digest>> \in persisted \/ Fault = "unpersisted")
    /\ sent' = {<<Request, Digest>>}
    /\ UNCHANGED <<exists, revision, choice, state, persisted, remoteReady,
                   unknown, pageKnown, pageRevision, taskCount, frozen,
                   frozenRevision, exposedTask, lateWrite>>

LoseReply ==
    /\ sent # {} /\ ~unknown /\ unknown' = TRUE
    /\ UNCHANGED <<exists, revision, choice, state, persisted, sent, remoteReady,
                   pageKnown, pageRevision, taskCount, frozen, frozenRevision,
                   exposedTask, lateWrite>>

FinishUpload ==
    /\ sent # {} /\ ~remoteReady /\ remoteReady' = TRUE
    /\ UNCHANGED <<exists, revision, choice, state, persisted, sent, unknown,
                   pageKnown, pageRevision, taskCount, frozen, frozenRevision,
                   exposedTask, lateWrite>>

Reload(a) ==
    /\ pageKnown[a] /\ pageKnown' = [pageKnown EXCEPT ![a] = FALSE]
    /\ exposedTask' = [exposedTask EXCEPT ![a] = FALSE]
    /\ UNCHANGED <<exists, revision, choice, state, persisted, sent, remoteReady,
                   unknown, pageRevision, taskCount, frozen, frozenRevision, lateWrite>>

Read(a) ==
    /\ exists
    /\ pageKnown' = [pageKnown EXCEPT ![a] = TRUE]
    /\ pageRevision' = [pageRevision EXCEPT ![a] = revision]
    /\ exposedTask' = [exposedTask EXCEPT ![a] = (state = "task")]
    /\ UNCHANGED <<exists, revision, choice, state, persisted, sent, remoteReady,
                   unknown, taskCount, frozen, frozenRevision, lateWrite>>

Save(a, value) ==
    /\ exists /\ state = "preparation" /\ revision < 2
    /\ pageKnown[a] /\ pageRevision[a] = revision
    /\ revision' = revision + 1 /\ choice' = value
    /\ pageRevision' = [pageRevision EXCEPT ![a] = revision + 1]
    /\ UNCHANGED <<exists, state, persisted, sent, remoteReady, unknown,
                   pageKnown, taskCount, frozen, frozenRevision, exposedTask, lateWrite>>

Promote(a) ==
    /\ exists /\ state = "preparation" /\ remoteReady
    /\ pageKnown[a] /\ pageRevision[a] = revision
    /\ state' = "task" /\ taskCount' = taskCount + 1
    /\ frozen' = choice /\ frozenRevision' = revision
    /\ exposedTask' = [exposedTask EXCEPT ![a] = TRUE]
    /\ UNCHANGED <<exists, revision, choice, persisted, sent, remoteReady,
                   unknown, pageKnown, pageRevision, lateWrite>>

LateSave ==
    /\ Fault = "late_save" /\ state = "task" /\ ~lateWrite
    /\ choice' = (IF choice = 1 THEN 2 ELSE 1) /\ lateWrite' = TRUE
    /\ UNCHANGED <<exists, revision, state, persisted, sent, remoteReady,
                   unknown, pageKnown, pageRevision, taskCount, frozen,
                   frozenRevision, exposedTask>>

Next == PersistIntent \/ Send \/ LoseReply \/ FinishUpload \/ LateSave
        \/ (\E a \in Actors: Reload(a) \/ Read(a) \/ Promote(a)
                              \/ (\E value \in {1, 2}: Save(a, value)))
Spec == Init /\ [][Next]_vars
TypeOK == /\ exists \in BOOLEAN /\ revision \in 0..2 /\ choice \in {1, 2}
          /\ state \in {"preparation", "task"}
          /\ persisted \subseteq {<<Request, Digest>>} /\ sent \subseteq {<<Request, Digest>>}
          /\ remoteReady \in BOOLEAN /\ unknown \in BOOLEAN
          /\ pageKnown \in [Actors -> BOOLEAN] /\ pageRevision \in [Actors -> 0..2]
          /\ taskCount \in 0..1 /\ frozen \in 0..2 /\ frozenRevision \in 0..2
          /\ exposedTask \in [Actors -> BOOLEAN] /\ lateWrite \in BOOLEAN
PersistBeforeEffect == sent \subseteq persisted
OriginalIdentityRetained == exists => persisted = {<<Request, Digest>>}
OnePromotion == (state = "task") <=> (taskCount = 1)
FrozenSnapshot == state = "task" => choice = frozen /\ revision = frozenRevision /\ ~lateWrite
ReadOnlyTaskRecovery == \A a \in Actors: exposedTask[a] => state = "task"
=============================================================================
