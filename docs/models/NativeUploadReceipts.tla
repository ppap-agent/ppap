------------------------ MODULE NativeUploadReceipts ------------------------
EXTENDS Naturals, FiniteSets

\* #165 focused obligation: one local upload request, not full-system or
\* cross-model refinement. AuditUploadDrafts checks UI navigation separately;
\* its evidence is not inherited here. No production behavior is proved.
\*
\* The stable request names one FileVersion. A successful create transaction
\* binds caller and fixed content digest before any blob effect. The private
\* operation receipt stays caller-only. PUT retains the existing file ACL:
\* actor 2 may or may not have file-read authority in the two initial states;
\* an authorized reader may write the SAME bytes but cannot borrow the private
\* receipt or change its owner/digest. ACL changes/revocation are not modeled.
\* Hashes stand for two distinct byte strings without a hash collision. Other
\* create metadata are held fixed; content-address deduplication is not modeled.
\* The second version identity exists to expose reconstruction after unknown.
\*
\* Atomic DB allocation, PUT authorization, object write, and DB READY are
\* separate actions. Object writes may finish after a lost response or local
\* removal. Removal is UI intent, NOT a server delete/cancellation: an accepted
\* PUT may still make its original version READY, retaining its request owner.
\* Removal prevents new owner-client PUT attempts, not another authorized file
\* reader's direct PUT. UI selection/navigation/epochs,
\* object deletion and cleanup are outside this model.
\*
\* The two PUT slots permit a first request to remain in flight while explicit
\* resume starts another. Both normal effects write the same fixed bytes.
\* GET captures a snapshot separately from delivery, may be lost or delayed,
\* and has no server effects. A stale STAGING read can arrive after READY;
\* explicit same-ID/same-digest resume remains safe. Unknown alone permits
\* readback, not another create or PUT. No absence-to-retry inference is made.
\*
\* Receipt binding and object-store durability are assumptions. Positive checks
\* cover client recovery and guarded writes, not SQL locks, actual tenant/grant
\* checks, browser persistence, transfer streaming, file parsing, sizes, hash
\* collision resistance, or deployment/storage compatibility. There is no
\* fairness, liveness, crash/restart, latency, retry-budget or cleanup claim.
\* Stuttering permits a call or observation to remain unresolved forever.
\* CHECK_DEADLOCK FALSE accepts that open-environment quiescence. No state or
\* action constraints, symmetry reduction, or operator overrides are used.

CONSTANT Fault
ASSUME Fault \in {"none", "fresh_version", "overwrite_ready"}

Actors == {1, 2}
Digests == {1, 2}
Versions == {1, 2}
Attempts == {1, 2}
Owner == 1
Digest == 1
Request == "upload-request"
NoValue == 0
BoundReceipt == [request |-> Request, version |-> 1,
                 owner |-> Owner, digest |-> Digest]
NoReceipt == [request |-> Request, version |-> NoValue,
              owner |-> NoValue, digest |-> NoValue]
NoRead == [state |-> "none", version |-> NoValue, actor |-> NoValue]
NoAttempt == [state |-> "unused", actor |-> NoValue, digest |-> NoValue,
              version |-> NoValue, request |-> Request,
              origin |-> "none", removedAtStart |-> FALSE, lost |-> FALSE]
ClientStates == {"new", "creating", "unknown", "resumable", "putting", "ready"}
AttemptStates == {"unused", "rejected", "accepted", "effected", "committed"}
AcceptedStates == {"accepted", "effected", "committed"}

VARIABLES receipt, versions, status, blob, readyDigest, fileReaders,
          client, createPending, observation, pendingRead, attempts, removed
server == <<receipt, versions, status, blob, readyDigest, fileReaders>>
vars == <<receipt, versions, status, blob, readyDigest, fileReaders,
          client, createPending, observation, pendingRead, attempts, removed>>

Init ==
    /\ receipt = NoReceipt
    /\ versions = {}
    /\ status = [v \in Versions |-> "absent"]
    /\ blob = [v \in Versions |-> NoValue]
    /\ readyDigest = [v \in Versions |-> NoValue]
    /\ fileReaders \in {{Owner}, Actors}
    /\ client = "new"
    /\ createPending = FALSE
    /\ observation = NoRead
    /\ pendingRead = NoRead
    /\ attempts = [a \in Attempts |-> NoAttempt]
    /\ removed = FALSE

InvokeCreate ==
    /\ client = "new" /\ ~removed
    /\ client' = "creating" /\ createPending' = TRUE
    /\ UNCHANGED <<server, observation, pendingRead, attempts, removed>>

CommitCreate ==
    /\ createPending /\ receipt = NoReceipt
    /\ receipt' = BoundReceipt /\ versions' = {1}
    /\ status' = [status EXCEPT ![1] = "staging"]
    /\ UNCHANGED <<blob, readyDigest, fileReaders, client, createPending,
                   observation, pendingRead, attempts, removed>>

DeliverCreate ==
    /\ client = "creating" /\ receipt = BoundReceipt
    /\ client' = "resumable" /\ createPending' = FALSE
    /\ observation' = [state |-> status[1], version |-> 1, actor |-> Owner]
    /\ UNCHANGED <<server, pendingRead, attempts, removed>>

LoseCreate ==
    /\ client = "creating"
    /\ client' = "unknown"
    /\ UNCHANGED <<server, createPending, observation,
                   pendingRead, attempts, removed>>

CaptureRead(actor) ==
    /\ client \notin {"new", "creating"} /\ pendingRead = NoRead
    /\ pendingRead' = IF actor # Owner
                      THEN [state |-> "denied", version |-> NoValue, actor |-> actor]
                      ELSE IF receipt = NoReceipt
                           THEN [state |-> "unknown", version |-> NoValue, actor |-> actor]
                           ELSE [state |-> status[1], version |-> 1, actor |-> actor]
    /\ UNCHANGED <<server, client, createPending,
                   observation, attempts, removed>>

DeliverRead ==
    /\ pendingRead # NoRead
    /\ observation' = pendingRead /\ pendingRead' = NoRead
    /\ client' = CASE pendingRead.state = "staging" -> "resumable"
                  [] pendingRead.state = "ready" -> "ready"
                  [] OTHER -> "unknown"
    /\ UNCHANGED <<server, createPending, attempts, removed>>

LoseRead ==
    /\ pendingRead # NoRead
    /\ pendingRead' = NoRead
    /\ UNCHANGED <<server, client, createPending,
                   observation, attempts, removed>>

\* The owner-client path is explicit continuation, never an automatic retry.
\* Direct calls retain existing file ACL semantics independently of local UI.
TryPut(a, actor, digest, origin) ==
    /\ IF origin = "client"
       THEN /\ actor = Owner /\ client = "resumable" /\ ~removed
            /\ observation.state = "staging" /\ observation.version = 1
       ELSE actor # Owner
    /\ attempts[a].state = "unused" /\ receipt = BoundReceipt
    /\ LET allowed == actor \in fileReaders /\ digest = receipt.digest
       IN /\ attempts' = [attempts EXCEPT ![a] =
                 [state |-> IF allowed THEN "accepted" ELSE "rejected",
                  actor |-> actor, digest |-> digest, version |-> 1,
                  request |-> Request, origin |-> origin,
                  removedAtStart |-> removed, lost |-> FALSE]]
          /\ client' = IF allowed /\ origin = "client" THEN "putting" ELSE client
    /\ UNCHANGED <<server, createPending, observation, pendingRead, removed>>

WriteBlob(a) ==
    /\ attempts[a].state = "accepted"
    /\ LET v == attempts[a].version
           bytes == IF Fault = "overwrite_ready" /\ status[v] = "ready"
                    THEN 2 ELSE attempts[a].digest
       IN blob' = [blob EXCEPT ![v] = bytes]
    /\ attempts' = [attempts EXCEPT ![a].state = "effected"]
    /\ UNCHANGED <<receipt, versions, status, readyDigest, fileReaders, client,
                   createPending, observation, pendingRead, removed>>

CommitReady(a) ==
    /\ attempts[a].state = "effected"
    /\ LET v == attempts[a].version
       IN /\ blob[v] = receipt.digest
          /\ status' = [status EXCEPT ![v] = "ready"]
          /\ readyDigest' = [readyDigest EXCEPT ![v] = receipt.digest]
    /\ attempts' = [attempts EXCEPT ![a].state = "committed"]
    /\ UNCHANGED <<receipt, versions, blob, fileReaders, client, createPending,
                   observation, pendingRead, removed>>

LosePut(a) ==
    /\ attempts[a].state \in AcceptedStates /\ ~attempts[a].lost
    /\ attempts[a].origin = "client"
    /\ attempts' = [attempts EXCEPT ![a].lost = TRUE]
    /\ client' = "unknown"
    /\ UNCHANGED <<server, createPending, observation, pendingRead, removed>>

DeliverPut(a) ==
    /\ attempts[a].state = "committed" /\ ~attempts[a].lost
    /\ attempts[a].origin = "client"
    /\ client' = "ready"
    /\ observation' = [state |-> "ready", version |-> 1, actor |-> Owner]
    /\ UNCHANGED <<server, createPending, pendingRead, attempts, removed>>

RemoveLocal ==
    /\ ~removed /\ client # "new"
    /\ removed' = TRUE
    /\ UNCHANGED <<server, client, createPending, observation,
                   pendingRead, attempts>>

\* Fault witness: uncertainty about create is mistaken for permission to
\* allocate another version for this SAME logical request. Not a new upload.
RebuildOnUnknown ==
    /\ Fault = "fresh_version" /\ client = "unknown"
    /\ receipt = BoundReceipt /\ versions = {1} /\ ~removed
    /\ versions' = {1, 2}
    /\ status' = [status EXCEPT ![2] = "staging"]
    /\ UNCHANGED <<receipt, blob, readyDigest, fileReaders, client, createPending,
                   observation, pendingRead, attempts, removed>>

Next == InvokeCreate \/ CommitCreate \/ DeliverCreate \/ LoseCreate
        \/ (\E actor \in Actors: CaptureRead(actor))
        \/ DeliverRead \/ LoseRead \/ RemoveLocal
        \/ RebuildOnUnknown
        \/ (\E a \in Attempts, actor \in Actors, digest \in Digests,
               origin \in {"client", "direct"}: TryPut(a, actor, digest, origin))
        \/ (\E a \in Attempts: WriteBlob(a) \/ CommitReady(a)
                                  \/ LosePut(a) \/ DeliverPut(a))

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ receipt \in {NoReceipt, BoundReceipt} /\ versions \subseteq Versions
    /\ status \in [Versions -> {"absent", "staging", "ready"}]
    /\ blob \in [Versions -> Digests \cup {NoValue}]
    /\ readyDigest \in [Versions -> Digests \cup {NoValue}]
    /\ fileReaders \in {{Owner}, Actors}
    /\ client \in ClientStates /\ createPending \in BOOLEAN /\ removed \in BOOLEAN
    /\ observation \in [state: {"none", "unknown", "staging", "ready", "denied"},
                         version: Versions \cup {NoValue}, actor: Actors \cup {NoValue}]
    /\ pendingRead \in [state: {"none", "unknown", "staging", "ready", "denied"},
                         version: Versions \cup {NoValue}, actor: Actors \cup {NoValue}]
    /\ attempts \in [Attempts -> [state: AttemptStates, actor: Actors \cup {NoValue},
                      digest: Digests \cup {NoValue}, version: Versions \cup {NoValue},
                      request: {Request}, origin: {"none", "client", "direct"},
                      removedAtStart: BOOLEAN, lost: BOOLEAN]]

OneRequestOneVersion == Cardinality(versions) <= 1
AcceptedInputsBound == \A a \in Attempts:
    attempts[a].state \in AcceptedStates =>
        /\ attempts[a].request = receipt.request
        /\ attempts[a].version = receipt.version
        /\ attempts[a].actor \in fileReaders
        /\ attempts[a].digest = receipt.digest
NoResumeAfterRemoval == \A a \in Attempts:
    attempts[a].state \in AcceptedStates /\ attempts[a].origin = "client" =>
        ~attempts[a].removedAtStart /\ attempts[a].actor = Owner
PrivateReceipt == \A reply \in {observation, pendingRead}:
    reply.state \in {"staging", "ready"} => reply.actor = receipt.owner
BlobMatchesReceipt == \A v \in Versions:
    blob[v] # NoValue => v = receipt.version /\ blob[v] = receipt.digest
ReadyBytesImmutable == \A v \in Versions:
    readyDigest[v] # NoValue =>
        readyDigest[v] = Digest /\ blob[v] = readyDigest[v]
ReadyHasBytes == \A v \in Versions:
    status[v] = "ready" => readyDigest[v] = receipt.digest /\ blob[v] = receipt.digest
EffectsKeepResponsibility == \A a \in Attempts:
    attempts[a].state \in {"effected", "committed"} =>
        /\ attempts[a].request = Request
        /\ attempts[a].version \in versions
        /\ attempts[a].version = receipt.version
        /\ receipt.owner = Owner
=============================================================================
