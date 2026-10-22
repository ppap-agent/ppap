----------------------- MODULE DeepGatewayRevocation -----------------------
EXTENDS Naturals

(* Focused #156 obligation, not a full runtime refinement. One revoked
   execution and one distinct waiting execution preserve the observed slot
   contention. Provider headers/data may never arrive: no fairness is imposed
   on the provider. Local interruption abstracts socket shutdown at the known
   headers/body read boundary, then handler cleanup separately releases the
   inference slot. Native socket tests supply that scoped OS fact; this model
   does not prove TLS/DNS connection setup, provider cancellation/billing,
   recovery, lease replacement, kernel isolation, or elapsed-time bounds. *)
CONSTANT RevokeNeedsProviderData
VARIABLES phase, active, revoked, ownsSlot, providerData, nextStarted
vars == <<phase, active, revoked, ownsSlot, providerData, nextStarted>>

Init == /\ phase = "idle" /\ active = TRUE /\ revoked = FALSE
        /\ ownsSlot = FALSE /\ providerData = FALSE /\ nextStarted = FALSE

Authorize == /\ phase = "idle" /\ active /\ ~nextStarted
             /\ phase' = "reserved" /\ ownsSlot' = TRUE
             /\ UNCHANGED <<active, revoked, providerData, nextStarted>>
Dispatch == /\ phase = "reserved" /\ active
            /\ phase' = "headers"
            /\ UNCHANGED <<active, revoked, ownsSlot, providerData, nextStarted>>
Headers == /\ phase = "headers" /\ active /\ providerData
           /\ phase' = "body" /\ providerData' = FALSE
           /\ UNCHANGED <<active, revoked, ownsSlot, nextStarted>>
BodyData == /\ phase = "body" /\ active /\ providerData
            /\ providerData' = FALSE
            /\ UNCHANGED <<phase, active, revoked, ownsSlot, nextStarted>>
ProviderData == /\ phase \in {"headers", "body"} /\ ~providerData
                /\ providerData' = TRUE
                /\ UNCHANGED <<phase, active, revoked, ownsSlot, nextStarted>>
Revoke == /\ active /\ active' = FALSE /\ revoked' = TRUE
          /\ UNCHANGED <<phase, ownsSlot, providerData, nextStarted>>
Interrupt == /\ revoked /\ phase \in {"headers", "body"}
             /\ (~RevokeNeedsProviderData \/ providerData)
             /\ phase' = "finishing"
             /\ UNCHANGED <<active, revoked, ownsSlot, providerData, nextStarted>>
CancelReserved == /\ revoked /\ phase = "reserved"
                  /\ phase' = "finishing"
                  /\ UNCHANGED <<active, revoked, ownsSlot, providerData, nextStarted>>
Cleanup == /\ phase = "finishing" /\ ownsSlot
           /\ phase' = "closed" /\ ownsSlot' = FALSE
           /\ UNCHANGED <<active, revoked, providerData, nextStarted>>
NextExecution == /\ revoked /\ ~ownsSlot /\ ~nextStarted
                 /\ nextStarted' = TRUE
                 /\ UNCHANGED <<phase, active, revoked, ownsSlot, providerData>>

Next == Authorize \/ Dispatch \/ Headers \/ BodyData \/ ProviderData \/ Revoke
        \/ Interrupt \/ CancelReserved \/ Cleanup \/ NextExecution
Spec == Init /\ [][Next]_vars /\ WF_vars(Interrupt) /\ WF_vars(CancelReserved)
        /\ WF_vars(Cleanup) /\ WF_vars(NextExecution)

TypeOK == /\ phase \in {"idle", "reserved", "headers", "body", "finishing", "closed"}
          /\ active \in BOOLEAN /\ revoked \in BOOLEAN /\ ownsSlot \in BOOLEAN
          /\ providerData \in BOOLEAN /\ nextStarted \in BOOLEAN
NoConcurrentInference == ~(ownsSlot /\ nextStarted)
CancellationIndependent == (revoked /\ phase \in {"headers", "body"})
                           => ENABLED Interrupt
RevocationReleasesSlot == (revoked /\ ownsSlot) ~> ~ownsSlot
NextExecutionProceeds == revoked ~> nextStarted
=============================================================================
