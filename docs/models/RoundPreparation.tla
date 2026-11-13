------------------------ MODULE RoundPreparation ---------------------------
EXTENDS Integers, FiniteSets

\* Focused obligation for #165 and DATA_MODEL: preparation is not acceptance.
\* All correct preparation steps preserve the
\* accepted count. No full refinement or liveness claim is made.
\* One operation, two request identities (the second exposes an unsafe retry),
\* and three navigation epochs (leave and return are not the original caller).
\* The DB transaction atomically saves the supplied authorized frozen inputs;
\* SQL authorization, payload conflicts, bytes and billing are checked by tests,
\* not modeled here. Same-ID replay deduplication is an explicit DB assumption.
\* Sending may fail before commit or lose the committed reply. Reads can fail
\* or deny access; only explicit fixed-identity replay can then send again.
\* The old operation may leave a private draft after navigation. Recovery by a
\* new caller is outside this one-operation check; controlled browser tests
\* cover reading that retained draft. No compensation or cleanup is claimed.
CONSTANTS FreshIdentity, IgnoreEpoch, StartOnPrepare
VARIABLES phase, epoch, owner, intent, stored, reply, visibleAt, accepted
vars == <<phase, epoch, owner, intent, stored, reply, visibleAt, accepted>>

Init == /\ phase = "ready" /\ epoch = 0 /\ owner = 0
        /\ intent = 0 /\ stored = {} /\ reply = 0
        /\ visibleAt = -1 /\ accepted = 0

Begin == /\ phase = "ready" /\ epoch = 0
         /\ intent' = 1 /\ owner' = epoch /\ phase' = "sending"
         /\ UNCHANGED <<epoch, stored, reply, visibleAt, accepted>>
CommitDB == /\ phase = "sending"
            /\ stored' = stored \cup {intent}
            /\ reply' = intent /\ phase' = "reply"
            /\ accepted' = IF StartOnPrepare THEN 1 ELSE accepted
            /\ UNCHANGED <<epoch, owner, intent, visibleAt>>
FailBeforeCommit == /\ phase = "sending" /\ phase' = "unknown"
                    /\ UNCHANGED <<epoch, owner, intent, stored, reply,
                                   visibleAt, accepted>>
LoseReply == /\ phase = "reply" /\ phase' = "unknown" /\ reply' = 0
             /\ UNCHANGED <<epoch, owner, intent, stored, visibleAt, accepted>>
ReadFound == /\ phase = "unknown" /\ intent \in stored
             /\ phase' = "reply" /\ reply' = intent
             /\ UNCHANGED <<epoch, owner, intent, stored, visibleAt, accepted>>
ReadDenied == /\ phase = "unknown" /\ phase' = "replay"
              /\ UNCHANGED <<epoch, owner, intent, stored, reply,
                             visibleAt, accepted>>
ReadFailed == /\ phase = "unknown"
              /\ UNCHANGED vars
Replay == /\ phase = "replay" /\ epoch = owner
          /\ intent' = IF FreshIdentity THEN 2 ELSE intent
          /\ phase' = "sending"
          /\ UNCHANGED <<epoch, owner, stored, reply, visibleAt, accepted>>
Navigate == /\ epoch < 2 /\ epoch' = epoch + 1 /\ visibleAt' = -1
            /\ UNCHANGED <<phase, owner, intent, stored, reply, accepted>>
OpenDraft == /\ phase = "reply" /\ reply = intent /\ reply \in stored
             /\ (epoch = owner \/ IgnoreEpoch)
             /\ phase' = "done" /\ visibleAt' = owner
             /\ UNCHANGED <<epoch, owner, intent, stored, reply, accepted>>

Next == Begin \/ CommitDB \/ FailBeforeCommit \/ LoseReply \/ ReadFound
        \/ ReadDenied \/ ReadFailed \/ Replay \/ Navigate \/ OpenDraft
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in {"ready", "sending", "reply", "unknown", "replay", "done"}
          /\ epoch \in 0..2 /\ owner \in 0..2 /\ intent \in 0..2
          /\ stored \subseteq 1..2 /\ reply \in 0..2
          /\ visibleAt \in -1..2 /\ accepted \in 0..1
OnePreparedDraft == Cardinality(stored) <= 1
NoLateNavigation == visibleAt = -1 \/ visibleAt = epoch
NoImplicitAcceptance == accepted = 0
=============================================================================
