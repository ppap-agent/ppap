----------------------- MODULE TenantCustomerDefaults -----------------------
EXTENDS Naturals, FiniteSets
\* #231: focused finite Safety obligation, not an implementation refinement.
\* Three member commands: two peers in tenant 1 and one member in tenant 2.
\* Invocation captures expected revision; atomic commit is the linearization
\* point. Completion may be lost forever; readback does not write. No fairness
\* or liveness claim. Authentication, SQL durability, task acceptance, actual
\* customer strings and browser rendering are outside this abstraction.
CONSTANT Fault
ASSUME Fault \in {"none", "stale", "tenant", "history"}
Tenants == {1, 2}
Commands == {1, 2, 3}
Owner(c) == IF c = 3 THEN 2 ELSE 1
Value(c) == IF c = 2 THEN "nio" ELSE "uaes"
VARIABLES defaults, revision, phase, expected, writes, snapshots, born
vars == <<defaults, revision, phase, expected, writes, snapshots, born>>
Init == /\ defaults = [t \in Tenants |-> "auto"]
        /\ revision = [t \in Tenants |-> 0]
        /\ phase = [c \in Commands |-> "idle"]
        /\ expected = [c \in Commands |-> 0]
        /\ writes = {}
        /\ snapshots = [t \in Tenants |-> "absent"]
        /\ born = [t \in Tenants |-> "absent"]
Invoke(c) == /\ phase[c] = "idle"
             /\ phase' = [phase EXCEPT ![c] = "pending"]
             /\ expected' = [expected EXCEPT ![c] = revision[Owner(c)]]
             /\ UNCHANGED <<defaults, revision, writes, snapshots, born>>
Commit(c) ==
    LET target == IF Fault = "tenant" THEN 3 - Owner(c) ELSE Owner(c)
    IN /\ phase[c] = "pending"
       /\ phase' = [phase EXCEPT ![c] = "unknown"]
       /\ IF revision[target] = expected[c] \/ Fault = "stale"
          THEN /\ defaults' = [defaults EXCEPT ![target] = Value(c)]
               /\ revision' = [revision EXCEPT ![target] = @ + 1]
               /\ writes' = writes \cup {[command |-> c, owner |-> Owner(c),
                    tenant |-> target, expected |-> expected[c]]}
               /\ snapshots' = IF Fault = "history" /\ snapshots[target] # "absent"
                               THEN [snapshots EXCEPT ![target] = Value(c)]
                               ELSE snapshots
          ELSE UNCHANGED <<defaults, revision, writes, snapshots>>
       /\ UNCHANGED <<expected, born>>
Readback(c) == /\ phase[c] = "unknown"
               /\ phase' = [phase EXCEPT ![c] = "read"]
               /\ UNCHANGED <<defaults, revision, expected, writes, snapshots, born>>
NewTask(t) == /\ snapshots[t] = "absent"
              /\ snapshots' = [snapshots EXCEPT ![t] = defaults[t]]
              /\ born' = [born EXCEPT ![t] = defaults[t]]
              /\ UNCHANGED <<defaults, revision, phase, expected, writes>>
Next == (\E c \in Commands : Invoke(c) \/ Commit(c) \/ Readback(c))
        \/ (\E t \in Tenants : NewTask(t))
Spec == Init /\ [][Next]_vars
NoLostUpdate == \A t \in Tenants, r \in 0..2 :
    Cardinality({w \in writes : w.tenant = t /\ w.expected = r}) <= 1
TenantBound == \A w \in writes : w.tenant = w.owner
FrozenTasks == snapshots = born
=============================================================================
