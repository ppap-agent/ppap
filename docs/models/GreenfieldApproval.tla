------------------------- MODULE GreenfieldApproval -------------------------
EXTENDS Naturals, FiniteSets, Sequences

\* Requirements-level, machine-assisted single-case path. Values stand for
\* versions, not bytes. Independent human checking of unknown machine results
\* is scoped separately in GreenfieldManualReview, not proved by this model.
CONSTANTS MaxSub, MaxRev, FaultyStale, FaultyRewrite,
          FaultyApproval, FaultyResend, FaultyCoverage
ASSUME /\ MaxSub = 2
       /\ MaxRev = 2
       /\ FaultyStale \in BOOLEAN
       /\ FaultyRewrite \in BOOLEAN
       /\ FaultyApproval \in BOOLEAN
       /\ FaultyResend \in BOOLEAN
       /\ FaultyCoverage \in BOOLEAN

Submissions == 1..MaxSub
Results == {"pass", "blocked", "unknown", "failed"}
Reviews == {"cleared", "blocked"}

VARIABLE st
vars == <<st>>

Init ==
  st = [scopeRev      |-> 0,
        draftRev      |-> 0,
        submission    |-> 0,
        frozenReq     |-> [s \in Submissions |-> 0],
        frozenDoc     |-> [s \in Submissions |-> 0],
        baselineReq   |-> [s \in Submissions |-> 0],
        baselineDoc   |-> [s \in Submissions |-> 0],
        pending       |-> {},
        resultFor     |-> 0,
        result        |-> "none",
        covered       |-> FALSE,
        reviewFor     |-> 0,
        review        |-> "none",
        decisions     |-> <<>>,
        impactPending |-> FALSE,
        notice        |-> "none",
        deliveryCount |-> 0]

\* Only a customer may confirm or change the governing scope; that access
\* check is represented by the action boundary, not a login implementation.
SetScope ==
  /\ st.scopeRev = 0
  /\ st' = [st EXCEPT !.scopeRev = 1]

ChangeScope ==
  /\ st.scopeRev = 1
  /\ st.submission > 0
  /\ st' = [st EXCEPT !.scopeRev = 2,
                     !.impactPending = TRUE]

\* Upload changes only the supplier's draft, never a frozen submission.
Upload ==
  /\ st.scopeRev > 0
  /\ st.draftRev < MaxRev
  /\ st' = [st EXCEPT !.draftRev = @ + 1]

Submit ==
  /\ st.scopeRev > 0
  /\ st.draftRev > 0
  /\ st.submission < MaxSub
  /\ (IF st.submission = 0
         THEN TRUE
         ELSE st.draftRev # st.frozenDoc[st.submission] \/
              st.scopeRev # st.frozenReq[st.submission])
  /\ LET n == st.submission + 1 IN
       st' = [st EXCEPT !.submission = n,
                        !.frozenReq[n] = st.scopeRev,
                        !.frozenDoc[n] = st.draftRev,
                        !.baselineReq[n] = st.scopeRev,
                        !.baselineDoc[n] = st.draftRev,
                        !.pending = @ \cup {n},
                        !.resultFor = 0,
                        !.result = "none",
                        !.covered = FALSE,
                        !.reviewFor = 0,
                        !.review = "none",
                        !.impactPending = FALSE]

\* An old Worker completion may be archived, but must not become current.
Complete(s, r, coverage) ==
  /\ s \in st.pending
  /\ r \in Results
  /\ coverage \in BOOLEAN
  /\ IF s = st.submission \/ FaultyStale
        THEN st' = [st EXCEPT !.pending = @ \ {s},
                              !.resultFor = s,
                              !.result = IF r = "pass" /\ ~coverage
                                          THEN "unknown" ELSE r,
                              !.covered = coverage]
        ELSE st' = [st EXCEPT !.pending = @ \ {s}]

\* A local deadline is always available to terminate a pending attempt.
\* This does not claim a measured 20-minute wall-clock deadline.
AuditTimeout(s) ==
  /\ s \in st.pending
  /\ IF s = st.submission /\ st.resultFor # s
        THEN st' = [st EXCEPT !.pending = @ \ {s},
                              !.resultFor = s,
                              !.result = "failed",
                              !.covered = FALSE]
        ELSE st' = [st EXCEPT !.pending = @ \ {s}]

Review(r) ==
  /\ st.submission > 0
  /\ st.resultFor = st.submission
  /\ r \in Reviews
  /\ (r = "cleared" => st.result = "pass")
  /\ st' = [st EXCEPT !.reviewFor = st.submission,
                     !.review = r]

NoDecisionYet ==
  ~\E i \in 1..Len(st.decisions) : st.decisions[i].sid = st.submission

SignApproved ==
  /\ st.submission > 0
  /\ NoDecisionYet
  /\ st.submission \notin st.pending
  /\ st.scopeRev = st.frozenReq[st.submission]
  /\ ~st.impactPending
  /\ st.resultFor = st.submission
  /\ st.result = "pass"
  /\ st.covered
  /\ st.reviewFor = st.submission
  /\ st.review = "cleared"
  /\ st' = [st EXCEPT !.decisions = Append(@,
                     [sid |-> st.submission,
                      req |-> st.frozenReq[st.submission],
                      doc |-> st.frozenDoc[st.submission],
                      actor |-> "approver",
                      kind |-> "approved",
                      audit |-> st.result,
                      covered |-> st.covered,
                      humanReview |-> st.review])]

SignRejected ==
  /\ st.submission > 0
  /\ NoDecisionYet
  /\ st.submission \notin st.pending
  /\ st.scopeRev = st.frozenReq[st.submission]
  /\ ~st.impactPending
  /\ st.reviewFor = st.submission
  /\ st' = [st EXCEPT !.decisions = Append(@,
                     [sid |-> st.submission,
                      req |-> st.frozenReq[st.submission],
                      doc |-> st.frozenDoc[st.submission],
                      actor |-> "approver",
                      kind |-> "rejected",
                      audit |-> st.result,
                      covered |-> st.covered,
                      humanReview |-> st.review])]

\* One correction request; sending and delivery are separate effects.
PrepareNotice ==
  /\ st.review = "blocked"
  /\ st.notice = "none"
  /\ st' = [st EXCEPT !.notice = "prepared"]

StartSend ==
  /\ st.notice = "prepared"
  /\ st' = [st EXCEPT !.notice = "sending"]

EffectDelivered ==
  /\ st.notice = "sending"
  /\ st.deliveryCount = 0
  /\ st' = [st EXCEPT !.notice = "effectObserved",
                     !.deliveryCount = 1]

AckDelivered ==
  /\ st.notice = "effectObserved"
  /\ st' = [st EXCEPT !.notice = "delivered"]

LoseAck ==
  /\ st.notice \in {"sending", "effectObserved"}
  /\ st' = [st EXCEPT !.notice = "unknown"]

Reconcile ==
  /\ st.notice = "unknown"
  /\ IF st.deliveryCount = 1
        THEN st' = [st EXCEPT !.notice = "delivered"]
        ELSE st' = [st EXCEPT !.notice = "prepared"]

\* Mutations exist only in negative configurations. They establish that the
\* properties can distinguish each forbidden behavior from the correct one.
RewriteFrozen ==
  /\ FaultyRewrite
  /\ st.submission > 0
  /\ st' = [st EXCEPT !.frozenReq[1] = 2]

ApproveUnknown ==
  /\ FaultyApproval
  /\ st.submission > 0
  /\ NoDecisionYet
  /\ st.resultFor = st.submission
  /\ st.result = "unknown"
  /\ st' = [st EXCEPT !.decisions = Append(@,
                     [sid |-> st.submission,
                      req |-> st.frozenReq[st.submission],
                      doc |-> st.frozenDoc[st.submission],
                      actor |-> "worker",
                      kind |-> "approved",
                      audit |-> st.result,
                      covered |-> st.covered,
                      humanReview |-> "none"])]

CommitUncovered ==
  /\ FaultyCoverage
  /\ st.submission > 0
  /\ st.resultFor = st.submission
  /\ st.result = "unknown"
  /\ ~st.covered
  /\ st' = [st EXCEPT !.result = "pass"]

ResendWithoutReconcile ==
  /\ FaultyResend
  /\ st.notice = "unknown"
  /\ st.deliveryCount = 1
  /\ st' = [st EXCEPT !.deliveryCount = 2]

Next ==
  \/ SetScope
  \/ ChangeScope
  \/ Upload
  \/ Submit
  \/ \E s \in Submissions, r \in Results, c \in BOOLEAN : Complete(s, r, c)
  \/ \E s \in Submissions : AuditTimeout(s)
  \/ \E r \in Reviews : Review(r)
  \/ SignApproved
  \/ SignRejected
  \/ PrepareNotice
  \/ StartSend
  \/ EffectDelivered
  \/ AckDelivered
  \/ LoseAck
  \/ Reconcile
  \/ RewriteFrozen
  \/ ApproveUnknown
  \/ CommitUncovered
  \/ ResendWithoutReconcile

Spec == Init /\ [][Next]_vars /\
        (\A s \in Submissions : WF_vars(AuditTimeout(s)))

TypeOK ==
  /\ st.scopeRev \in 0..MaxRev
  /\ st.draftRev \in 0..MaxRev
  /\ st.submission \in 0..MaxSub
  /\ st.pending \subseteq Submissions
  /\ st.resultFor \in 0..MaxSub
  /\ st.result \in Results \cup {"none"}
  /\ st.covered \in BOOLEAN
  /\ st.reviewFor \in 0..MaxSub
  /\ st.review \in Reviews \cup {"none"}
  /\ st.notice \in {"none", "prepared", "sending", "effectObserved",
                    "unknown", "delivered"}
  /\ st.deliveryCount \in 0..2
  /\ Len(st.decisions) <= MaxSub

FrozenInputsStable ==
  \A s \in 1..st.submission :
    st.frozenReq[s] = st.baselineReq[s] /\
    st.frozenDoc[s] = st.baselineDoc[s] /\
    st.frozenReq[s] > 0 /\ st.frozenDoc[s] > 0

CurrentResultFresh ==
  st.resultFor = 0 \/ st.resultFor = st.submission

NoEmptyPass == st.result = "pass" => st.covered

DecisionSound ==
  \A i \in 1..Len(st.decisions) :
    LET d == st.decisions[i] IN
      /\ d.actor = "approver"
      /\ d.sid \in 1..st.submission
      /\ d.req = st.frozenReq[d.sid]
      /\ d.doc = st.frozenDoc[d.sid]
      /\ (d.kind = "approved" =>
            d.audit = "pass" /\ d.covered /\ d.humanReview = "cleared")

NoDuplicateDelivery == st.deliveryCount <= 1

AuditEventuallyStops ==
  \A s \in Submissions : (s \in st.pending) ~> (s \notin st.pending)

=============================================================================
