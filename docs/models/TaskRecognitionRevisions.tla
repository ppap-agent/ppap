---------------------- MODULE TaskRecognitionRevisions ----------------------
EXTENDS Integers, Sequences, FiniteSets

\* Focused requirements obligation for #165 input revisions and recognition
\* response loss, not a product implementation or cross-model refinement proof.
\* One existing private Task starts at revision 0. Its original request ID is
\* the Task ID; the only explicit rematch has a fresh request ID and revision 1.
\* Two immutable file versions expose both a kept file and a removed file.
\* Rule IDs denote complete frozen candidate snapshots, not live rule lookups.
\*
\* Projection from TaskRecognition: one accepted Task and its original bound
\* jobs become revision 0 here. Original job/request payloads stay immutable;
\* only this model's current Task input and job references can change. The
\* one-shot model's InputsStayFrozen applies outside this revision extension.
\* Cross-Task isolation remains in TaskRecognition; it is not rechecked here.
\*
\* Acceptance is one row-lock/CAS transaction: validate original precheck
\* receipts, freeze candidates, create jobs, and replace the current input and
\* references. Only the rematch receipt lives in the first selected file's
\* job, whose ID equals request_id. The initial receipt is the draft's immutable
\* audit_drafts.task_fingerprint; initial file-analysis jobs have opaque IDs.
\* InitialRequest bundles that draft receipt and its first job in one abstract
\* symbol. Its jobPayload fingerprint/acceptedRevision are projected draft
\* facts, not stored initial-job fields. ReadReceipt/Replay abstract the two
\* source-specific lookups; no actual initial job ID equals Task ID assumption
\* is required. Physical receipt lookup/key selection is outside this check.
\* The stale-job and stale-End witnesses retain distinct jobs and revisions;
\* bundling the initial receipt does not remove either fault boundary. There
\* is no new receipt table or business ledger in this abstraction.
\* jobPayload[RematchRequest] projects recognition_request{request_id=request,
\* input_revision=acceptedRevision,fingerprint}; task.revision/request project
\* the current input_revision/recognition_request_id. refs and jobState project
\* the current per-file results. Reply.currentRevision records the response
\* Task's input revision; acceptedRevision still names the original receipt.
\* DTO field completeness, reply transit delay and UI read epochs are outside
\* this model; no independent cached response result set is modeled.
\*
\* Normal delivery, lost response, accepted readback, and unknown readback are
\* separate actions. Unknown is not proof of absence. Replay is an explicit
\* same-request call; there is no automatic retry or fresh request on unknown.
\* A started old job may finish its own job. Old queued work can be skipped.
\* End captures one revision's zero-match intent and must CAS that revision.
\* input_revision counts recognition input generations. Production End may
\* atomically save selected+unmatched -> removed terminal dispositions through
\* its optional removals DTO, without a new revision or new recognition work.
\* This model projects away that DTO and those terminal disposition writes:
\* task.input/refs retain the accepted generation's recognition basis, so
\* CurrentJobRefs is not a claim about the displayed terminal selection.
\* End checks zero-match authority and the revision CAS only. Completed Tasks
\* cannot rematch, as enforced by InvokeRematch/AcceptRematch state guards.
\*
\* No fairness, liveness, timing, authorization, parser/model quality, crash
\* recovery, receipt-retention, attempt-fencing, confirmation, fee or report
\* claim. The environment may never accept/respond/finish. Original receipt
\* truth and the database's atomic transaction boundary are assumptions; this
\* model checks their use. No constraints, symmetry or overrides are used.
\* The fixed valid input change removes version 2 and changes mode/customer;
\* a third input has a wrong original receipt. Fingerprints are exact equality
\* of these canonical inputs; hash collisions and arbitrary input sets are not
\* modeled. One fresh rematch does not cover two competing fresh rematches or
\* unbounded revision histories. End/rematch concurrency is checked explicitly.
\* One captured End intent suffices for the stale-CAS witness; a new End intent
\* after conflict is outside the checked scope.
\* CHECK_DEADLOCK FALSE treats waiting/terminal states as allowed quiescence;
\* stuttering permits external non-response forever.

CONSTANT Fault
ASSUME Fault \in {"none", "stale_job_commit", "stale_end"}

Versions == {1, 2}
Revisions == {0, 1}
Rules == {1, 2}
InitialRequest == "task-id"
RematchRequest == "rematch-id"
Requests == {InitialRequest, RematchRequest}
\* InitialRequest here denotes the opaque first initial job, not its real ID.
Jobs == Requests \cup {"initial-other-job"}
NoJob == "no-job"
NoRevision == -1
OriginalReceipts == [v \in Versions |-> v]
InitialInput == [selected |-> <<1, 2>>, unselected |-> {}, removed |-> {},
                 receipts |-> OriginalReceipts,
                 provenance |-> [v \in Versions |->
                     [source |-> v, removalReason |-> ""]],
                 mode |-> "standard", customer |-> "first"]
RevisedInput == [selected |-> <<1>>, unselected |-> {}, removed |-> {2},
                 receipts |-> OriginalReceipts,
                 provenance |-> [v \in Versions |->
                     [source |-> v,
                      removalReason |-> IF v = 2 THEN "user_removed" ELSE ""]],
                 mode |-> "deep", customer |-> "second"]
InvalidInput == [RevisedInput EXCEPT !.receipts[1] = 0]
Inputs == {InitialInput, RevisedInput, InvalidInput}
Selected(input) == {input.selected[i] : i \in 1..Len(input.selected)}
Eligible(input) ==
    /\ input.receipts = OriginalReceipts
    /\ Selected(input) # {}
    /\ Selected(input) \cap input.removed = {}
    /\ Selected(input) \cup input.unselected \cup input.removed = Versions
InputAt(rev) == IF rev = 0 THEN InitialInput ELSE RevisedInput
RequestAt(rev) == IF rev = 0 THEN InitialRequest ELSE RematchRequest
JobAt(rev, version) ==
    IF version = 1 THEN RequestAt(rev) ELSE "initial-other-job"
JobVersion(job) == IF job = "initial-other-job" THEN 2 ELSE 1
JobRevision(job) == IF job = RematchRequest THEN 1 ELSE 0
BoundRefs(rev) == [v \in Versions |->
    IF v \in Selected(InputAt(rev)) THEN JobAt(rev, v) ELSE NoJob]
NoPayload == [fingerprint |-> InvalidInput, acceptedRevision |-> NoRevision,
              taskRevision |-> NoRevision, candidates |-> 0]
Payload(rev, input, rules) ==
    [fingerprint |-> input, acceptedRevision |-> rev,
     taskRevision |-> rev, candidates |-> rules]
JobStates == {"absent", "queued", "running", "unknown", "obsolete",
              "matched", "unmatched", "failed"}
Terminal == {"matched", "unmatched", "failed"}
Reply(status, request, input, accepted, current) ==
    [status |-> status, request |-> request, fingerprint |-> input,
     acceptedRevision |-> accepted, currentRevision |-> current]

VARIABLES task, refs, jobState, jobPayload, currentRule, rematchInput,
          clientPhase, reply, endIntent, endedIntent
vars == <<task, refs, jobState, jobPayload, currentRule, rematchInput,
          clientPhase, reply, endIntent, endedIntent>>

Init ==
    /\ task = [revision |-> 0, input |-> InitialInput,
                request |-> InitialRequest, candidates |-> 1, state |-> "matching"]
    /\ refs = BoundRefs(0)
    /\ jobState = [j \in Jobs |-> IF j = RematchRequest THEN "absent" ELSE "queued"]
    /\ jobPayload = [j \in Jobs |->
        IF j = RematchRequest THEN NoPayload ELSE Payload(0, InitialInput, 1)]
    /\ currentRule = 1
    /\ rematchInput = RevisedInput
    /\ clientPhase = [r \in Requests |->
        IF r = InitialRequest THEN "pending" ELSE "unused"]
    /\ reply = Reply("none", InitialRequest, InitialInput, NoRevision, NoRevision)
    /\ endIntent = NoRevision
    /\ endedIntent = NoRevision

InvokeRematch(input) ==
    /\ clientPhase[RematchRequest] = "unused"
    /\ clientPhase[InitialRequest] = "accepted"
    /\ task.state \in {"matching", "pending_confirm"}
    /\ rematchInput' = input
    /\ clientPhase' = [clientPhase EXCEPT ![RematchRequest] = "pending"]
    /\ UNCHANGED <<task, refs, jobState, jobPayload, currentRule, reply,
                   endIntent, endedIntent>>

AcceptRematch ==
    /\ clientPhase[RematchRequest] \in {"pending", "unknown"}
    /\ jobState[RematchRequest] = "absent"
    /\ task.state \in {"matching", "pending_confirm"}
    /\ task.revision = 0 \* expected_revision from the revision-0 invocation
    /\ Eligible(rematchInput)
    /\ task' = [revision |-> 1, input |-> rematchInput,
                request |-> RematchRequest, candidates |-> currentRule,
                state |-> "matching"]
    /\ refs' = BoundRefs(1)
    /\ jobState' = [jobState EXCEPT ![RematchRequest] = "queued"]
    /\ jobPayload' = [jobPayload EXCEPT ![RematchRequest] =
        Payload(1, rematchInput, currentRule)]
    /\ UNCHANGED <<currentRule, rematchInput, clientPhase, reply,
                   endIntent, endedIntent>>

RejectInvalidRematch ==
    /\ clientPhase[RematchRequest] \in {"pending", "unknown"}
    /\ jobState[RematchRequest] = "absent"
    /\ ~Eligible(rematchInput)
    /\ clientPhase' = [clientPhase EXCEPT ![RematchRequest] = "rejected"]
    /\ reply' = Reply("invalid", RematchRequest, rematchInput,
                       NoRevision, task.revision)
    /\ UNCHANGED <<task, refs, jobState, jobPayload, currentRule, rematchInput,
                   endIntent, endedIntent>>

LoseResponse(request) ==
    /\ clientPhase[request] = "pending"
    /\ clientPhase' = [clientPhase EXCEPT ![request] = "unknown"]
    /\ UNCHANGED <<task, refs, jobState, jobPayload, currentRule, rematchInput,
                   reply, endIntent, endedIntent>>

DeliverAccepted(request) ==
    /\ clientPhase[request] = "pending"
    /\ jobState[request] # "absent"
    /\ clientPhase' = [clientPhase EXCEPT ![request] = "accepted"]
    /\ reply' = Reply("accepted", request, jobPayload[request].fingerprint,
                       jobPayload[request].acceptedRevision, task.revision)
    /\ UNCHANGED <<task, refs, jobState, jobPayload, currentRule, rematchInput,
                   endIntent, endedIntent>>

ReadReceipt(request, readable) ==
    /\ clientPhase[request] \in {"pending", "unknown"}
    /\ IF readable /\ jobState[request] # "absent"
       THEN /\ clientPhase' = [clientPhase EXCEPT ![request] = "accepted"]
            /\ reply' = Reply("accepted", request,
                 jobPayload[request].fingerprint,
                 jobPayload[request].acceptedRevision, task.revision)
       ELSE /\ UNCHANGED clientPhase
            /\ reply' = Reply("unknown", request,
                 IF request = InitialRequest THEN InitialInput ELSE rematchInput,
                 NoRevision, NoRevision)
    /\ UNCHANGED <<task, refs, jobState, jobPayload, currentRule, rematchInput,
                   endIntent, endedIntent>>

\* Exact canonical input equality represents the request fingerprint. It
\* includes all file/provenance/receipt rows, selection, mode, and customer.
\* In particular replaying revision 0 after revision 1 compares its ORIGINAL
\* receipt, not the mutable Task input. Both replies return the current Task.
Replay(request, input) ==
    /\ jobState[request] # "absent"
    /\ reply' = Reply(
        IF input = jobPayload[request].fingerprint THEN "accepted" ELSE "conflict",
        request, input, jobPayload[request].acceptedRevision, task.revision)
    /\ UNCHANGED <<task, refs, jobState, jobPayload, currentRule, rematchInput,
                   clientPhase, endIntent, endedIntent>>

PublishRules ==
    /\ currentRule = 1
    /\ currentRule' = 2
    /\ UNCHANGED <<task, refs, jobState, jobPayload, rematchInput, clientPhase,
                   reply, endIntent, endedIntent>>

Claim(job) ==
    /\ jobState[job] = "queued"
    /\ refs[JobVersion(job)] = job
    /\ jobState' = [jobState EXCEPT ![job] = "running"]
    /\ UNCHANGED <<task, refs, jobPayload, currentRule, rematchInput,
                   clientPhase, reply, endIntent, endedIntent>>

SkipOldQueued(job) ==
    /\ jobState[job] = "queued"
    /\ refs[JobVersion(job)] # job
    /\ jobState' = [jobState EXCEPT ![job] = "obsolete"]
    /\ UNCHANGED <<task, refs, jobPayload, currentRule, rematchInput,
                   clientPhase, reply, endIntent, endedIntent>>

LoseJobResponse(job) ==
    /\ jobState[job] = "running"
    /\ jobState' = [jobState EXCEPT ![job] = "unknown"]
    /\ UNCHANGED <<task, refs, jobPayload, currentRule, rematchInput,
                   clientPhase, reply, endIntent, endedIntent>>

Complete(job, outcome) ==
    /\ jobState[job] \in {"running", "unknown"}
    /\ jobState' = [jobState EXCEPT ![job] = outcome]
    /\ refs' = IF Fault = "stale_job_commit" /\ JobRevision(job) # task.revision
               THEN [refs EXCEPT ![JobVersion(job)] = job] ELSE refs
    /\ UNCHANGED <<task, jobPayload, currentRule, rematchInput,
                   clientPhase, reply, endIntent, endedIntent>>

Settled == \A v \in Selected(task.input): jobState[refs[v]] \in Terminal
DefinitiveZero == \A v \in Selected(task.input): jobState[refs[v]] = "unmatched"
RefreshTask ==
    /\ task.state = "matching"
    /\ Settled
    /\ task' = [task EXCEPT !.state = "pending_confirm"]
    /\ UNCHANGED <<refs, jobState, jobPayload, currentRule, rematchInput,
                   clientPhase, reply, endIntent, endedIntent>>

CaptureEndIntent ==
    /\ task.state = "pending_confirm"
    /\ DefinitiveZero
    /\ endIntent = NoRevision
    /\ endIntent' = task.revision
    /\ UNCHANGED <<task, refs, jobState, jobPayload, currentRule, rematchInput,
                   clientPhase, reply, endedIntent>>

EndTask ==
    /\ task.state = "pending_confirm"
    /\ DefinitiveZero
    /\ endIntent # NoRevision
    /\ (Fault = "stale_end" \/ endIntent = task.revision)
    /\ task' = [task EXCEPT !.state = "completed"]
    /\ endedIntent' = endIntent
    /\ UNCHANGED <<refs, jobState, jobPayload, currentRule, rematchInput,
                   clientPhase, reply, endIntent>>

Next ==
    \/ \E input \in {RevisedInput, InvalidInput}: InvokeRematch(input)
    \/ AcceptRematch \/ RejectInvalidRematch \/ PublishRules
    \/ \E request \in Requests:
        LoseResponse(request) \/ DeliverAccepted(request)
        \/ (\E readable \in BOOLEAN: ReadReceipt(request, readable))
        \/ (\E input \in Inputs: Replay(request, input))
    \/ \E job \in Jobs:
        Claim(job) \/ SkipOldQueued(job) \/ LoseJobResponse(job)
        \/ (\E outcome \in Terminal: Complete(job, outcome))
    \/ RefreshTask \/ CaptureEndIntent \/ EndTask
Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ task \in [revision: Revisions, input: Inputs, request: Requests,
                 candidates: Rules, state: {"matching", "pending_confirm", "completed"}]
    /\ refs \in [Versions -> Jobs \cup {NoJob}]
    /\ jobState \in [Jobs -> JobStates]
    /\ jobPayload \in [Jobs -> [fingerprint: Inputs,
        acceptedRevision: Revisions \cup {NoRevision},
        taskRevision: Revisions \cup {NoRevision}, candidates: Rules \cup {0}]]
    /\ currentRule \in Rules
    /\ rematchInput \in {RevisedInput, InvalidInput}
    /\ clientPhase \in [Requests -> {"unused", "pending", "unknown", "accepted", "rejected"}]
    /\ reply \in [status: {"none", "accepted", "conflict", "invalid", "unknown"},
        request: Requests, fingerprint: Inputs,
        acceptedRevision: Revisions \cup {NoRevision},
        currentRevision: Revisions \cup {NoRevision}]
    /\ endIntent \in Revisions \cup {NoRevision}
    /\ endedIntent \in Revisions \cup {NoRevision}

CurrentJobRefs == refs = BoundRefs(task.revision)
AtomicCurrentInput ==
    /\ task.input = InputAt(task.revision)
    /\ task.request = RequestAt(task.revision)
    /\ Eligible(task.input)
    /\ \A v \in Selected(task.input):
        /\ jobState[refs[v]] # "absent"
        /\ jobPayload[refs[v]].taskRevision = task.revision
        /\ jobPayload[refs[v]].candidates = task.candidates
    /\ (task.revision = 0) = (jobState[RematchRequest] = "absent")
OriginalReceipt ==
    /\ jobPayload[InitialRequest] = Payload(0, InitialInput, 1)
    /\ \A request \in Requests:
        jobState[request] # "absent" =>
            /\ jobPayload[request].acceptedRevision = JobRevision(request)
            /\ jobPayload[request].fingerprint = InputAt(JobRevision(request))
ReceiptTruth ==
    /\ (reply.status \in {"accepted", "conflict"} =>
        /\ jobState[reply.request] # "absent"
        /\ reply.acceptedRevision = jobPayload[reply.request].acceptedRevision
        /\ (reply.status = "accepted") =
            (reply.fingerprint = jobPayload[reply.request].fingerprint)
        /\ reply.currentRevision <= task.revision)
    /\ (reply.status = "unknown" =>
        /\ reply.acceptedRevision = NoRevision
        /\ reply.currentRevision = NoRevision)
    /\ (reply.status = "invalid" => ~Eligible(reply.fingerprint))
PendingRequiresSettledJobs ==
    task.state \in {"pending_confirm", "completed"} => Settled
EndedOnlyDefinitiveZero == task.state = "completed" => DefinitiveZero
EndUsesCurrentRevision == task.state = "completed" => endedIntent = task.revision

\* Action properties keep immutable payloads without a shadow receipt ledger.
PayloadsStayFrozen == [][\A job \in Jobs:
    jobState[job] # "absent" => jobPayload'[job] = jobPayload[job]]_vars
ResponseReturnsCurrentTask == [][reply' # reply /\
    reply'.status \in {"accepted", "conflict"} =>
        reply'.currentRevision = task'.revision]_vars
=============================================================================
