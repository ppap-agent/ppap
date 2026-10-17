------------------------- MODULE NotificationDispatch -------------------------
EXTENDS Naturals, FiniteSets

\* #169: one durable notification intention, two competing workers/lease epochs.
\* Claim expiry is recoverable only BEFORE the durable dispatch boundary.
\* Dispatch is a database CAS, not an atomic transaction with the provider.
\* Issued calls can be delayed forever; a lost receipt is not confirmed absence.
\* No provider deduplication, fairness, eventual delivery or crash-free assumption.
\* Payload/recipient authorization, intent insertion and provider readback are
\* separate obligations; this is not a refinement proof of the current Worker.
CONSTANTS FaultyFence, FaultyRecovery
Workers == 1..2
VARIABLES phase, epoch, owner, tickets, issued, delivered, unauthorized
vars == <<phase, epoch, owner, tickets, issued, delivered, unauthorized>>

Init == /\ phase = "queued" /\ epoch = 0 /\ owner = 0
        /\ tickets = [w \in Workers |-> 0]
        /\ issued = {} /\ delivered = {} /\ unauthorized = FALSE

Claim(w) == /\ phase = "queued" /\ epoch < 2 /\ tickets[w] = 0
            /\ phase' = "claimed" /\ epoch' = epoch + 1 /\ owner' = w
            /\ tickets' = [tickets EXCEPT ![w] = epoch + 1]
            /\ UNCHANGED <<issued, delivered, unauthorized>>

ExpireBeforeDispatch == /\ phase = "claimed"
                        /\ phase' = "queued" /\ owner' = 0
                        /\ UNCHANGED <<epoch, tickets, issued, delivered, unauthorized>>

Dispatch(w) == /\ phase = "claimed" /\ tickets[w] > 0 /\ w \notin issued
               /\ LET current == owner = w /\ tickets[w] = epoch IN
                    /\ (current \/ FaultyFence)
                    /\ unauthorized' = (unauthorized \/ ~current)
               /\ phase' = "dispatching" /\ issued' = issued \cup {w}
               /\ UNCHANGED <<epoch, owner, tickets, delivered>>

Deliver(w) == /\ w \in issued \ delivered
              /\ delivered' = delivered \cup {w}
              /\ UNCHANGED <<phase, epoch, owner, tickets, issued, unauthorized>>

LoseReceipt == /\ phase = "dispatching" /\ phase' = "unknown"
               /\ UNCHANGED <<epoch, owner, tickets, issued, delivered, unauthorized>>

Observe(w) == /\ w \in delivered /\ phase \in {"dispatching", "unknown"}
              /\ phase' = "sent"
              /\ UNCHANGED <<epoch, owner, tickets, issued, delivered, unauthorized>>

BlindRecovery == /\ FaultyRecovery /\ phase \in {"dispatching", "unknown"}
                 /\ phase' = "queued" /\ owner' = 0
                 /\ UNCHANGED <<epoch, tickets, issued, delivered, unauthorized>>

Next == ExpireBeforeDispatch \/ LoseReceipt \/ BlindRecovery
        \/ (\E w \in Workers : Claim(w) \/ Dispatch(w) \/ Deliver(w) \/ Observe(w))
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in {"queued", "claimed", "dispatching", "unknown", "sent"}
          /\ epoch \in 0..2 /\ owner \in 0..2
          /\ tickets \in [Workers -> 0..2]
          /\ issued \subseteq Workers /\ delivered \subseteq issued
          /\ unauthorized \in BOOLEAN
DispatchRequiresCurrentOwner == ~unauthorized
AtMostOneDispatch == Cardinality(issued) <= 1
NoDuplicateDelivery == Cardinality(delivered) <= 1
SentRequiresReceipt == phase = "sent" => delivered # {}
=============================================================================
