------------------------- MODULE FeishuImportReceipts -------------------------
EXTENDS Naturals, FiniteSets

\* #165 focused Safety obligation, not a refinement or production proof.
\* One private request, two competing handlers, two source tokens and two byte
\* digests. A source is bound before provider I/O. The first captured bytes bind
\* the immutable version; a changed remote document cannot replace them.
\* Reserve/capture, provider completion, object effect and READY are distinct.
\* A handler can stop or lose its DB session at any cut point. Its provider reply
\* and an already submitted object effect may still finish after lock loss.
\* Capture requires the original session. Explicit continuation acquires the
\* same session lock; a missing response alone does not create another request.
\* Digests abstract collision-free byte strings. SQL/session lock correctness,
\* ACL enforcement, provider identity/exports, streaming, cleanup, UI epochs and
\* liveness are outside this model. No fairness or deadline is assumed; calls
\* may remain unresolved. Deadlock checking is off for open-system quiescence.
\* No constraints, symmetry or operator overrides are used.

CONSTANT Fault
ASSUME Fault \in {"none", "source_rebind", "content_rebind", "lost_lock"}
Handlers == {1, 2}
Sources == {1, 2}
Digests == {1, 2}
Empty == [phase |-> "idle", source |-> 0, digest |-> 0, called |-> FALSE]

VARIABLES source, firstSource, digest, firstDigest, blob, ready, failed,
          holder, handlers, writes
vars == <<source, firstSource, digest, firstDigest, blob, ready, failed,
          holder, handlers, writes>>

Init ==
    /\ source = 0 /\ firstSource = 0 /\ digest = 0 /\ firstDigest = 0
    /\ blob = 0 /\ ready = FALSE /\ failed = FALSE /\ holder = 0
    /\ handlers = [h \in Handlers |-> Empty]
    /\ writes = [h \in Handlers |-> 0]

Start(h, s) ==
    /\ handlers[h].phase = "idle" /\ ~failed
    /\ LET allowed == source = 0 \/ source = s \/ Fault = "source_rebind"
       IN /\ source' = IF allowed THEN s ELSE source
          /\ firstSource' = IF firstSource = 0 THEN s ELSE firstSource
          /\ handlers' = [handlers EXCEPT ![h] =
                [phase |-> IF allowed THEN "bound" ELSE "done",
                 source |-> s, digest |-> 0, called |-> FALSE]]
    /\ UNCHANGED <<digest, firstDigest, blob, ready, failed, holder, writes>>

Acquire(h) ==
    /\ handlers[h].phase = "bound" /\ holder = 0 /\ ~failed
    /\ holder' = h
    /\ handlers' = [handlers EXCEPT ![h].phase = "locked"]
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, blob, ready, failed, writes>>

Fetch(h) ==
    /\ holder = h /\ handlers[h].phase = "locked" /\ ~ready
    /\ ~(digest # 0 /\ blob = digest)
    /\ handlers' = [handlers EXCEPT ![h].phase = "fetching", ![h].called = TRUE]
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, blob, ready,
                   failed, holder, writes>>

Deliver(h, d) ==
    /\ handlers[h].phase = "fetching"
    /\ handlers' = [handlers EXCEPT ![h].phase = "loaded", ![h].digest = d]
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, blob, ready,
                   failed, holder, writes>>

Capture(h) ==
    /\ handlers[h].phase = "loaded" /\ ~ready /\ ~failed
    /\ holder = h \/ (Fault = "lost_lock" /\ holder # h /\ firstDigest # 0)
    /\ LET d == handlers[h].digest
           allowed == digest = 0 \/ digest = d \/ Fault = "content_rebind" \/ Fault = "lost_lock"
       IN /\ digest' = IF allowed THEN d ELSE digest
          /\ firstDigest' = IF firstDigest = 0 THEN d ELSE firstDigest
          /\ failed' = ~allowed
          /\ handlers' = [handlers EXCEPT ![h].phase = IF allowed THEN "captured" ELSE "done"]
          /\ holder' = IF allowed THEN holder ELSE 0
    /\ UNCHANGED <<source, firstSource, blob, ready, writes>>

SubmitWrite(h) ==
    /\ holder = h /\ handlers[h].phase = "captured"
    /\ writes[h] = 0
    /\ writes' = [writes EXCEPT ![h] = handlers[h].digest]
    /\ handlers' = [handlers EXCEPT ![h].phase = "writing"]
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, blob,
                   ready, failed, holder>>

ObjectEffect(h) ==
    /\ writes[h] # 0
    /\ blob' = IF blob = 0 THEN writes[h] ELSE blob
    /\ writes' = [writes EXCEPT ![h] = 0]
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, ready, failed,
                   holder, handlers>>

Publish(h) ==
    /\ holder = h /\ digest # 0 /\ blob = digest /\ ~failed
    /\ handlers[h].phase \in {"locked", "writing"}
    /\ ready' = TRUE /\ holder' = 0
    /\ handlers' = [handlers EXCEPT ![h].phase = "done"]
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, blob, failed, writes>>

Stop(h) ==
    /\ holder = h
    /\ holder' = 0
    /\ handlers' = [handlers EXCEPT ![h].phase = "done"]
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, blob,
                   ready, failed, writes>>

LoseSession(h) ==
    /\ holder = h
    /\ holder' = 0
    /\ UNCHANGED <<source, firstSource, digest, firstDigest, blob,
                   ready, failed, writes, handlers>>

Next == (\E h \in Handlers, s \in Sources: Start(h, s))
        \/ (\E h \in Handlers: Acquire(h) \/ Fetch(h) \/ Capture(h) \/ SubmitWrite(h)
                                    \/ ObjectEffect(h) \/ Publish(h) \/ Stop(h) \/ LoseSession(h))
        \/ (\E h \in Handlers, d \in Digests: Deliver(h, d))
Spec == Init /\ [][Next]_vars

TypeOK == /\ source \in Sources \cup {0} /\ firstSource \in Sources \cup {0}
          /\ digest \in Digests \cup {0} /\ firstDigest \in Digests \cup {0}
          /\ blob \in Digests \cup {0} /\ holder \in Handlers \cup {0}
          /\ ready \in BOOLEAN /\ failed \in BOOLEAN
          /\ writes \in [Handlers -> Digests \cup {0}]
          /\ handlers \in [Handlers ->
                 [phase: {"idle", "bound", "locked", "fetching", "loaded", "captured", "writing", "done"},
                  source: Sources \cup {0}, digest: Digests \cup {0}, called: BOOLEAN]]
SourceBound == source = firstSource
ProviderCallBound == \A h \in Handlers: handlers[h].called => handlers[h].source = firstSource
CapturedBytesFrozen == digest = firstDigest
OwnedObject == blob # 0 => source # 0 /\ digest # 0 /\ blob = firstDigest
ReadyHasFrozenBytes == ready => ~failed /\ digest # 0 /\ blob = firstDigest /\ digest = firstDigest
=============================================================================
