---------------------- MODULE AuditReportShareDrafts ----------------------
EXTENDS Naturals, TLC
CONSTANT Fault
VARIABLES open, scope, draft, intent, compose, directory, organization,
          eligible, cached, job, operations, unsafeSend
vars == <<open, scope, draft, intent, compose, directory, organization,
          eligible, cached, job, operations, unsafeSend>>

(* Focused #169 P7 UI obligation, not a Feishu delivery or persistence proof.
   Two report scopes and organizations distinguish stale context; two targets
   distinguish user edits. Two operations permit a completed share followed
   by a new draft or an observed external request. Draft intent is a history
   projection changed only by explicit edits, scope replacement or an observed
   organization change. Closing must not erase it. Directory completion and
   request observation are separate; either may never arrive. No fairness or
   liveness claim. Actual sending must recheck authorization at the service.
   Faults expose lost drafts, stale eligibility and hidden unknown requests.
   Compose abstracts edit/result presentation; the implementation binds a new
   draft to the preceding request ID. This is not a trace refinement proof.
   Exact request/report identities and late-directory epoch rejection remain
   DTO/controlled-browser obligations, not claims of these invariants. One
   selected recipient abstracts the per-target gate; collection uniqueness
   and multi-recipient conjunction remain DTO/browser obligations. *)
Default(s) == [scope |-> s, target |-> 0, organization |-> 0, mode |-> "link"]
VisibleJob == IF compose /\ (job # "unknown" \/ Fault = "hide")
              THEN "none" ELSE job
Fresh == directory = "ready" /\ draft.target \in eligible
         /\ draft.organization = organization

Init == /\ open = FALSE /\ scope = 1 /\ draft = Default(1) /\ intent = draft
        /\ compose = TRUE /\ directory = "unknown" /\ organization = 0
        /\ eligible = {} /\ cached = FALSE /\ job = "none"
        /\ operations = 0 /\ unsafeSend = FALSE

Open == /\ ~open /\ open' = TRUE
        /\ draft' = IF draft.scope = scope THEN draft ELSE Default(scope)
        /\ intent' = IF draft.scope = scope THEN intent ELSE Default(scope)
        /\ compose' = IF draft.scope = scope THEN compose ELSE job = "none"
        /\ directory' = "loading" /\ organization' = 0 /\ eligible' = {}
        /\ UNCHANGED <<scope, cached, job, operations, unsafeSend>>
Close == /\ open /\ open' = FALSE /\ directory' = "unknown"
         /\ eligible' = {}
         /\ draft' = IF Fault = "drop" THEN Default(scope) ELSE draft
         /\ UNCHANGED <<scope, intent, compose, organization, cached,
                         job, operations, unsafeSend>>
ChooseScope == /\ ~open /\ scope' = 3 - scope
               /\ UNCHANGED <<open, draft, intent, compose, directory,
                    organization, eligible, cached, job,
                    operations, unsafeSend>>
DirectoryReply(org, seen, allowed) ==
  /\ open /\ VisibleJob = "none" /\ directory = "loading"
  /\ organization \in {0, org} /\ allowed \subseteq seen
  /\ directory' = "ready" /\ organization' = org
  /\ eligible' = (eligible \ seen) \cup allowed
  /\ LET changed == draft.target # 0 /\ draft.organization # org
         next == IF changed THEN [draft EXCEPT !.target = 0,
                                               !.organization = 0] ELSE draft
     IN /\ draft' = next /\ intent' = IF changed THEN next ELSE intent
        /\ cached' = (cached \/ next.target \in eligible')
  /\ UNCHANGED <<open, scope, compose, job, operations, unsafeSend>>
DirectoryFailure == /\ open /\ VisibleJob = "none" /\ directory = "loading"
                    /\ directory' = "error"
                    /\ UNCHANGED <<open, scope, draft, intent, compose,
                         organization, eligible, cached, job, operations, unsafeSend>>
ReadDirectory == /\ open /\ VisibleJob = "none"
                 /\ directory \in {"ready", "error"}
                 /\ directory' = "loading"
                 /\ UNCHANGED <<open, scope, draft, intent, compose,
                      organization, eligible, cached, job, operations, unsafeSend>>
ChooseTarget(t) == /\ open /\ VisibleJob = "none" /\ directory = "ready"
                   /\ t \in eligible
                   /\ draft' = [draft EXCEPT !.target = t,
                                             !.organization = organization]
                   /\ intent' = draft' /\ cached' = TRUE
                   /\ UNCHANGED <<open, scope, compose, directory, organization,
                                  eligible, job, operations, unsafeSend>>
ModeEdit(m) == /\ open /\ VisibleJob = "none"
               /\ draft' = [draft EXCEPT !.mode = m] /\ intent' = draft'
               /\ UNCHANGED <<open, scope, compose, directory, organization,
                              eligible, cached, job, operations, unsafeSend>>
RemoveTarget == /\ open /\ VisibleJob = "none" /\ draft.target # 0
                /\ draft' = [draft EXCEPT !.target = 0, !.organization = 0]
                /\ intent' = draft'
                /\ UNCHANGED <<open, scope, compose, directory, organization,
                               eligible, cached, job, operations, unsafeSend>>
Send == /\ open /\ VisibleJob = "none" /\ job # "unknown"
        /\ draft.target # 0 /\ operations < 2
        /\ (Fresh \/ (Fault = "stale" /\ cached))
        /\ unsafeSend' = (unsafeSend \/ ~Fresh \/ draft.scope # scope)
        /\ job' = "unknown"
        /\ operations' = operations + 1 /\ compose' = FALSE
        /\ UNCHANGED <<open, scope, draft, intent, directory,
                         organization, eligible, cached>>
ObserveUnknown == /\ job # "unknown" /\ operations < 2
                     /\ job' = "unknown"
                     /\ operations' = operations + 1
                     /\ UNCHANGED <<open, scope, draft, intent, compose,
                          directory, organization, eligible, cached, unsafeSend>>
ObserveSent == /\ job = "unknown" /\ job' = "sent"
               /\ UNCHANGED <<open, scope, draft, intent, compose, directory,
                    organization, eligible, cached, operations, unsafeSend>>
NewShare == /\ open /\ VisibleJob = "sent"
            /\ draft' = Default(scope) /\ intent' = draft' /\ compose' = TRUE
            /\ cached' = FALSE /\ directory' = "loading"
            /\ organization' = 0 /\ eligible' = {}
            /\ UNCHANGED <<open, scope, job, operations, unsafeSend>>
Next == Open \/ Close \/ ChooseScope \/ ReadDirectory \/ DirectoryFailure
     \/ Send \/ ObserveUnknown \/ ObserveSent \/ NewShare \/ RemoveTarget
     \/ (\E org \in 1..2, seen \in SUBSET (1..2), allowed \in SUBSET (1..2) :
           DirectoryReply(org, seen, allowed))
     \/ (\E t \in 1..2 : ChooseTarget(t))
     \/ (\E m \in {"link", "file"} : ModeEdit(m))
Spec == Init /\ [][Next]_vars
DraftRetained == draft = intent
FreshTargetsOnly == ~unsafeSend
UnknownRemainsVisible == (open /\ job = "unknown") => VisibleJob = "unknown"
DraftScopeMatches == (open /\ VisibleJob = "none") => draft.scope = scope
=============================================================================
