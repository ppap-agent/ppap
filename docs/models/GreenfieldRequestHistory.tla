--------------------- MODULE GreenfieldRequestHistory ---------------------
EXTENDS Naturals, FiniteSets

\* Focused obligation: one clause, two versions of its request, one other
\* independent request, and replies to each exact request identity.
\* User actions are atomic in this single-tab prototype. Replies may arrive
\* before or after revision/withdrawal; their original target never changes.
\* No claim about semantic judgments, storage atomicity, concurrent tabs,
\* real sending, unbounded history, authorization, or eventual user action.
CONSTANTS RetargetReply, ResolveOnWithdraw, RewriteOriginal
Ids == {1, 2, 3}
Original == [r \in Ids |-> r]
VARIABLES issued, active, content, replies, satisfied, verified
vars == <<issued, active, content, replies, satisfied, verified>>
Init == /\ issued = {1} /\ active = {1} /\ content = Original
        /\ replies = {} /\ satisfied = FALSE /\ verified = FALSE
Append == /\ 3 \notin issued /\ issued' = issued \cup {3}
          /\ active' = active \cup {3}
          /\ UNCHANGED <<content, replies, satisfied, verified>>
Revise == /\ 1 \in active /\ 2 \notin issued
          /\ issued' = issued \cup {2} /\ active' = (active \ {1}) \cup {2}
          /\ content' = IF RewriteOriginal THEN [content EXCEPT ![1] = 2] ELSE content
          /\ UNCHANGED <<replies, satisfied, verified>>
Withdraw(r) == /\ r \in active /\ active' = active \ {r}
               /\ satisfied' = IF ResolveOnWithdraw THEN TRUE ELSE satisfied
               /\ UNCHANGED <<issued, content, replies, verified>>
Receive(r) == /\ r \in issued
              /\ replies' = replies \cup {[origin |-> r,
                   target |-> IF RetargetReply /\ r = 1 /\ 2 \in issued THEN 2 ELSE r]}
              /\ satisfied' = FALSE /\ verified' = FALSE
              /\ UNCHANGED <<issued, active, content>>
Judge == /\ satisfied' = TRUE /\ verified' = TRUE
         /\ UNCHANGED <<issued, active, content, replies>>
Next == Append \/ Revise \/ Judge
        \/ (\E r \in Ids: Withdraw(r) \/ Receive(r))
Spec == Init /\ [][Next]_vars
ReplyKeepsTarget == \A reply \in replies: reply.origin = reply.target
OriginalRequestUnchanged == \A r \in issued: content[r] = Original[r]
NoAdministrativePass == satisfied => verified
=============================================================================
