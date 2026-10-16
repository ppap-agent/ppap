--------------------------- MODULE TaskRecognition ---------------------------
EXTENDS Naturals, Sequences, FiniteSets

\* Requirements model for #165, #164 and #163 D2; not an implementation
\* refinement. StartRecognition is one atomic Task/input/job acceptance.
\* A request ID also names its unique Task in this abstraction. A job pair
\* <<request, file-version>> denotes the persisted, immutable per-file job ID.
\* File-version IDs stand for immutable bytes; rule IDs stand for full frozen
\* candidate snapshots. The two published candidate revisions can differ
\* between Tasks which select the same version. Request order is significant.
\*
\* The environment may withhold recognition forever. A leased job can become
\* unknown; only its original completion can later settle it. Worker ownership
\* and attempt fencing are separate obligations in FileValidationReceipt.
\* RefreshTask publishes the settled bound results. It can be delayed. Partial
\* recognition display is omitted: it does not authorize pending_confirm/End.
\* There is no fairness, liveness, timing, authorization, parser, model-quality,
\* Task-number allocation, formal confirmation or paid-review claim here.
\* Unselected and precheck-removed versions are provenance, never job inputs.
\* The model fixes the first Task input; rematching/revision is outside scope.
\* No constraints or symmetry remove interleavings. Stuttering permits a
\* queued/leased/unknown job or an unconfirmed Task to remain so indefinitely.

CONSTANT Fault
ASSUME Fault \in {"none", "latest", "failed_as_zero"}

Requests == {1, 2}
\* 1/2 can be selected or left unselected; 3 is valid but removed, and 4 is
\* invalid. Removal and validation therefore impose separate eligibility gates.
Versions == {1, 2, 3, 4}
ValidVersions == {1, 2, 3}
RemovedVersions == {3}
Rules == {1, 2}
Jobs == Requests \X Versions
NoJob == <<0, 0>>
Selections == {<<1>>, <<2>>, <<1, 2>>, <<2, 1>>, <<3>>, <<4>>}
Selected(s) == {s[i] : i \in 1..Len(s)}
Eligible(s) == /\ Selected(s) \subseteq ValidVersions
               /\ Selected(s) \cap RemovedVersions = {}
BoundJob(t, v) == <<t, v>>
JobStates == {"absent", "queued", "leased", "unknown",
              "matched", "unmatched", "failed"}
Terminal == {"matched", "unmatched", "failed"}
NoResult == [job |-> NoJob, state |-> "absent", rule |-> 0]
NoInput == [selected |-> <<>>, candidates |-> 0,
            unselected |-> {}, removed |-> {}]
NoReceipt == [request |-> 0, selected |-> <<>>, task |-> 0, status |-> "none"]

VARIABLES taskState, taskInput, jobState, jobCandidates, taskResults,
          latestResult, currentRule, receipt, effects
vars == <<taskState, taskInput, jobState, jobCandidates, taskResults,
          latestResult, currentRule, receipt, effects>>

Init ==
    /\ taskState = [t \in Requests |-> "absent"]
    /\ taskInput = [t \in Requests |-> NoInput]
    /\ jobState = [j \in Jobs |-> "absent"]
    /\ jobCandidates = [j \in Jobs |-> 0]
    /\ taskResults = [t \in Requests |-> [v \in Versions |-> NoResult]]
    /\ latestResult = [v \in Versions |-> NoJob]
    /\ currentRule = 1
    /\ receipt = NoReceipt
    /\ effects = [t \in Requests |-> {}]

StartRecognition(t, s) ==
    /\ taskState[t] = "absent"
    /\ Eligible(s)
    /\ taskState' = [taskState EXCEPT ![t] = "matching"]
    /\ taskInput' = [taskInput EXCEPT ![t] =
        [selected |-> s, candidates |-> currentRule,
         unselected |-> ValidVersions \ (Selected(s) \cup RemovedVersions),
         removed |-> RemovedVersions]]
    /\ jobState' = [j \in Jobs |->
        IF j[1] = t /\ j[2] \in Selected(s) THEN "queued" ELSE jobState[j]]
    /\ jobCandidates' = [j \in Jobs |->
        IF j[1] = t /\ j[2] \in Selected(s) THEN currentRule
        ELSE jobCandidates[j]]
    /\ taskResults' = [taskResults EXCEPT ![t] = [v \in Versions |->
        IF v \in Selected(s)
        THEN [job |-> BoundJob(t, v), state |-> "queued", rule |-> 0]
        ELSE NoResult]]
    /\ receipt' = [request |-> t, selected |-> s, task |-> t, status |-> "accepted"]
    /\ UNCHANGED <<latestResult, currentRule, effects>>

Replay(t, s) ==
    /\ taskState[t] # "absent"
    /\ receipt' = [request |-> t, selected |-> s, task |-> t,
        status |-> IF s = taskInput[t].selected THEN "coalesced" ELSE "conflict"]
    /\ UNCHANGED <<taskState, taskInput, jobState, jobCandidates, taskResults,
                   latestResult, currentRule, effects>>

RejectIneligible(t, s) ==
    /\ taskState[t] = "absent"
    /\ ~Eligible(s)
    /\ receipt' = [request |-> t, selected |-> s, task |-> 0, status |-> "invalid"]
    /\ UNCHANGED <<taskState, taskInput, jobState, jobCandidates, taskResults,
                   latestResult, currentRule, effects>>

PublishRules ==
    /\ currentRule = 1
    /\ currentRule' = 2
    /\ UNCHANGED <<taskState, taskInput, jobState, jobCandidates, taskResults,
                   latestResult, receipt, effects>>

Claim(j) ==
    /\ jobState[j] = "queued"
    /\ jobState' = [jobState EXCEPT ![j] = "leased"]
    /\ UNCHANGED <<taskState, taskInput, jobCandidates, taskResults,
                   latestResult, currentRule, receipt, effects>>

LoseLease(j) ==
    /\ jobState[j] = "leased"
    /\ jobState' = [jobState EXCEPT ![j] = "unknown"]
    /\ UNCHANGED <<taskState, taskInput, jobCandidates, taskResults,
                   latestResult, currentRule, receipt, effects>>

Complete(j, outcome) ==
    /\ jobState[j] \in {"leased", "unknown"}
    /\ jobState' = [jobState EXCEPT ![j] = outcome]
    /\ latestResult' = [latestResult EXCEPT ![j[2]] = j]
    /\ UNCHANGED <<taskState, taskInput, jobCandidates, taskResults,
                   currentRule, receipt, effects>>

ReadJob(t, v) ==
    IF Fault = "latest" /\ latestResult[v] # NoJob
    THEN latestResult[v] ELSE BoundJob(t, v)
ReadResult(t, v) ==
    LET j == ReadJob(t, v) IN
    [job |-> j, state |-> jobState[j],
     rule |-> IF jobState[j] = "matched" THEN jobCandidates[j] ELSE 0]
AllJobsTerminal(t) ==
    \A v \in Selected(taskInput[t].selected): jobState[BoundJob(t, v)] \in Terminal

RefreshTask(t) ==
    /\ taskState[t] \in {"matching", "pending_confirm"}
    /\ AllJobsTerminal(t)
    /\ taskResults' = [taskResults EXCEPT ![t] = [v \in Versions |->
        IF v \in Selected(taskInput[t].selected) THEN ReadResult(t, v)
        ELSE NoResult]]
    /\ taskState' = [taskState EXCEPT ![t] = "pending_confirm"]
    /\ UNCHANGED <<taskInput, jobState, jobCandidates, latestResult,
                   currentRule, receipt, effects>>

DefinitivelyUnmatched(t) ==
    \A v \in Selected(taskInput[t].selected):
        /\ jobState[BoundJob(t, v)] = "unmatched"
        /\ taskResults[t][v].state = "unmatched"
        /\ taskResults[t][v].job = BoundJob(t, v)

EndTask(t) ==
    /\ taskState[t] = "pending_confirm"
    /\ IF Fault = "failed_as_zero"
       THEN \A v \in Selected(taskInput[t].selected):
                taskResults[t][v].state \in {"unmatched", "failed"}
       ELSE DefinitivelyUnmatched(t)
    /\ taskState' = [taskState EXCEPT ![t] = "completed"]
    /\ UNCHANGED <<taskInput, jobState, jobCandidates, taskResults,
                   latestResult, currentRule, receipt, effects>>

Next ==
    \/ \E t \in Requests, s \in Selections:
          StartRecognition(t, s) \/ Replay(t, s) \/ RejectIneligible(t, s)
    \/ PublishRules
    \/ \E j \in Jobs: Claim(j) \/ LoseLease(j) \/ (\E o \in Terminal: Complete(j, o))
    \/ \E t \in Requests: RefreshTask(t) \/ EndTask(t)
Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ taskState \in [Requests -> {"absent", "matching", "pending_confirm", "completed"}]
    /\ taskInput \in [Requests ->
        [selected: Selections \cup {<<>>}, candidates: Rules \cup {0},
         unselected: SUBSET Versions, removed: SUBSET Versions]]
    /\ jobState \in [Jobs -> JobStates]
    /\ jobCandidates \in [Jobs -> Rules \cup {0}]
    /\ taskResults \in [Requests -> [Versions ->
        [job: Jobs \cup {NoJob}, state: JobStates, rule: Rules \cup {0}]]]
    /\ latestResult \in [Versions -> Jobs \cup {NoJob}]
    /\ currentRule \in Rules
    /\ receipt \in [request: Requests \cup {0}, selected: Selections \cup {<<>>},
                    task: Requests \cup {0},
                    status: {"none", "accepted", "coalesced", "conflict", "invalid"}]
    /\ effects \in [Requests -> SUBSET {"round", "fee", "report"}]

AtomicSelection ==
    \A t \in Requests:
        /\ (taskState[t] # "absent" =>
            /\ Eligible(taskInput[t].selected)
            /\ taskInput[t].unselected =
                ValidVersions \ (Selected(taskInput[t].selected) \cup RemovedVersions)
            /\ taskInput[t].removed = RemovedVersions
            /\ taskInput[t].candidates \in Rules)
        /\ \A v \in Versions:
            /\ (jobState[BoundJob(t, v)] # "absent") =
                (taskState[t] # "absent" /\ v \in Selected(taskInput[t].selected))
            /\ (jobState[BoundJob(t, v)] # "absent" =>
                 jobCandidates[BoundJob(t, v)] = taskInput[t].candidates)

ExactJobResult ==
    \A t \in Requests: \A v \in Selected(taskInput[t].selected):
        taskResults[t][v].job = BoundJob(t, v)
FrozenCandidateResult ==
    \A t \in Requests: \A v \in Selected(taskInput[t].selected):
        taskResults[t][v].state = "matched" =>
            taskResults[t][v].rule = taskInput[t].candidates
PendingRequiresSettledJobs ==
    \A t \in Requests:
        taskState[t] \in {"pending_confirm", "completed"} => AllJobsTerminal(t)
EndedOnlyDefinitiveZero ==
    \A t \in Requests: taskState[t] = "completed" => DefinitivelyUnmatched(t)
NoReviewEffects == \A t \in Requests: effects[t] = {}
ReceiptTruth ==
    /\ (receipt.status \in {"accepted", "coalesced", "conflict"} =>
        /\ receipt.task = receipt.request
        /\ taskState[receipt.request] # "absent"
        /\ (receipt.status = "conflict") =
            (receipt.selected # taskInput[receipt.request].selected))
    /\ (receipt.status = "invalid" =>
        /\ receipt.task = 0
        /\ ~Eligible(receipt.selected))

\* Action Safety properties retain the first accepted input without a shadow
\* business ledger. The next-state guard excludes the initial creation step.
InputsStayFrozen == [][
    /\ \A t \in Requests:
        taskState[t] # "absent" => taskInput'[t] = taskInput[t]
    /\ \A j \in Jobs:
        jobState[j] # "absent" => jobCandidates'[j] = jobCandidates[j]]_vars
=============================================================================
