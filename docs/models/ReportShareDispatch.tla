------------------------- MODULE ReportShareDispatch -------------------------
EXTENDS Naturals, FiniteSets

\* #169 focused obligation: two recipients, two explicit attempts each.
\* A private upload/preparation is not recipient delivery. The durable dispatch
\* boundary precedes the provider call; a lost reply never proves non-delivery.
\* The guard is one physical database connection, not a renewable time lease.
\* No fairness, dedup window, provider readback or eventual delivery assumption.
\* Organization authorization and exact artifact bytes are separate obligations.
CONSTANTS FaultyGuard, FaultyRetry
Targets == {"A", "B"}
Attempts == 1..2
Operations == Targets \X Attempts
VARIABLES phases, current, guards, committed, issued, rejected, delivered, unsafe
vars == <<phases, current, guards, committed, issued, rejected, delivered, unsafe>>

Init == /\ phases = [o \in Operations |-> IF o[2] = 1 THEN "queued" ELSE "unused"]
        /\ current = [t \in Targets |-> 1]
        /\ guards = [t \in Targets |-> 0]
        /\ committed = {} /\ issued = {} /\ rejected = {} /\ delivered = {}
        /\ unsafe = FALSE

Prepare(t) == LET o == <<t, current[t]>> IN
             /\ phases[o] = "queued" /\ guards[t] = 0
             /\ phases' = [phases EXCEPT ![o] = "preparing"]
             /\ guards' = [guards EXCEPT ![t] = current[t]]
             /\ UNCHANGED <<current, committed, issued, rejected, delivered, unsafe>>

LoseGuard(t) == /\ guards[t] # 0 /\ guards' = [guards EXCEPT ![t] = 0]
                /\ UNCHANGED <<phases, current, committed, issued, rejected, delivered, unsafe>>

Dispatch(o) == /\ phases[o] = "preparing"
               /\ (guards[o[1]] = o[2] \/ FaultyGuard)
               /\ phases' = [phases EXCEPT ![o] = "dispatching"]
               /\ committed' = committed \cup {o}
               /\ unsafe' = (unsafe \/ guards[o[1]] # o[2])
               /\ UNCHANGED <<current, guards, issued, rejected, delivered>>

Call(o) == /\ o \in committed \ issued /\ issued' = issued \cup {o}
           /\ UNCHANGED <<phases, current, guards, committed, rejected, delivered, unsafe>>

Deliver(o) == /\ o \in issued \ (rejected \cup delivered)
              /\ delivered' = delivered \cup {o}
              /\ UNCHANGED <<phases, current, guards, committed, issued, rejected, unsafe>>

Receipt(o) == /\ o \in delivered /\ phases[o] \in {"dispatching", "unknown"}
              /\ phases' = [phases EXCEPT ![o] = "sent"]
              /\ UNCHANGED <<current, guards, committed, issued, rejected, delivered, unsafe>>

Reject(o) == /\ o \in issued \ (delivered \cup rejected)
             /\ phases[o] = "dispatching"
             /\ rejected' = rejected \cup {o}
             /\ phases' = [phases EXCEPT ![o] = "failed"]
             /\ UNCHANGED <<current, guards, committed, issued, delivered, unsafe>>

ReadAfterLoss(t) == LET o == <<t, current[t]>> IN
                   /\ guards[t] = 0
                   /\ phases[o] \in {"queued", "preparing", "dispatching"}
                   /\ phases' = [phases EXCEPT ![o] = IF o \in committed THEN "unknown" ELSE "failed"]
                   /\ UNCHANGED <<current, guards, committed, issued, rejected, delivered, unsafe>>

Retry(t) == /\ current[t] = 1
            /\ (phases[<<t, 1>>] = "failed" \/ (FaultyRetry /\ phases[<<t, 1>>] = "unknown"))
            /\ guards[t] = 0
            /\ current' = [current EXCEPT ![t] = 2]
            /\ phases' = [phases EXCEPT ![<<t, 2>>] = "queued"]
            /\ UNCHANGED <<guards, committed, issued, rejected, delivered, unsafe>>

Next == (\E t \in Targets : Prepare(t) \/ LoseGuard(t) \/ ReadAfterLoss(t) \/ Retry(t))
        \/ (\E o \in Operations : Dispatch(o) \/ Call(o) \/ Deliver(o) \/ Receipt(o) \/ Reject(o))
Spec == Init /\ [][Next]_vars
TypeOK == /\ current \in [Targets -> Attempts]
          /\ guards \in [Targets -> 0..2]
          /\ phases \in [Operations -> {"unused", "queued", "preparing", "dispatching", "unknown", "failed", "sent"}]
          /\ delivered \subseteq issued /\ rejected \subseteq issued /\ issued \subseteq committed
          /\ unsafe \in BOOLEAN
GuardedDispatch == ~unsafe
NoDuplicateDelivery == \A t \in Targets : Cardinality({o \in delivered : o[1] = t}) <= 1
SentRequiresReceipt == \A o \in Operations : phases[o] = "sent" => o \in delivered
=============================================================================
