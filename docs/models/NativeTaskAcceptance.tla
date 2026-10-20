----------------------- MODULE NativeTaskAcceptance -------------------------
EXTENDS Integers, FiniteSets

\* Focused #164/#165 obligation for the existing Draft/Accept/Round transaction,
\* not a refinement proof for GreenfieldCheckStart or TaskRecognitionRevisions.
\* Projection: recognition's current Task revision becomes revision[t]; its
\* completed matching results become InputsAt(t, revision[t]). Confirm captures
\* that revision. A successful existing acceptance transaction freezes exactly
\* its matched/selected per-file versions and rules plus explicit reference
\* versions without a rule, creates one Round and its
\* existing engine job, and locks further recognition input changes.
\*
\* Two Tasks have the same creator, so cross-Task reuse of a successful key is
\* not hidden by owner authorization. A second actor is a non-owner. Two keys
\* can independently confirm one Task; a key is not a Round or Task identity.
\* Two revisions expose confirm/rematch interleaving. Revision 0 has two
\* matched targets with distinct rules. Revision 1 removes the second target
\* and uses a distinct frozen rule. An unmatched third file never enters either
\* execution input set. A fourth file is an explicit reference in both revisions;
\* it must not disappear or acquire a target role/rule. Actual bytes, parsing,
\* customer scope/ambiguity resolution, recognition jobs and arbitrary
\* selection sets are delegated to TaskRecognitionRevisions / implementation
\* tests; this model checks the acceptance cut only.
\*
\* Invoke, DB commit, delivery/lost response and receipt readback are separate.
\* The successful DB action is atomic across Task binding, immutable Round and
\* job creation. Fault split_commit exposes its forbidden intermediate state.
\* No S3/model/Worker execution or other external side effect is inside that
\* transaction. Unknown/absent readback stays unknown and does not authorize a
\* new request. Independent keys model separate entry points, not automatic
\* client retry. Replay is one authorized read/transaction response over durable
\* Round facts; response transit delay is omitted because those facts cannot
\* change after acceptance. LastReply retains the identity needed to check a
\* leaked/wrong-Task response rather than a second receipt/business ledger.
\*
\* Finite instance: 2 Tasks, 2 keys, 2 actors, 2 revisions, 2 possible Round IDs,
\* 4 file versions per Task, and 4 rule-version tokens per Task. Round IDs are
\* allocated independently of keys. Both Tasks intentionally have one owner;
\* tenant partitioning, changing ACLs and legacy accepted Drafts are outside
\* this focused owner-only Task contract. No constraint, symmetry or override.
\* No fairness, liveness, fees, report lifecycle, full SQL implementation,
\* rollback/crash recovery, or production entry-switch claim. Charging is
\* disabled for the technical integration this model supports. The DB atomic
\* boundary is assumed; the negative split is a regression obligation, not a
\* proof of PostgreSQL isolation or transaction configuration.
\* CHECK_DEADLOCK FALSE permits terminal/waiting quiescence and non-response.

CONSTANT Fault
ASSUME Fault \in {"none", "stale_revision", "cross_task_key", "split_commit",
                  "wrong_inputs", "dropped_reference", "reference_as_target"}

Tasks == {1, 2}
Keys == {"key-a", "key-b"}
Actors == {"owner", "foreign"}
Revisions == {0, 1}
RoundIds == {1, 2}
NoRound == 0
NoTask == 0
NoRevision == -1
Owner(t) == "owner"
TargetsAt(rev) == IF rev = 0 THEN {1, 2} ELSE {1}
FileVersion(t, file) == 10 * t + file
RuleVersion(t, file, rev) == 100 * t + 10 * rev + file
InputsAt(t, rev) ==
    {<<FileVersion(t, file), "target", RuleVersion(t, file, rev)>> :
        file \in TargetsAt(rev)} \cup {<<FileVersion(t, 4), "reference", 0>>}
\* The forbidden unmatched version is in the checked value domain, not just
\* absent from a property antecedent. This fault silently substitutes it for
\* a matched/selected target; exact per-file freeze must reject that behavior.
WrongInputsAt(t, rev) ==
    {<<FileVersion(t, 1), "target", RuleVersion(t, 1, rev)>>,
     <<FileVersion(t, 3), "target", RuleVersion(t, 3, 0)>>,
     <<FileVersion(t, 4), "reference", 0>>}
DroppedReferenceAt(t, rev) ==
    InputsAt(t, rev) \ {<<FileVersion(t, 4), "reference", 0>>}
ReferenceAsTargetAt(t, rev) ==
    DroppedReferenceAt(t, rev) \cup
        {<<FileVersion(t, 4), "target", RuleVersion(t, 1, rev)>>}
AllInputs == UNION { {InputsAt(t, rev), WrongInputsAt(t, rev),
                      DroppedReferenceAt(t, rev), ReferenceAsTargetAt(t, rev)} :
                     t \in Tasks, rev \in Revisions}
EmptyRound == [exists |-> FALSE, task |-> NoTask, key |-> "none",
    actor |-> "none", requestedRevision |-> NoRevision,
    frozenRevision |-> NoRevision, inputs |-> {}]
EmptyCommand == [task |-> NoTask, actor |-> "none", revision |-> NoRevision]
EmptyReply == [status |-> "none", task |-> NoTask, key |-> "none",
    actor |-> "none", round |-> NoRound]
Reply(status, task, key, actor, round) ==
    [status |-> status, task |-> task, key |-> key,
     actor |-> actor, round |-> round]

VARIABLES revision, taskRound, rounds, jobs, command, server, client, lastReply
vars == <<revision, taskRound, rounds, jobs, command, server, client, lastReply>>

ExistingRounds == {r \in RoundIds : rounds[r].exists}
ForKey(key) == {r \in ExistingRounds : rounds[r].key = key}
ForTask(task) == {r \in ExistingRounds : rounds[r].task = task}
RoundFor(key) == CHOOSE r \in ForKey(key) : TRUE

Init ==
    /\ revision = [t \in Tasks |-> 0]
    /\ taskRound = [t \in Tasks |-> NoRound]
    /\ rounds = [r \in RoundIds |-> EmptyRound]
    /\ jobs = {}
    /\ command = [k \in Keys |-> EmptyCommand]
    /\ server = [k \in Keys |-> [status |-> "unused", round |-> NoRound]]
    /\ client = [k \in Keys |-> "unused"]
    /\ lastReply = EmptyReply

Invoke(key, task, actor) ==
    /\ client[key] = "unused"
    /\ command' = [command EXCEPT ![key] =
        [task |-> task, actor |-> actor, revision |-> revision[task]]]
    /\ server' = [server EXCEPT ![key].status = "pending"]
    /\ client' = [client EXCEPT ![key] = "pending"]
    /\ UNCHANGED <<revision, taskRound, rounds, jobs, lastReply>>

Rematch(task) ==
    /\ taskRound[task] = NoRound
    /\ revision[task] = 0
    /\ revision' = [revision EXCEPT ![task] = 1]
    /\ UNCHANGED <<taskRound, rounds, jobs, command, server, client, lastReply>>

\* One row-lock/transaction boundary: compare captured revision while holding
\* the Task lock, then freeze the current per-target input and queue once.
Commit(key, round) ==
    /\ server[key].status = "pending"
    /\ LET c == command[key] IN
        /\ c.actor = Owner(c.task)
        /\ taskRound[c.task] = NoRound
        /\ ForKey(key) = {}
        /\ ~rounds[round].exists
        /\ (c.revision = revision[c.task] \/ Fault = "stale_revision")
        /\ taskRound' = [taskRound EXCEPT ![c.task] = round]
        /\ rounds' = [rounds EXCEPT ![round] =
            [exists |-> TRUE, task |-> c.task, key |-> key, actor |-> c.actor,
             requestedRevision |-> c.revision,
             frozenRevision |-> revision[c.task],
             inputs |-> IF Fault = "wrong_inputs"
                THEN WrongInputsAt(c.task, revision[c.task])
                ELSE IF Fault = "dropped_reference"
                THEN DroppedReferenceAt(c.task, revision[c.task])
                ELSE IF Fault = "reference_as_target"
                THEN ReferenceAsTargetAt(c.task, revision[c.task])
                ELSE InputsAt(c.task, revision[c.task])]]
        /\ server' = [server EXCEPT ![key] =
            [status |-> "accepted", round |-> round]]
        /\ jobs' = IF Fault = "split_commit" THEN jobs ELSE jobs \cup {round}
    /\ UNCHANGED <<revision, command, client, lastReply>>

Reject(key) ==
    /\ server[key].status = "pending"
    /\ LET c == command[key] IN
        \/ c.actor # Owner(c.task)
        \/ taskRound[c.task] # NoRound
        \/ c.revision # revision[c.task]
    /\ server' = [server EXCEPT ![key].status = "rejected"]
    /\ UNCHANGED <<revision, taskRound, rounds, jobs, command, client, lastReply>>

LoseResponse(key) ==
    /\ client[key] = "pending"
    /\ client' = [client EXCEPT ![key] = "unknown"]
    /\ UNCHANGED <<revision, taskRound, rounds, jobs, command, server, lastReply>>

Deliver(key) ==
    /\ client[key] = "pending"
    /\ server[key].status \in {"accepted", "rejected"}
    /\ client' = [client EXCEPT ![key] = server[key].status]
    /\ lastReply' = Reply(server[key].status, command[key].task, key,
                          command[key].actor, server[key].round)
    /\ UNCHANGED <<revision, taskRound, rounds, jobs, command, server>>

ReadBack(key, readable) ==
    /\ client[key] = "unknown"
    /\ IF readable /\ server[key].status = "accepted"
       THEN /\ client' = [client EXCEPT ![key] = "accepted"]
            /\ lastReply' = Reply("accepted", command[key].task, key,
                                  command[key].actor, server[key].round)
       ELSE /\ UNCHANGED client
            /\ lastReply' = Reply("unknown", command[key].task, key,
                                  command[key].actor, NoRound)
    /\ UNCHANGED <<revision, taskRound, rounds, jobs, command, server>>

\* Same-key replay may name a different Task (a tenant-global key collision).
\* Correct lookup checks source_draft_id as well as key, current object owner,
\* and captured revision; it must not return a different Task's successful Round.
Replay(key, task, actor, expectedRevision) ==
    /\ ForKey(key) # {}
    /\ LET r == RoundFor(key)
           authorized == actor = Owner(task)
           bound == /\ rounds[r].task = task
                    /\ rounds[r].requestedRevision = expectedRevision
           accepted == authorized /\ (bound \/ Fault = "cross_task_key")
       IN lastReply' = Reply(
            IF accepted THEN "accepted" ELSE "conflict", task, key, actor,
            IF accepted THEN r ELSE NoRound)
    /\ UNCHANGED <<revision, taskRound, rounds, jobs, command, server, client>>

Next ==
    \/ \E key \in Keys, task \in Tasks, actor \in Actors: Invoke(key, task, actor)
    \/ \E task \in Tasks: Rematch(task)
    \/ \E key \in Keys:
        (\E round \in RoundIds: Commit(key, round)) \/ Reject(key)
        \/ LoseResponse(key) \/ Deliver(key)
        \/ (\E readable \in BOOLEAN: ReadBack(key, readable))
        \/ (\E task \in Tasks, actor \in Actors, rev \in Revisions:
                Replay(key, task, actor, rev))
Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ revision \in [Tasks -> Revisions]
    /\ taskRound \in [Tasks -> RoundIds \cup {NoRound}]
    /\ rounds \in [RoundIds -> [exists: BOOLEAN, task: Tasks \cup {NoTask},
        key: Keys \cup {"none"}, actor: Actors \cup {"none"},
        requestedRevision: Revisions \cup {NoRevision},
        frozenRevision: Revisions \cup {NoRevision}, inputs: AllInputs \cup {{}}]]
    /\ jobs \subseteq RoundIds
    /\ command \in [Keys -> [task: Tasks \cup {NoTask},
        actor: Actors \cup {"none"}, revision: Revisions \cup {NoRevision}]]
    /\ server \in [Keys -> [status: {"unused", "pending", "accepted", "rejected"},
        round: RoundIds \cup {NoRound}]]
    /\ client \in [Keys -> {"unused", "pending", "unknown", "accepted", "rejected"}]
    /\ lastReply \in [status: {"none", "accepted", "rejected", "unknown", "conflict"},
        task: Tasks \cup {NoTask}, key: Keys \cup {"none"},
        actor: Actors \cup {"none"}, round: RoundIds \cup {NoRound}]

OneRoundPerTask == \A task \in Tasks: Cardinality(ForTask(task)) <= 1
OneRoundPerKey == \A key \in Keys: Cardinality(ForKey(key)) <= 1
AtomicRoundAndJob ==
    /\ jobs = ExistingRounds
    /\ \A task \in Tasks:
        ForTask(task) = IF taskRound[task] = NoRound THEN {} ELSE {taskRound[task]}
FrozenMatchedInputs == \A r \in ExistingRounds:
    rounds[r].inputs = InputsAt(rounds[r].task, rounds[r].frozenRevision)
FrozenRequestedRevision == \A r \in ExistingRounds:
    rounds[r].frozenRevision = rounds[r].requestedRevision
OwnerOnlyAcceptance == \A r \in ExistingRounds:
    rounds[r].actor = Owner(rounds[r].task)
ReplyBoundToTask == lastReply.status = "accepted" =>
    /\ lastReply.round \in ExistingRounds
    /\ rounds[lastReply.round].task = lastReply.task
    /\ rounds[lastReply.round].key = lastReply.key
    /\ lastReply.actor = Owner(lastReply.task)
    /\ taskRound[lastReply.task] = lastReply.round
ReceiptTruth == \A key \in Keys:
    /\ (server[key].status = "accepted" =>
        /\ server[key].round \in ExistingRounds
        /\ rounds[server[key].round].key = key
        /\ rounds[server[key].round].task = command[key].task
        /\ rounds[server[key].round].requestedRevision = command[key].revision)
    /\ (client[key] = "accepted" => server[key].status = "accepted")
UnknownHasNoRound == lastReply.status = "unknown" => lastReply.round = NoRound
AcceptedInputLocked == [][\A task \in Tasks:
    taskRound[task] # NoRound => revision'[task] = revision[task]]_vars
RoundFactsStayFrozen == [][\A r \in ExistingRounds: rounds'[r] = rounds[r]]_vars
=============================================================================
