----------------------- MODULE ReportPackageAcceptance -----------------------
EXTENDS Naturals
CONSTANTS FaultDuplicate, FaultRebind
Requests == {"r1", "r2"}
Collections == {"ab", "ac"}
VARIABLES stored, first, work
vars == <<stored, first, work>>

\* Focused #169 first-ZIP acceptance obligation, not a system refinement.
\* Collections abstract canonical full report/artifact sets; ordering is not
\* identity. Two requests distinguish aliases; two sets expose request rebinding.
\* Request receipt and first queue insertion are one transaction. Repeated or
\* reply-lost requests can re-enter Submit, but cannot create another work item.
\* A failed/ready work is retained under its collection identity, not rebuilt.
\* Worker fencing, current authorization, bytes, I/O completion, cleanup and
\* policy for explicit retries are outside this focused model. No fairness.
Init == /\ stored = [r \in Requests |-> "none"]
        /\ first = [r \in Requests |-> "none"]
        /\ work = [c \in Collections |-> 0]
Submit(r,c) == /\ stored[r] \in {"none", c} \/ FaultRebind
               /\ stored' = [stored EXCEPT ![r] = c]
               /\ first' = [first EXCEPT ![r] = IF @ = "none" THEN c ELSE @]
               /\ work' = [work EXCEPT ![c] = IF @ = 0 THEN 1
                         ELSE IF FaultDuplicate THEN 2 ELSE @]
Next == \E r \in Requests, c \in Collections : Submit(r,c)
Spec == Init /\ [][Next]_vars
TypeOK == /\ stored \in [Requests -> Collections \cup {"none"}]
          /\ first \in [Requests -> Collections \cup {"none"}]
          /\ work \in [Collections -> 0..2]
BindingStable == \A r \in Requests : stored[r] = first[r]
OneWorkPerCollection == \A c \in Collections : work[c] <= 1
ReferencesHaveWork == \A r \in Requests : stored[r] # "none" => work[stored[r]] > 0
=============================================================================
