------------------------ MODULE FeishuTicketUpdates ------------------------
EXTENDS Naturals

\* #11: focused Safety obligation for one configured app, two event revisions,
\* and a foreign app. Delivery may repeat or arrive out of order. A lost ACK
\* repeats Deliver; a crash before commit stutters, and committed PostgreSQL
\* state survives restart. The guarded upsert is the single atomic boundary.
\* Token authentication, real timestamp parsing, DB durability and routing are
\* implementation/environment obligations, not proved by this finite model.
\* Provider timestamps order tickets; equal timestamps do not replace a value.
\* No delivery/liveness guarantee, fairness, constraints or symmetry is assumed.
\* Deadlock checking is off for open-system quiescence.

CONSTANT Fault
ASSUME Fault \in {"none", "order", "app"}
Events == {[app |-> "current", revision |-> 1],
           [app |-> "current", revision |-> 2],
           [app |-> "foreign", revision |-> 1]}
VARIABLES stored, highWater
vars == <<stored, highWater>>

Init == /\ stored = [app |-> "current", revision |-> 0]
        /\ highWater = 0

Deliver(e) ==
    LET authorized == e.app = "current"
        newer == e.revision > stored.revision
        accepted == (authorized \/ Fault = "app") /\ (newer \/ Fault = "order")
    IN /\ stored' = IF accepted THEN e ELSE stored
       /\ highWater' = IF authorized /\ e.revision > highWater THEN e.revision ELSE highWater

Next == \E e \in Events : Deliver(e)
Spec == Init /\ [][Next]_vars
AppBound == stored.app = "current"
LatestWins == stored.revision = highWater
=============================================================================
